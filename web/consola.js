/* Consola clínica Vela — lógica de interfaz.
 *
 * Todo lo clínico que se muestra viene del backend, que a su vez lo toma del motor
 * determinista. Esta capa no decide nada: pinta lo que el sistema puede sustentar.
 * La rúbrica contrasta lo que se ve contra los logs, así que la interfaz no inventa
 * ni interpola valores.
 */
'use strict';

const $ = (id) => document.getElementById(id);
// El tema vive en <html>: <body> es ancestro del contenedor y no heredaría las
// variables si el atributo estuviera más abajo.
const raiz = document.documentElement;

let llamadaId = null;
let grabadora = null;
let trozos = [];
let stream = null;
let ctxAudio = null;
let analizador = null;
let relojT = null;
let segundos = 0;
let rafBarras = null;

/* ── Tema ─────────────────────────────────────────────────────────────────── */
/* Claro por defecto: la consola se usa en salas iluminadas y se proyecta. */
$('tema').onclick = () => {
  const claro = raiz.dataset.theme !== 'dark';
  raiz.dataset.theme = claro ? 'dark' : 'light';
  $('tema').textContent = claro ? 'Modo claro' : 'Modo oscuro';
};

/* ── Navegación ───────────────────────────────────────────────────────────── */
$('navCall').onclick = () => mostrar('call');
$('navKb').onclick = () => { mostrar('kb'); cargarDocumentos(); };

function mostrar(vista) {
  const esLlamada = vista === 'call';
  $('vistaCall').classList.toggle('hide', !esLlamada);
  $('vistaKb').classList.toggle('hide', esLlamada);
  $('navCall').classList.toggle('on', esLlamada);
  $('navKb').classList.toggle('on', !esLlamada);
}

/* ── Barras de audio ──────────────────────────────────────────────────────── */
function construirBarras() {
  const cont = $('barras');
  if (cont.childElementCount) return;
  for (let i = 0; i < 34; i++) {
    const b = document.createElement('div');
    b.style.cssText = 'width:3px;border-radius:1px;background:var(--line-2);height:3px';
    cont.appendChild(b);
  }
}

function animarBarras() {
  rafBarras = requestAnimationFrame(animarBarras);
  const hijos = $('barras').children;
  if (!hijos.length) return;
  if (!analizador) {
    for (const h of hijos) { h.style.height = '3px'; h.style.background = 'var(--line-2)'; }
    return;
  }
  const datos = new Uint8Array(analizador.frequencyBinCount);
  analizador.getByteFrequencyData(datos);
  const paso = Math.floor(datos.length * 0.55 / hijos.length);
  for (let i = 0; i < hijos.length; i++) {
    const v = datos[i * paso] / 255;
    hijos[i].style.height = Math.max(3, Math.round(v * 32)) + 'px';
    hijos[i].style.background = v > 0.06 ? 'var(--accent)' : 'var(--line-2)';
  }
}

/* ── Salud del servicio ───────────────────────────────────────────────────── */
async function comprobarSalud() {
  try {
    const r = await fetch('/api/salud');
    const d = await r.json();
    const listo = d.indice && d.sintetizador && d.modelo_disponible && d.transcripcion_lista;
    // Se nombra QUÉ falta. "Servicio incompleto" no le sirve a nadie a mitad de
    // una demostración; "falta GROQ_API_KEY" se arregla en veinte segundos.
    $('salud').textContent = listo
      ? `${d.fragmentos.toLocaleString('es')} fragmentos · ${d.modelo}`
      : !d.transcripcion_lista
        ? `sin transcripción · falta ${d.faltantes.join(', ')}`
        : !d.modelo_disponible
          ? 'modelo local no responde · ¿está corriendo ollama?'
          : 'servicio incompleto';
    $('dotSalud').style.background = listo ? 'var(--ok)' : 'var(--crit)';
    $('mModelo').textContent = `${d.modelo} · local`;
    // Sin transcripción la llamada no puede avanzar: se bloquea antes de empezar.
    if (!d.transcripcion_lista) {
      $('btnIniciar').disabled = true;
      $('btnIniciar').style.filter = 'grayscale(1)';
      $('btnIniciar').title = `Falta ${d.faltantes.join(', ')} en el archivo .env`;
    }
  } catch {
    $('salud').textContent = 'sin conexión';
    $('dotSalud').style.background = 'var(--crit)';
  }
}

