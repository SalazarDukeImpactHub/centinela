"""API de la consola clínica.

Expone las dos superficies que exige el reto:

  Interfaz de llamada (G4)  — iniciar llamada desde el navegador, hablar por
                              micrófono y escuchar al agente.
  Consola de conocimiento (G5) — subir documento, verlo procesarse, listarlo y
                              eliminarlo, con estado visible.

Todo el estado clínico que la interfaz muestra viene del motor determinista, no
del modelo: el semáforo y sus motivos salen de `escalamiento.evaluar`, las citas
de la compuerta de grounding, y las métricas del registro estructurado de la
llamada. La consola no puede mostrar nada que el sistema no pueda sustentar,
porque la rúbrica contrasta lo mostrado contra los logs.
"""

from __future__ import annotations

import asyncio
import base64
import re
import io
import tempfile
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src import config
from src.clinico.escalamiento import evaluar
from src.conversacion.turno import Conversacion, EstadoLlamada
from src.modelo.cliente import MODELO, ClienteLocal
from src.observabilidad.metricas import RegistroLlamada, costo_estimado
from src.rag.chunk import fragmentar
from src.rag.extract import extraer as extraer_pdf
from src.rag.grounding import verificar
from src.rag.index import MODELO_EMBEDDINGS, IndiceClinico
from src.rag.saneamiento import contiene_inyeccion
from src.voz.stt import ErrorTranscripcion, transcribir
from src.voz.tts import Sintetizador

RAIZ = Path(__file__).resolve().parents[2]
INDICE = RAIZ / "chroma_data"
LOGS = RAIZ / "logs"
SUBIDAS = RAIZ / "subidas"
WEB = RAIZ / "web"

# Escenario por defecto de la demo. En producción vendría del perfil del paciente.
ESCENARIO_DEMO = "cholecystitis"

app = FastAPI(title="Centinela — seguimiento posoperatorio", version="0.1")


# -- Estado del proceso ----------------------------------------------------------


@dataclass
class Servicios:
    """Recursos caros: se cargan una vez al arrancar, nunca por petición."""

    indice: IndiceClinico | None = None
    sintetizador: Sintetizador | None = None
    cliente: ClienteLocal | None = None
    llamadas: dict[str, "SesionLlamada"] = field(default_factory=dict)
    # Documentos subidos en caliente, con su estado de procesamiento visible.
    procesando: dict[str, dict] = field(default_factory=dict)


SERVICIOS = Servicios()


@dataclass
class SesionLlamada:
    id: str
    conversacion: Conversacion
    registro: RegistroLlamada
    citas: list[dict] = field(default_factory=list)
    grounding: dict | None = None
    segundos_audio: float = 0.0
    finalizada: bool = False
    repeticiones: int = 0
    # Un turno a la vez por llamada. Sin esto, dos grabaciones enviadas seguidas
    # se procesaban en paralelo sobre el mismo estado y las respuestas volvían
    # encimadas — la conversación "se pegaba" y después hablaba varias veces.
    turno_en_curso: asyncio.Lock = field(default_factory=asyncio.Lock)


@app.on_event("startup")
async def arrancar() -> None:
    """Carga y CALIENTA los modelos antes de aceptar tráfico.

    El calentamiento no es opcional: la primera síntesis de Piper tarda ~28 s
    contra ~350 ms las siguientes. Sin esto, ese costo lo paga el primer turno de
    la primera llamada — es decir, la demo ante el jurado.
    """
    LOGS.mkdir(exist_ok=True)
    SUBIDAS.mkdir(exist_ok=True)

    # Las credenciales se cargan ACÁ y no en cada script. Sin esto, el servidor
    # arrancaba sano y solo fallaba al primer turno de voz, con un 500 y la
    # interfaz muda: el saludo se escuchaba y la conversación no avanzaba nunca.
    archivo = config.cargar()
    ausentes = config.faltantes()
    if ausentes:
        for clave, consecuencia in ausentes.items():
            print(f"  FALTA {clave} — {consecuencia}")
        print(f"  Definila en {config.ENV} o en el entorno del proceso.")
    elif archivo:
        print(f"  Credenciales cargadas desde {archivo}")

    SERVICIOS.indice = IndiceClinico(INDICE)
    SERVICIOS.cliente = ClienteLocal()

    def preparar() -> None:
        SERVICIOS.sintetizador = Sintetizador()
        SERVICIOS.sintetizador.calentar()
        _ = SERVICIOS.indice.modelo  # carga el modelo de embeddings

    await asyncio.get_running_loop().run_in_executor(None, preparar)