/* ── Llamada ──────────────────────────────────────────────────────────────── */
$('btnIniciar').onclick = async () => {
  $('btnIniciar').disabled = true;
  $('btnIniciar').textContent = 'Iniciando…';
  try {
    const r = await fetch('/api/llamada', { method: 'POST' });
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json();
    llamadaId = d.llamada_id;

    $('btnIniciar').classList.add('hide');
    $('btnHablar').classList.remove('hide');
    $('btnColgar').classList.remove('hide');
    $('trVacio').remove();

    segundos = 0;
    relojT = setInterval(() => {
      segundos++;
      $('reloj').textContent =
        String(Math.floor(segundos / 60)).padStart(2, '0') + ':' + String(segundos % 60).padStart(2, '0');
    }, 1000);

    await abrirMicrofono();
    pintar(d);
    reproducir(d.audio_wav_base64);
  } catch (e) {
    alert('No se pudo iniciar la llamada: ' + e.message);
    $('btnIniciar').disabled = false;
    $('btnIniciar').textContent = 'Iniciar llamada';
  }
};

async function abrirMicrofono() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    ctxAudio = new (window.AudioContext || window.webkitAudioContext)();
    const fuente = ctxAudio.createMediaStreamSource(stream);
    analizador = ctxAudio.createAnalyser();
    analizador.fftSize = 256;
    analizador.smoothingTimeConstant = 0.72;
    fuente.connect(analizador);
    $('notaAudio').textContent = 'Micrófono en vivo';
    estado('Escuchando', 'Turno del paciente', 'var(--accent)');
  } catch {
    $('notaAudio').textContent = 'Sin acceso al micrófono';
    estado('Sin micrófono', 'Permiso denegado', 'var(--crit)');
  }
}

/* Pulsar para hablar: evita cortar al paciente a mitad de frase, que es el
 * problema clásico de la detección automática de fin de turno. */
const iniciarGrabacion = () => {
  if (!stream || grabadora) return;
  trozos = [];
  grabadora = new MediaRecorder(stream);
  grabadora.ondataavailable = (e) => e.data.size && trozos.push(e.data);
  grabadora.onstop = enviarTurno;
  grabadora.start();
  $('btnHablar').classList.add('rec');
  $('btnHablar').textContent = 'Grabando… soltá para enviar';
};

const detenerGrabacion = () => {
  if (!grabadora) return;
  grabadora.stop();
  grabadora = null;
  $('btnHablar').classList.remove('rec');
  $('btnHablar').textContent = 'Mantené presionado para hablar';
};

$('btnHablar').addEventListener('mousedown', iniciarGrabacion);
$('btnHablar').addEventListener('mouseup', detenerGrabacion);
$('btnHablar').addEventListener('mouseleave', detenerGrabacion);
$('btnHablar').addEventListener('touchstart', (e) => { e.preventDefault(); iniciarGrabacion(); });
$('btnHablar').addEventListener('touchend', (e) => { e.preventDefault(); detenerGrabacion(); });

async function enviarTurno() {
  if (!trozos.length || !llamadaId) return;
  estado('Procesando', 'Transcribiendo y decidiendo', 'var(--warn)');

  const forma = new FormData();
  forma.append('audio', new Blob(trozos, { type: 'audio/webm' }), 'turno.webm');
  try {
    const r = await fetch(`/api/llamada/${llamadaId}/turno`, { method: 'POST', body: forma });
    if (!r.ok) {
      const detalle = await r.json().catch(() => ({ detail: r.statusText }));
      const mensaje = detalle.detail || `error ${r.status}`;
      // 422 es del audio y se reintenta hablando de nuevo. Cualquier otro código
      // es del servicio: se muestra en rojo y en la transcripción, porque un
      // fallo silencioso hace creer que la conversación simplemente no avanza.
      if (r.status === 422) {
        estado('Escuchando', mensaje, 'var(--accent)');
      } else {
        estado('Error del servicio', mensaje, 'var(--crit)');
        avisoEnLlamada(mensaje);
      }
      return;
    }
    const d = await r.json();
    pintar(d);
    reproducir(d.audio_wav_base64);
    if (d.finalizada) finalizar();
  } catch (e) {
    estado('Error del servicio', e.message, 'var(--crit)');
    avisoEnLlamada(e.message);
  }
}

/* Un fallo del servicio se escribe en la transcripción, donde el operador está
 * mirando. Dejarlo solo en la etiqueta de estado hacía que el problema pasara
 * inadvertido: se oía el saludo y después nada. */
function avisoEnLlamada(mensaje) {
  const aviso = document.createElement('div');
  aviso.className = 'dcin';
  aviso.style.cssText =
    'margin-bottom:12px;max-width:760px;border:1px solid var(--crit);background:var(--crit-bg);border-radius:var(--r2);padding:11px 13px';
  aviso.innerHTML =
    `<div style="font-size:13px;font-weight:700;color:var(--crit-t)">El turno no se pudo procesar</div>
     <div style="margin-top:5px;font-size:12.5px;color:var(--text)">${esc(mensaje)}</div>`;
  $('tr').appendChild(aviso);
  $('tr').scrollTop = $('tr').scrollHeight;
}

$('btnColgar').onclick = async () => {
  if (!llamadaId) return;
  $('btnColgar').disabled = true;
  try {
    const r = await fetch(`/api/llamada/${llamadaId}/colgar`, { method: 'POST' });
    pintar(await r.json());
  } finally {
    finalizar();
  }
};

function finalizar() {
  clearInterval(relojT);
  if (stream) stream.getTracks().forEach((t) => t.stop());
  if (ctxAudio) ctxAudio.close();
  stream = null; ctxAudio = null; analizador = null;
  $('btnHablar').classList.add('hide');
  $('btnColgar').classList.add('hide');
  $('btnIniciar').classList.remove('hide');
  $('btnIniciar').disabled = false;
  $('btnIniciar').textContent = 'Nueva llamada';
  $('notaAudio').textContent = 'Audio inactivo';
  estado('Llamada finalizada', 'Sesión cerrada', 'var(--text-3)');
  llamadaId = null;
}

function estado(texto, nota, color) {
  $('estadoAgente').textContent = texto;
  $('notaEstado').textContent = nota;
  $('dotEstado').style.background = color;
}

function reproducir(b64) {
  if (!b64) return;
  estado('Hablando', 'Reproduciendo respuesta', 'var(--ok)');
  const audio = new Audio('data:audio/wav;base64,' + b64);
  audio.onended = () => {
    if (llamadaId) estado('Escuchando', 'Turno del paciente', 'var(--accent)');
  };
  audio.play().catch(() => {});
}

/* ── Pintado del estado clínico ───────────────────────────────────────────── */
const COLORES = {
  verde:    { b: 'var(--ok)',   t: 'var(--ok-t)',   f: 'var(--ok-bg)',   n: 'Verde · Estable' },
  amarillo: { b: 'var(--warn)', t: 'var(--warn-t)', f: 'var(--warn-bg)', n: 'Amarillo · Vigilar' },
  rojo:     { b: 'var(--crit)', t: 'var(--crit-t)', f: 'var(--crit-bg)', n: 'Rojo · Escalar ahora' },
};