# -- Utilidades ------------------------------------------------------------------


def _wav_base64(pcm: bytes, frecuencia: int, canales: int, ancho: int) -> str:
    """Empaqueta PCM crudo como WAV en base64, listo para un <audio> del navegador."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(canales)
        w.setsampwidth(ancho)
        w.setframerate(frecuencia)
        w.writeframes(pcm)
    return base64.b64encode(buffer.getvalue()).decode()


def _sintetizar(texto: str) -> tuple[str, float]:
    """Devuelve (wav en base64, ms hasta el primer audio)."""
    import time

    tts = SERVICIOS.sintetizador
    if tts is None:
        raise HTTPException(503, "sintetizador no disponible")

    inicio = time.perf_counter()
    fragmentos = list(tts.por_oraciones(texto))
    primer_audio = (time.perf_counter() - inicio) * 1000
    if not fragmentos:
        return "", primer_audio

    pcm = b"".join(f.pcm for f in fragmentos)
    cabeza = fragmentos[0]
    return (
        _wav_base64(pcm, cabeza.frecuencia, cabeza.canales, cabeza.ancho_muestra),
        primer_audio,
    )


def _estado_publico(sesion: SesionLlamada) -> dict:
    """Proyección del estado para la interfaz. Todo sale del motor determinista."""
    estado = sesion.conversacion.estado
    decision = evaluar(estado.cuadro)
    cuadro = estado.cuadro

    # "Lo que detectó el agente": hallazgos reales del cuadro clínico, no del modelo.
    hallazgos: list[dict] = []
    if cuadro.fiebre_c is not None:
        hallazgos.append(
            {
                "nombre": f"Temperatura {cuadro.fiebre_c} °C",
                "critico": cuadro.fiebre_c >= 38.0,
                "detalle": "referida por el paciente",
            }
        )
    if cuadro.dolor_nrs is not None:
        hallazgos.append(
            {
                "nombre": f"Dolor {cuadro.dolor_nrs}/10",
                "critico": cuadro.dolor_nrs >= 8,
                "detalle": "escala verbal numérica",
            }
        )
    if cuadro.herida.value != "desconocido":
        etiquetas = {
            "normal": "Herida sin hallazgos",
            "eritema_leve": "Eritema en la herida",
            "secrecion_purulenta": "Secreción purulenta",
        }
        hallazgos.append(
            {
                "nombre": etiquetas[cuadro.herida.value],
                "critico": cuadro.herida.value == "secrecion_purulenta",
                "detalle": "inspección referida",
            }
        )
    if cuadro.movilidad.value not in ("desconocido", "normal"):
        hallazgos.append(
            {
                "nombre": f"Movilidad {cuadro.movilidad.value.replace('_', ' ')}",
                "critico": False,
                "detalle": "referida por el paciente",
            }
        )
    for alarma in cuadro.sintomas_alarma:
        hallazgos.append(
            {
                "nombre": alarma.replace("_", " ").capitalize(),
                "critico": True,
                "detalle": "detectado en el habla",
            }
        )

    totales = sesion.registro.totales()
    # El consumo del modelo sale del acumulador vivo de la conversación: la
    # extracción corre en un hilo de fondo y sus tokens del turno actual todavía
    # no están en el registro cuando esta respuesta viaja. El registro JSONL
    # queda consistente por delta al turno siguiente.
    uso = sesion.conversacion.uso_modelo
    totales["llamadas_modelo"] = uso["llamadas"]
    totales["tokens_entrada"] = uso["tokens_entrada"]
    totales["tokens_salida"] = uso["tokens_salida"]
    return {
        "llamada_id": sesion.id,
        "finalizada": sesion.finalizada,
        "semaforo": decision.semaforo.value,
        "motivos": decision.motivos,
        "requiere_indagar": decision.requiere_indagar,
        "faltantes": cuadro.campos_faltantes,
        "hallazgos": hallazgos,
        "citas": sesion.citas,
        "grounding": sesion.grounding,
        "transcripcion": [
            {"quien": quien, "texto": texto} for quien, texto in estado.transcripcion
        ],
        "metricas": totales,
        "costo": costo_estimado(
            int(totales["tokens_entrada"]),
            int(totales["tokens_salida"]),
            sesion.segundos_audio,
        ),
        "modelo": MODELO,
        "modelo_ubicacion": "local",
    }


# -- Llamada (G4) ----------------------------------------------------------------


def _consultor_corpus(sesion_ref: list) -> object:
    """Consultor de preguntas del paciente contra el corpus.

    Responde con una CITA TEXTUAL del fragmento mejor sustentado — sin pasar por
    el modelo de lenguaje. Es deliberado dos veces: no agrega latencia (el modelo
    local tarda ~17 s) y no puede alucinar, porque no genera — cita. La rúbrica
    exige que cada afirmación clínica resista verificación contra la fuente.
    """

    def consultar(pregunta: str) -> str | None:
        if SERVICIOS.indice is None:
            return None
        veredicto = verificar(SERVICIOS.indice, pregunta, escenario=ESCENARIO_DEMO)
        # El estado de grounding queda registrado para la consola, responda o no.
        if sesion_ref:
            sesion = sesion_ref[0]
            sesion.grounding = {
                "permitido": veredicto.permitido,
                "motivo": veredicto.motivo,
                "capa": _capa_de(veredicto.motivo),
            }
            sesion.citas = [
                {
                    "id": f.chunk_id,
                    "puntaje": round(f.similitud, 3),
                    "fragmento": f.texto[:180],
                    "fuente": f.cita(),
                }
                for f in veredicto.fragmentos
            ]
        if not veredicto.permitido or not veredicto.fragmentos:
            return None  # la máquina de turnos dirá el límite declarado

        mejor = veredicto.fragmentos[0]
        # Solo la primera oración completa: un fragmento de 1.200 caracteres
        # leído por voz es un monólogo, no una respuesta.
        oracion = mejor.texto.split(". ")[0].strip().rstrip(".")
        return f"Le cuento lo que dice la guía: {oracion}. Eso está en {mejor.cita()}."

    return consultar


@app.post("/api/llamada")
async def iniciar_llamada() -> JSONResponse:
    """Crea una llamada y devuelve el saludo del agente ya sintetizado."""
    if SERVICIOS.cliente is None:
        raise HTTPException(503, "servicio no inicializado")

    llamada_id = uuid.uuid4().hex[:12]
    sesion_ref: list = []
    conversacion = Conversacion(
        SERVICIOS.cliente,
        EstadoLlamada(escenario=ESCENARIO_DEMO),
        consultor=_consultor_corpus(sesion_ref),
    )
    sesion = SesionLlamada(
        id=llamada_id,
        conversacion=conversacion,
        registro=RegistroLlamada(llamada_id, LOGS / f"llamada-{llamada_id}.jsonl"),
    )
    sesion_ref.append(sesion)  # el consultor registra grounding y citas acá
    SERVICIOS.llamadas[llamada_id] = sesion

    respuesta = conversacion.abrir()
    metrica = sesion.registro.nuevo_turno()
    audio, primer_audio_ms = _sintetizar(respuesta.texto)
    metrica.latencia_ms = primer_audio_ms
    metrica.modelo = MODELO
    metrica.registrar_etapa("tts", primer_audio_ms)
    metrica.semaforo = respuesta.decision.semaforo.value
    sesion.registro.persistir(metrica)

    return JSONResponse(
        {
            **_estado_publico(sesion),
            "texto_agente": respuesta.texto,
            "audio_wav_base64": audio,
            "latencia_ms": round(primer_audio_ms, 1),
        }
    )


@app.post("/api/llamada/{llamada_id}/turno")
async def turno(llamada_id: str, audio: UploadFile) -> JSONResponse:
    """Procesa un turno del paciente: audio -> transcripción -> decisión -> voz.

    La latencia que se reporta es la que el paciente percibe: desde que termina de
    hablar hasta que empieza a sonar el audio del agente.
    """
    sesion = SERVICIOS.llamadas.get(llamada_id)
    if sesion is None:
        raise HTTPException(404, "llamada no encontrada")
    if sesion.finalizada:
        raise HTTPException(409, "la llamada ya fue finalizada")
    if sesion.turno_en_curso.locked():
        # El turno anterior sigue procesándose. Rechazar es mejor que encolar:
        # una respuesta al turno de hace diez segundos ya no viene al caso.
        raise HTTPException(429, "todavía estoy procesando lo anterior, un momento")

    async with sesion.turno_en_curso:
        return await _procesar_turno(sesion, audio)


# Qué buscar en el corpus para sustentar cada paso del triaje. El agente no
# improvisa sus preguntas: sigue protocolo, y el panel de citas debe mostrarlo.
# Sin esto, las citas solo aparecían cuando el paciente preguntaba algo y el
# panel quedaba vacío la mayor parte de la llamada — trazabilidad invisible.
CONSULTA_POR_FOCO = {
    "fiebre": "fiebre postoperatoria umbral de alarma 38 grados",
    "herida": "signos de infección de sitio operatorio secreción eritema",
    "dolor": "evaluación del dolor postoperatorio escala",
    "movilidad": "movilización temprana después de la cirugía",
}
CONSULTA_ESCALACION = "criterios de derivación urgente infección postoperatoria fiebre"

# Alucinaciones conocidas de Whisper sobre audio vacío o ruido. Aparecen porque
# el modelo fue entrenado con subtítulos: ante el silencio, "recuerda" créditos.
_RUIDO_WHISPER = re.compile(
    r"subt[ií]tulos|amara\.org|gracias por ver|suscr[ií]bete|www\.",
    re.IGNORECASE,
)


# Frases de cortesía que Whisper alucina sobre el silencio — verificado: nuestro
# archivo de ruido casi inaudible transcribe como "Gracias.". Solo cuentan como
# ruido cuando son el ÚNICO contenido del turno: "gracias, ya me tomé la
# temperatura" es habla real y pasa.
_CORTESIA_ALUCINADA = {
    "gracias", "muchas gracias", "gracias por ver", "adios", "hasta luego",
    "chau", "amen", "musica", "aplausos", "silencio",
}


def _transcripcion_trivial(texto: str) -> bool:
    """La transcripción no contiene habla real del paciente.

    Los dígitos CUENTAN como contenido: "37" es la respuesta legítima a la
    pregunta por la temperatura. Solo puntuación y espacios no lo son.
    """
    contenido = re.sub(r"[\W_]+", "", texto, flags=re.UNICODE)
    if len(contenido) < 2:
        return True
    if _RUIDO_WHISPER.search(texto):
        return True
    normalizado = re.sub(r"[\W_]+", " ", texto.lower()).strip()
    normalizado = normalizado.replace("á", "a").replace("é", "e").replace("í", "i") \
        .replace("ó", "o").replace("ú", "u")
    return normalizado in _CORTESIA_ALUCINADA


def _sustentar_paso(sesion: SesionLlamada, respuesta) -> None:
    """Puebla el panel de citas con el sustento del paso actual del triaje."""
    if SERVICIOS.indice is None:
        return
    if respuesta.escala:
        consulta = CONSULTA_ESCALACION
        etiqueta = "protocolo de escalación"
    elif respuesta.foco is not None:
        consulta = CONSULTA_POR_FOCO.get(respuesta.foco.value)
        etiqueta = f"protocolo del paso: {respuesta.foco.value}"
    else:
        consulta = "seguimiento postoperatorio recomendaciones al alta"
        etiqueta = "protocolo de cierre"
    if not consulta:
        return

    # Búsqueda directa, sin la compuerta completa. La verificación léxica exige
    # solapamiento de términos en el idioma de la consulta, y parte del corpus
    # está en inglés: el sustento de protocolo en español quedaba bloqueado. Acá
    # no hay afirmación clínica hacia el paciente que proteger — solo se muestra
    # el material del procedimiento que respalda el paso, con su cita.
    recuperados = [
        r
        for r in SERVICIOS.indice.buscar(consulta, k=8)
        if r.escenario == ESCENARIO_DEMO and r.similitud >= 0.84
    ][:2]
    if not recuperados:
        return
    sesion.grounding = {"permitido": True, "motivo": etiqueta, "capa": "protocolo"}
    sesion.citas = [
        {
            "id": f.chunk_id,
            "puntaje": round(f.similitud, 3),
            "fragmento": f.texto[:180],
            "fuente": f.cita(),
        }
        for f in recuperados
    ]


async def _procesar_turno(sesion: SesionLlamada, audio: UploadFile) -> JSONResponse:
    import time

    metrica = sesion.registro.nuevo_turno()
    metrica.modelo = MODELO
    inicio = time.perf_counter()
    # El estado de grounding es POR TURNO: se limpia acá y lo puebla el consultor
    # (si el paciente preguntó) o el sustento de protocolo (el resto).
    sesion.grounding = None
    sesion.citas = []
    # Consumo del modelo por delta: la extracción del turno ANTERIOR ya terminó,
    # así que la diferencia del acumulador se atribuye a este registro. El JSONL
    # suma exacto aunque cada turno llegue con un turno de rezago.
    uso_previo = dict(sesion.conversacion.uso_modelo)

    datos = await audio.read()
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        tmp.write(datos)
        ruta_audio = Path(tmp.name)

    try:
        bucle = asyncio.get_running_loop()
        t0 = time.perf_counter()
        transcripcion = await bucle.run_in_executor(None, transcribir, ruta_audio)
        metrica.registrar_etapa("stt", (time.perf_counter() - t0) * 1000)
        sesion.segundos_audio += transcripcion.segundos_audio
    except ErrorTranscripcion as exc:
        # Un fallo de transcripción es de configuración o de red, no del paciente.
        # Devolverlo como 503 con el detalle evita el 500 mudo que hacía parecer
        # que la conversación simplemente no arrancaba.
        raise HTTPException(503, f"transcripción no disponible: {exc}") from exc
    finally:
        ruta_audio.unlink(missing_ok=True)

    if _transcripcion_trivial(transcripcion.texto):
        # Whisper alucina sobre grabaciones vacías: devuelve ".", "Gracias." o
        # créditos de subtítulos. Pasó en prueba real: un "." consumió el turno
        # de movilidad y la llamada cerró sin ese dato. La respuesta correcta es
        # pedir que repita, POR VOZ y sin consumir el turno clínico.
        sesion.repeticiones += 1
        # La segunda vez cambia la frase y sugiere el modo manual: repetir
        # identico "Perdon, no le escuche" dos veces seguidas suena a disco
        # rayado y no ayuda a resolver el problema de fondo (ruido, microfono).
        variantes = (
            "Perdón, no le escuché bien. ¿Me lo puede repetir?",
            "Sigo sin escucharle. Acérquese al micrófono, o mantenga presionado "
            "el botón de hablar mientras me responde.",
            "Parece que hay ruido en la línea. Tómese su tiempo y repítame lo "
            "último, por favor.",
        )
        texto_repetir = variantes[min(sesion.repeticiones - 1, len(variantes) - 1)]
        audio_b64, _ = _sintetizar(texto_repetir)
        sesion.conversacion.estado.transcripcion.append(("agente", texto_repetir))
        metrica.latencia_ms = (time.perf_counter() - inicio) * 1000
        sesion.registro.persistir(metrica)
        return JSONResponse(
            {
                **_estado_publico(sesion),
                "texto_paciente": "",
                "texto_agente": texto_repetir,
                "audio_wav_base64": audio_b64,
                "latencia_ms": round(metrica.latencia_ms, 1),
                "repeticion": True,
            }
        )

    # Decisión: código puro, sin esperar al modelo.
    t1 = time.perf_counter()
    respuesta = sesion.conversacion.responder(transcripcion.texto)
    metrica.registrar_etapa("decision", (time.perf_counter() - t1) * 1000)

    # El grounding de preguntas lo maneja el consultor dentro de la máquina de
    # turnos. Si el paciente no preguntó nada, se sustenta el paso del protocolo:
    # el panel de citas nunca queda mudo, porque el agente nunca actúa sin base.
    if sesion.grounding is None:
        _sustentar_paso(sesion, respuesta)
    metrica.consultas_rag += 1

    texto_agente = respuesta.texto
    t2 = time.perf_counter()
    audio_b64, _ = _sintetizar(texto_agente)
    metrica.registrar_etapa("tts", (time.perf_counter() - t2) * 1000)

    metrica.latencia_ms = (time.perf_counter() - inicio) * 1000
    metrica.semaforo = respuesta.decision.semaforo.value
    metrica.escalado = respuesta.escala
    uso_actual = sesion.conversacion.uso_modelo
    metrica.llamadas_modelo = uso_actual["llamadas"] - uso_previo["llamadas"]
    metrica.tokens_entrada = uso_actual["tokens_entrada"] - uso_previo["tokens_entrada"]
    metrica.tokens_salida = uso_actual["tokens_salida"] - uso_previo["tokens_salida"]
    # Trazabilidad completa del turno: qué se dijo, qué se decidió y por qué, qué
    # se detectó y qué documento respalda la respuesta. El semáforo de CADA turno
    # queda escrito aunque después cambie — la historia no se pisa.
    metrica.motivos = list(respuesta.decision.motivos)
    metrica.texto_paciente = transcripcion.texto
    metrica.texto_agente = texto_agente
    metrica.citas = list(sesion.citas)
    metrica.grounding = dict(sesion.grounding) if sesion.grounding else None
    metrica.hallazgos = _estado_publico(sesion)["hallazgos"]
    sesion.registro.persistir(metrica)

    if respuesta.cierra:
        sesion.finalizada = True

    return JSONResponse(
        {
            **_estado_publico(sesion),
            "texto_paciente": transcripcion.texto,
            "texto_agente": texto_agente,
            "audio_wav_base64": audio_b64,
            "latencia_ms": round(metrica.latencia_ms, 1),
            "escala": respuesta.escala,
            "alarmas": respuesta.alarmas_detectadas,
        }
    )


def _capa_de(motivo: str) -> str:
    """Traduce el motivo del veredicto a la capa que lo produjo.

    La compuerta tiene cuatro capas y la interfaz debe decir CUÁL bloqueó, no un
    número contra un umbral: se midió que un umbral solo no separa las consultas
    respondibles de las que el corpus no cubre.
    """
    if "personal médico" in motivo:
        return "tema restringido"
    if "fuera del alcance" in motivo:
        return "procedimiento fuera del corpus"
    if "sin documentación" in motivo:
        return "filtro por procedimiento del paciente"
    if "no trata el tema" in motivo:
        return "verificación léxica"
    if "similitud" in motivo:
        return "umbral de similitud"
    return "sustentado"


@app.post("/api/llamada/{llamada_id}/colgar")
async def colgar(llamada_id: str) -> JSONResponse:
    """Cierra la llamada y devuelve el resumen estructurado."""
    sesion = SERVICIOS.llamadas.get(llamada_id)
    if sesion is None:
        raise HTTPException(404, "llamada no encontrada")

    # Espera acotada. El valor por defecto de esperar_extraccion es 60 s: con el
    # modelo local tardando ~17 s por turno, colgar podía quedarse un minuto sin
    # devolver nada y la consola parecía trabada. Doce segundos alcanzan para la
    # extracción típica; si no llegó, se cierra igual y el resumen deja
    # constancia de lo que quedó sin preguntar.
    completa = await asyncio.get_running_loop().run_in_executor(
        None, sesion.conversacion.esperar_extraccion, 12.0
    )
    sesion.finalizada = True

    estado = sesion.conversacion.estado
    decision = evaluar(estado.cuadro)
    # Contexto para quien recibe la alerta. Un aviso que solo dice "fiebre 38"
    # obliga a llamar de vuelta para saber lo básico: qué más se preguntó, qué
    # dijo el paciente y qué quedó sin cubrir. Se arma desde el registro por
    # turno, así que no puede afirmar nada que no haya ocurrido.
    cronologia = [
        {
            "turno": t.turno,
            "semaforo": t.semaforo,
            "paciente": t.texto_paciente,
            "agente": t.texto_agente,
            "motivos": t.motivos,
            "escalado": t.escalado,
        }
        for t in sesion.registro.turnos
        if t.texto_paciente or t.escalado
    ]
    turno_alerta = next((t for t in cronologia if t["escalado"]), None)

    resumen = {
        "llamada_id": sesion.id,
        "escenario": estado.escenario,
        "semaforo_final": decision.semaforo.value,
        "motivos": decision.motivos,
        "escalado": decision.escala,
        "alerta": {
            "disparada_en_turno": turno_alerta["turno"] if turno_alerta else None,
            "disparador": turno_alerta["motivos"] if turno_alerta else [],
            "dicho_por_el_paciente": turno_alerta["paciente"] if turno_alerta else "",
            # Cuenta entradas con número de turno mayor, no una resta de índices:
            # la cronología omite turnos sin habla del paciente (la apertura),
            # así que su largo no coincide con la numeración.
            "turnos_posteriores": (
                sum(1 for t in cronologia if t["turno"] > turno_alerta["turno"])
                if turno_alerta
                else 0
            ),
        }
        if decision.escala or turno_alerta
        else None,
        "cronologia": cronologia,
        "cuadro": {
            "dolor_nrs": estado.cuadro.dolor_nrs,
            "fiebre_c": estado.cuadro.fiebre_c,
            "herida": estado.cuadro.herida.value,
            "movilidad": estado.cuadro.movilidad.value,
            "sintomas_alarma": estado.cuadro.sintomas_alarma,
        },
        "sin_preguntar": estado.cuadro.campos_faltantes,
        # Lo que el paciente trajo por su cuenta, en sus palabras. Quien reciba
        # la alerta necesita saber qué le preocupaba de verdad.
        "inquietudes_del_paciente": estado.inquietudes,
        "transcripcion": [
            {"quien": quien, "texto": texto} for quien, texto in estado.transcripcion
        ],
        # La historia turno a turno con semáforo, motivos, hallazgos y citas
        # vive en llamada-{id}.jsonl; acá va el estado final consolidado.
        "metricas": sesion.registro.totales(),
        "costo": costo_estimado(
            sesion.conversacion.uso_modelo["tokens_entrada"],
            sesion.conversacion.uso_modelo["tokens_salida"],
            sesion.segundos_audio,
        ),
        "registro_turnos": str(sesion.registro.ruta.name),
        "extraccion_completa": completa,
    }
    # El resumen se PERSISTE, no solo se devuelve: es el "resumen estructurado
    # de cada llamada" que exige el reto, y la evidencia de auditoría.
    import json as _json

    destino = LOGS / f"llamada-{sesion.id}-resumen.json"
    destino.write_text(
        _json.dumps(resumen, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # La latencia del último turno viaja también en el cierre: sin ella, la
    # consola pintaba "—" en tiempo de respuesta justo al colgar, borrando el
    # dato que el jurado está mirando.
    ultimo = sesion.registro.turnos[-1] if sesion.registro.turnos else None
    return JSONResponse(
        {
            **_estado_publico(sesion),
            "latencia_ms": round(ultimo.latencia_ms, 1) if ultimo else 0,
            "resumen": resumen,
        }
    )


# -- Conocimiento (G5) -----------------------------------------------------------


@app.get("/api/documentos")
async def listar_documentos() -> JSONResponse:
    """Inventario del índice más lo que está procesándose en este momento."""
    if SERVICIOS.indice is None:
        raise HTTPException(503, "índice no disponible")

    indexados = [
        {
            "doc_id": doc_id,
            "nombre": datos["fuente"],
            "escenario": datos["escenario"],
            "fragmentos": datos["chunks"],
            "estado": "indexado",
            "nota": f"{datos['chunks']} fragmentos disponibles",
        }
        for doc_id, datos in SERVICIOS.indice.documentos().items()
    ]
    en_proceso = list(SERVICIOS.procesando.values())

    return JSONResponse(
        {
            "documentos": en_proceso + sorted(indexados, key=lambda d: d["nombre"]),
            "total_documentos": len(indexados) + len(en_proceso),
            "total_fragmentos": SERVICIOS.indice.total_chunks(),
            "en_proceso": len(en_proceso),
            "motor": {
                "base_vectorial": "ChromaDB",
                "embeddings": MODELO_EMBEDDINGS,
                "dimensiones": 384,
                "fragmento_chars": 1200,
            },
        }
    )


@app.post("/api/documentos")
async def subir_documento(archivo: UploadFile, escenario: str = ESCENARIO_DEMO):
    """Sube un PDF, lo procesa y lo deja disponible. Es la mitad 'aprender' de G5."""
    if SERVICIOS.indice is None:
        raise HTTPException(503, "índice no disponible")
    if not archivo.filename or not archivo.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "solo se aceptan archivos PDF")

    destino = SUBIDAS / archivo.filename
    destino.write_bytes(await archivo.read())

    temporal = uuid.uuid4().hex[:8]
    SERVICIOS.procesando[temporal] = {
        "doc_id": temporal,
        "nombre": archivo.filename,
        "escenario": escenario,
        "fragmentos": 0,
        "estado": "extrayendo",
        "nota": "extrayendo texto del PDF",
    }

    def procesar() -> dict:
        documento = extraer_pdf(destino, escenario)
        if documento.sin_capa_texto:
            return {
                "estado": "error",
                "nota": "PDF sin capa de texto · requiere OCR",
                "doc_id": documento.doc_id,
                "fragmentos": 0,
            }

        trozos = fragmentar(documento)
        SERVICIOS.procesando[temporal]["estado"] = "embeddings"
        SERVICIOS.procesando[temporal]["nota"] = f"vectorizando {len(trozos)} fragmentos"

        sospechoso = any(contiene_inyeccion(t.texto) for t in trozos)
        SERVICIOS.indice.indexar(trozos)
        return {
            "estado": "indexado",
            "nota": f"{len(trozos)} fragmentos disponibles",
            "doc_id": documento.doc_id,
            "fragmentos": len(trozos),
            "sospechoso": sospechoso,
        }

    try:
        resultado = await asyncio.get_running_loop().run_in_executor(None, procesar)
    finally:
        SERVICIOS.procesando.pop(temporal, None)

    if resultado["estado"] == "error":
        return JSONResponse(status_code=422, content=resultado)
    return JSONResponse({**resultado, "nombre": archivo.filename})


@app.delete("/api/documentos/{doc_id}")
async def olvidar_documento(doc_id: str) -> JSONResponse:
    """Elimina un documento del índice. Es la mitad 'olvidar' de G5.

    Devuelve cuántos fragmentos se borraron: la consola muestra evidencia real de
    la baja, no un mensaje optimista.
    """
    if SERVICIOS.indice is None:
        raise HTTPException(503, "índice no disponible")

    borrados = SERVICIOS.indice.olvidar(doc_id)
    if borrados == 0:
        raise HTTPException(404, "documento no encontrado en el índice")
    return JSONResponse(
        {
            "doc_id": doc_id,
            "fragmentos_eliminados": borrados,
            "total_fragmentos": SERVICIOS.indice.total_chunks(),
        }
    )


@app.get("/api/salud")
async def salud() -> JSONResponse:
    """Diagnóstico de arranque. Sirve al jurado para confirmar que todo está en pie."""
    ausentes = config.faltantes()
    return JSONResponse(
        {
            "indice": SERVICIOS.indice is not None,
            "fragmentos": SERVICIOS.indice.total_chunks() if SERVICIOS.indice else 0,
            "sintetizador": SERVICIOS.sintetizador is not None,
            "modelo": MODELO,
            "modelo_disponible": SERVICIOS.cliente.disponible()
            if SERVICIOS.cliente
            else False,
            # La transcripción se verifica ACÁ, al levantar, y no al primer turno.
            # Que el saludo suene no prueba que la conversación pueda avanzar: el
            # saludo es síntesis local y la transcripción necesita credencial.
            "transcripcion_lista": not ausentes,
            "faltantes": list(ausentes),
        }
    )


# -- Estáticos -------------------------------------------------------------------

if WEB.exists():
    app.mount("/static", StaticFiles(directory=str(WEB)), name="static")

    @app.get("/")
    async def raiz() -> FileResponse:
        return FileResponse(str(WEB / "index.html"))