function pintar(d) {
  const c = COLORES[d.semaforo] || COLORES.verde;
  const caja = $('riesgo');
  caja.style.borderColor = c.b;
  caja.style.background = c.f;
  $('riesgoPunto').style.background = c.b;
  $('riesgoPunto').style.boxShadow = '0 0 0 4px ' + c.f;
  $('riesgoTitulo').textContent = c.n;
  $('riesgoTitulo').style.color = c.t;
  $('riesgoMotivo').textContent = d.motivos.join(' · ');

  // Un verde con campos sin preguntar no es un alta: es información faltante.
  const indagar = $('riesgoIndagar');
  if (d.requiere_indagar && d.faltantes.length) {
    indagar.textContent = 'Falta preguntar: ' + d.faltantes.join(', ');
    indagar.classList.remove('hide');
  } else {
    indagar.classList.add('hide');
  }

  // Hallazgos
  const cont = $('hallazgos');
  $('nHallazgos').textContent = d.hallazgos.length ? d.hallazgos.length + ' activos' : '0';
  if (!d.hallazgos.length) {
    cont.innerHTML = '<div style="border:1px dashed var(--line-2);border-radius:var(--r2);padding:14px;text-align:center" class="mono lbl">Sin extracciones aún</div>';
  } else {
    cont.innerHTML = d.hallazgos.map((h) => `
      <div class="dcin" style="display:flex;align-items:center;gap:9px;padding:7px 9px;background:var(--panel-2);border:1px solid var(--line);border-radius:var(--r1)">
        <span style="width:5px;height:14px;border-radius:1px;background:${h.critico ? 'var(--crit)' : 'var(--line-2)'};flex:none"></span>
        <span style="flex:1;min-width:0;font-size:12.5px">${esc(h.nombre)}</span>
        ${h.critico ? '<span class="mono" style="font-size:9px;letter-spacing:.07em;text-transform:uppercase;color:var(--crit-t);border:1px solid var(--crit);padding:1px 5px;border-radius:2px">Alerta</span>' : ''}
        <span class="mono" style="font-size:10px;color:var(--text-3)">${esc(h.detalle)}</span>
      </div>`).join('');
  }

  // Compuerta: se muestra QUÉ CAPA bloqueó, no un puntaje contra un umbral.
  // Se midió que un umbral solo no separa lo respondible de lo ausente.
  const gate = $('gate');
  if (!d.grounding) {
    gate.innerHTML = '';
  } else if (d.grounding.permitido) {
    gate.innerHTML = `<div style="display:flex;align-items:center;gap:7px;padding:6px 9px;border:1px solid var(--ok);background:var(--ok-bg);border-radius:var(--r1);margin-bottom:8px">
      <span style="font-size:11.5px;font-weight:600;color:var(--ok-t)">Grounding suficiente</span>
      <span style="flex:1"></span>
      <span class="mono" style="font-size:10.5px;color:var(--text-2)">${d.citas.length} fragmento(s)</span></div>`;
  } else {
    gate.innerHTML = `<div class="dcin" style="border:1px solid var(--warn);background:var(--warn-bg);border-radius:var(--r1);padding:9px 10px;margin-bottom:8px">
      <div style="font-size:11.5px;font-weight:700;color:var(--warn-t)">Compuerta bloqueada — el agente respondió “no lo sé”</div>
      <div class="mono" style="margin-top:6px;font-size:10.5px;color:var(--text-2);line-height:1.6">
        Capa que bloqueó: ${esc(d.grounding.capa)}<br>${esc(d.grounding.motivo)}</div></div>`;
  }

  // Citas
  const citas = $('citas');
  if (!d.citas.length) {
    citas.innerHTML = '<div style="border:1px dashed var(--line-2);border-radius:var(--r2);padding:14px;text-align:center" class="mono lbl">Sin citas en el turno actual</div>';
  } else {
    citas.innerHTML = d.citas.map((c) => `
      <div class="dcin" style="border:1px solid var(--line);background:var(--panel-2);border-radius:var(--r1);padding:8px 9px">
        <div style="display:flex;align-items:center;gap:8px">
          <span class="mono" style="font-size:10.5px;color:var(--accent-t)">${esc(c.id)}</span>
          <span style="flex:1"></span>
          <span class="mono" style="font-size:10.5px;color:var(--text-2);font-variant-numeric:tabular-nums">${c.puntaje}</span>
          <span style="width:34px;height:3px;background:var(--line-2);border-radius:2px;overflow:hidden;display:block">
            <span style="display:block;height:100%;background:var(--accent);width:${Math.round(c.puntaje * 100)}%"></span></span>
        </div>
        <div style="margin-top:5px;font-size:11.5px;color:var(--text-2);line-height:1.45">${esc(c.fragmento)}</div>
        <div class="mono" style="margin-top:5px;font-size:9.5px;color:var(--text-3)">${esc(c.fuente)}</div>
      </div>`).join('');
  }

  // Escalación: el disparador y la regla salen de los motivos del motor.
  if (d.semaforo === 'rojo') {
    $('banEsc').classList.remove('hide');
    $('escDisp').textContent = d.motivos.join(' · ');
    $('escRegla').textContent = reglaDe(d.motivos);
    $('escHora').textContent = new Date().toLocaleTimeString('es-CO');
  }

  // Transcripción
  const tr = $('tr');
  tr.innerHTML = d.transcripcion.map((t) => {
    const paciente = t.quien === 'paciente';
    return `<div class="dcin" style="margin-bottom:12px;max-width:760px">
      <div style="border-left:2px solid ${paciente ? 'var(--accent)' : 'var(--line-2)'};padding-left:11px">
        <div style="display:flex;align-items:center;gap:9px;margin-bottom:4px">
          <span class="mono" style="font-size:9.5px;letter-spacing:.09em;text-transform:uppercase;font-weight:600;color:${paciente ? 'var(--accent-t)' : 'var(--text-2)'}">${paciente ? 'Paciente' : 'Agente'}</span>
        </div>
        <p style="margin:0;font-size:14px;line-height:1.5;color:${paciente ? 'var(--text)' : 'var(--text-2)'}">${esc(t.texto)}</p>
      </div></div>`;
  }).join('');
  tr.scrollTop = tr.scrollHeight;

  // Métricas
  const m = d.metricas;
  $('mUlt').textContent = d.latencia_ms ? Math.round(d.latencia_ms) + ' ms' : '—';
  $('mP50').textContent = m.latencia_p50_ms ? Math.round(m.latencia_p50_ms) + ' ms' : '—';
  $('mP95').textContent = m.latencia_p95_ms ? Math.round(m.latencia_p95_ms) + ' ms' : '—';
  $('mTok').textContent = (m.tokens_entrada + m.tokens_salida) || '—';
  $('mCalls').textContent = m.llamadas_modelo;
  $('mRag').textContent = m.consultas_rag;
}

/* Traduce los motivos del motor a la regla que se cumplió. Los umbrales son los
 * mismos que están en src/clinico/escalamiento.py — si cambian allá, cambian acá. */
function reglaDe(motivos) {
  const texto = motivos.join(' ');
  const partes = [];
  if (texto.includes('fiebre')) partes.push('fiebre ≥ 38.0 °C');
  if (texto.includes('purulenta')) partes.push('secreción purulenta');
  if (texto.includes('dolor')) partes.push('dolor ≥ 8/10');
  if (texto.includes('síntoma de alarma')) partes.push('síntoma de alarma');
  // Disyunción, no conjunción: cualquiera de estas condiciones escala sola.
  return partes.length ? partes.join('  ∨  ') : '—';
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ── Consola de conocimiento ──────────────────────────────────────────────── */
async function cargarDocumentos() {
  try {
    const d = await (await fetch('/api/documentos')).json();
    $('kbDocs').textContent = d.total_documentos.toLocaleString('es');
    $('kbChunks').textContent = d.total_fragmentos.toLocaleString('es');
    $('kbPend').textContent = d.en_proceso;
    $('motor').textContent =
      `Detalle técnico: ${d.motor.base_vectorial} · ${d.motor.embeddings} · ` +
      `${d.motor.dimensiones} dimensiones · fragmentos de ${d.motor.fragmento_chars} caracteres`;

    $('docs').innerHTML = d.documentos.length
      ? d.documentos.map(fila).join('')
      : `<div style="padding:44px 20px;text-align:center">
           <div style="font-size:12.5px;color:var(--text-2)">Índice vacío</div>
           <div class="mono lbl" style="margin-top:6px">Sin conocimiento cargado, el agente responderá “no lo sé” a toda consulta clínica</div>
         </div>`;

    document.querySelectorAll('[data-borrar]').forEach((b) => {
      b.onclick = () => borrar(b.dataset.borrar, b.dataset.nombre);
    });
  } catch (e) {
    $('docs').innerHTML = `<div style="padding:24px;text-align:center;color:var(--crit-t)">No se pudo cargar el índice: ${esc(e.message)}</div>`;
  }
}

const PILLS = {
  indexado:   ['var(--ok)', 'var(--ok-bg)', 'var(--ok-t)', 'Indexado'],
  extrayendo: ['var(--accent)', 'var(--accent-bg)', 'var(--accent-t)', 'Extrayendo'],
  embeddings: ['var(--accent)', 'var(--accent-bg)', 'var(--accent-t)', 'Embeddings'],
  error:      ['var(--crit)', 'var(--crit-bg)', 'var(--crit-t)', 'Error'],
};

function fila(d) {
  const [borde, fondo, texto, etiqueta] = PILLS[d.estado] || PILLS.indexado;
  const listo = d.estado === 'indexado';
  return `<div class="row">
    <div style="min-width:0">
      <div style="font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(d.nombre)}</div>
      <div class="mono" style="margin-top:3px;font-size:11px;color:var(--text-2)">${esc(d.doc_id)} · ${esc(d.escenario)}</div>
    </div>
    <div style="min-width:0">
      <span class="pill" style="border:1px solid ${borde};background:${fondo};color:${texto}">${etiqueta}</span>
      <div style="margin-top:5px;font-size:11.5px;color:var(--text-2)">${esc(d.nota)}</div>
    </div>
    <div class="mono" style="text-align:right;font-size:13.5px;font-variant-numeric:tabular-nums">${d.fragmentos || '—'}</div>
    <div style="text-align:right">
      ${listo ? `<button class="mono" data-borrar="${esc(d.doc_id)}" data-nombre="${esc(d.nombre)}" style="padding:4px 9px;border:1px solid var(--line);background:var(--panel-2);color:var(--text-2);border-radius:var(--r1);font-size:10px;letter-spacing:.04em;text-transform:uppercase">Eliminar</button>` : ''}
    </div>
  </div>`;
}

async function borrar(docId, nombre) {
  if (!confirm(`¿Eliminar “${nombre}” del conocimiento del agente?\n\nEl agente dejará de poder citarlo de inmediato.`)) return;
  try {
    const r = await fetch('/api/documentos/' + docId, { method: 'DELETE' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'no se pudo eliminar');
    avisar('ok', `Retirado del índice: ${nombre}`, `${d.fragmentos_eliminados} fragmentos eliminados`);
    cargarDocumentos();
  } catch (e) {
    avisar('error', 'No se pudo eliminar', e.message);
  }
}

$('file').onchange = (e) => { subir(Array.from(e.target.files)); e.target.value = ''; };
$('drop').addEventListener('dragover', (e) => { e.preventDefault(); $('drop').style.borderColor = 'var(--accent)'; });
$('drop').addEventListener('dragleave', () => { $('drop').style.borderColor = 'var(--line-2)'; });
$('drop').addEventListener('drop', (e) => {
  e.preventDefault();
  $('drop').style.borderColor = 'var(--line-2)';
  subir(Array.from(e.dataTransfer.files));
});

async function subir(archivos) {
  for (const archivo of archivos) {
    avisar('proceso', `Procesando ${archivo.name}`, 'extrayendo texto y vectorizando…');
    const forma = new FormData();
    forma.append('archivo', archivo);
    try {
      const r = await fetch('/api/documentos', { method: 'POST', body: forma });
      const d = await r.json();
      if (!r.ok) { avisar('error', archivo.name, d.nota || 'no se pudo procesar'); continue; }
      const aviso = d.sospechoso ? ' · contiene patrones de instrucción, neutralizados' : '';
      avisar('ok', `Indexado: ${archivo.name}`, `${d.fragmentos} fragmentos disponibles${aviso}`);
    } catch (e) {
      avisar('error', archivo.name, e.message);
    }
    cargarDocumentos();
  }
}

let toastT = null;
function avisar(tipo, titulo, detalle) {
  const t = $('toast');
  const c = tipo === 'error'
    ? ['var(--crit)', 'var(--crit-bg)', 'var(--crit-t)']
    : tipo === 'proceso'
      ? ['var(--accent)', 'var(--accent-bg)', 'var(--accent-t)']
      : ['var(--ok)', 'var(--ok-bg)', 'var(--ok-t)'];
  t.style.border = '1px solid ' + c[0];
  t.style.background = c[1];
  t.innerHTML = `<span style="font-size:12.5px;color:${c[2]};font-weight:600">${esc(titulo)}</span>
                 <span class="mono" style="font-size:11px;color:var(--text-2)">${esc(detalle)}</span>`;
  t.classList.remove('hide');
  clearTimeout(toastT);
  if (tipo !== 'proceso') toastT = setTimeout(() => t.classList.add('hide'), 6000);
}

/* ── Arranque ─────────────────────────────────────────────────────────────── */
construirBarras();
animarBarras();
comprobarSalud();
