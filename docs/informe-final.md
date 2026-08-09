# Informe final — Centinela

**Tech Sphere Challenge 2026** · Agente de voz para seguimiento posoperatorio
**Autora:** Jennifer Salazar Duke · Salazar Duke Impact Hub
**Repositorio:** https://github.com/SalazarDukeImpactHub/centinela

> Documento vivo hasta la entrega. Los números son medidos y reproducibles con
> los comandos que se indican en cada sección.

---

## 1. Declaración de modelo (compuerta G3)

### Modelo usado: `llama3.2:3b`, local, vía Ollama

De la lista cerrada de `stack-tecnico.md` §1. **Ningún otro modelo de lenguaje
participa del razonamiento del agente.**

### Por qué este y no otro: los dos de nube fueron retirados

El 7 de agosto de 2026 verifiqué la disponibilidad real de los cuatro modelos
permitidos contra las API de los proveedores, no contra su documentación. Los
scripts que lo comprueban están en el repositorio y son reejecutables:

```bash
python scripts/check_groq.py     # lista los modelos que Groq sirve hoy
python scripts/check_gemini.py   # lista los que sirve Google AI Studio
```

| Modelo permitido | Resultado de la verificación |
|---|---|
| Google Gemini 1.5 Flash | **Ausente.** La cuenta sirve 42 modelos con `generateContent` y ninguno es 1.5 — Google los retiró |
| Llama 3.1 70B vía Groq | **Ausente.** Groq sirve `llama-3.3-70b-versatile` y `llama-3.1-8b-instant`; ninguno es «Llama 3.1 70B» |
| Llama 3.2 (1B / 3B) local | **Disponible** |
| Phi-3.5 Mini local | **Disponible** |

**Los dos modelos de nube de la lista ya no existen como servicio.** La
arquitectura local no fue una preferencia de diseño: fue el único camino sin
riesgo de descalificación bajo G3.

### Por qué 3B y no 1B

El 1B es casi el doble de rápido (9,9 tok/s contra 5,6 en el hardware de
referencia) y se descartó por **calidad, no por velocidad**. Ante un paciente
que reportó *escalofríos*, respondió hablando de *«escarmiento»* e inventó
palabras. En contexto clínico eso es inadmisible. El 3B respondió coherente y
escaló correctamente.

### Nota de integridad

En el live del 22 de julio se recomendó Haiku de Anthropic. Esa recomendación no
se siguió: el `stack-tecnico.md` publicado después excluye a Anthropic y G3
declara que usar un modelo fuera de la lista descalifica. Se optó por la lectura
estricta del documento vigente.

---

## 2. La decisión técnica más relevante

### Sacar el modelo de lenguaje de toda decisión clínica

**El problema.** Un modelo de 3B corriendo en CPU es lento (~17 s por
extracción) y comete errores que en salud no son tolerables. Ambas cosas se
midieron, no se supusieron.

**La decisión.** El modelo extrae *qué dijo el paciente*. Todo lo demás
—clasificar criticidad, decidir si se escala, elegir la siguiente pregunta— vive
en código determinista, auditable y probado contra los 160 casos etiquetados.

**Cómo se llegó ahí.** No de entrada. Cada responsabilidad bajó al código
después de una falla concreta y medida:

| Nº | Falla observada | Consecuencia si quedaba en el modelo |
|---|---|---|
| 1 | Ante la lista de síntomas en el prompt, el 3B inventó `dolor_toracico` en turnos sobre fiebre y sobre la herida | Escalamiento con motivo falso; registro clínico falseado |
| 2 | *«Creo que como 38»* escalaba un turno tarde | El dato más importante de la llamada llegaba después de cambiar de tema |
| 3 | El 3B extrajo `fiebre_c=38` de un texto **sin ninguna cifra** | Registro que dice 38 cuando la paciente dijo 34 |
| 4 | Whisper transcribió *«38»* como *«58»* y el sistema lo aceptó | Temperatura imposible en la historia clínica |
| 5 | *«Roja, hinchada y le sale líquido»* se registraba como eritema leve | Una **secreción purulenta** —que escala sola— degradada a amarillo |
| 6 | *«Nada de pus»* se registraba como secreción purulenta | 13 pacientes verdes escalando por un hallazgo que negaban |
| 7 | El eco decía *«un 7 de dolor, anotado»* y el resumen cerraba *«sin dato»* | El sistema afirmaba haber guardado algo que no guardó |

**El beneficio inesperado.** La misma decisión que protegía contra la
alucinación resolvió la latencia: como la decisión no depende del modelo, el
modelo puede correr **en segundo plano** mientras el paciente escucha la
respuesta. La latencia percibida bajó de **19 s a 4,2 s de P50**.

### Alternativas evaluadas y descartadas

| Alternativa | Por qué se descartó |
|---|---|
| Confiar la clasificación al modelo con un buen prompt | Los siete casos de la tabla anterior. Un prompt no es un contrato |
| Gemini 1.5 Flash con contexto de 1M (sin RAG) | Retirado por Google. Y G5 exige que borrar un documento haga que el agente lo olvide: eso requiere retrieval real |
| BGE-M3 como embeddings (el sugerido por el reto) | Medido: 1,5 chunks/s en el hardware de referencia = 72 min de indexación. Inviable para G2. Se usó `multilingual-e5-small`: 12,2 chunks/s |
| Voz `es_AR-daniela-high`, más natural | Factor de tiempo real 0,99 — al borde de entrecortarse. Se conservó `es_MX-claude-high`, cuatro veces más rápida |
| Umbral único de similitud para el grounding | Medido: las consultas respondibles (0,853–0,927) se **superponen** con las clínicamente ausentes (0,871–0,891). Ningún umbral las separa |

### Riesgos identificados

| Riesgo | Mitigación |
|---|---|
| El paciente minimizador subreporta | Un verde sin campos preguntados **no es un alta**: el agente sigue indagando. Aun así, 2 de 12 rojos se pierden en amarillo cuando el paciente dice *«un poquito»* con dolor real de 9 |
| Whisper transcribe mal las cifras | Rango fisiológico 30–43 °C, formas compuestas (*«30 y 5»*), y tope de dos reintentos antes de seguir |
| Inyección de prompt vía documentos subidos | La consola de G5 es un canal al prompt: el texto se sanea y se delimita como datos. La decisión clínica, además, no es inyectable |
| Credencial de Groq en repositorio público | Exigido por G2. Clave desechable, revocación el 18 de agosto |
| Corpus incompleto en la evaluación | Es lo esperado (§4). El agente declara el límite y la consola de carga en caliente es el mecanismo previsto |

### Qué cambiaría con dos semanas más

1. **Reranker cruzado sobre el RAG.** La compuerta actual funciona con capas
   baratas porque un reranker no entra en el presupuesto de latencia de esta
   máquina. Con más margen mejoraría la precisión de las citas.
2. **Detección del minimizador.** Los 2 falsos negativos que quedan son
   pacientes que subreportan sistemáticamente. El dataset marca ese estilo; un
   modelo de confianza por paciente permitiría ponderar sus respuestas.
3. **Traducción de consulta para el corpus bilingüe.** Documentado en
   `docs/security/F2-revision-seguridad.md`: una pregunta coloquial en español
   contra documentos en inglés queda bloqueada de más.
4. **Barge-in.** Que el paciente pueda interrumpir al agente mientras habla.

---

## 3. Hallazgos sobre el material entregado

### La carpeta `breast_cancer` contiene literatura de cáncer de cuello uterino

**Verificado por análisis de texto:** de los 19 documentos de esa carpeta, 18
mencionan cérvix o cuello uterino y **ninguno menciona mama ni mastectomía**.
Mientras tanto, 8 de los 40 pacientes del dataset tienen `Mastectomía` como
procedimiento — 32 de los 160 casos.

**La trampa:** un sistema que indexe por nombre de carpeta y confíe en la
similitud vectorial responderá a una paciente mastectomizada citando literatura
de cérvix, con documento y número de página. Grounding formalmente impecable,
dominio clínico equivocado.

**Conducta adoptada:** ante una consulta de esas pacientes, Centinela declara el
límite. Fijado en `tests/test_grounding.py` para que ninguna calibración futura
lo rompa. Detalle completo en [`docs/corpus-hallazgos.md`](corpus-hallazgos.md).

### Otros defectos del corpus, todos manejados

| Defecto | Tratamiento |
|---|---|
| 1 PDF escaneado sin capa de texto | Excluido del índice y reportado |
| 1 documento duplicado con bytes distintos | Detectado por huella de contenido normalizado; el hash binario no lo veía |
| Corpus bilingüe | La verificación léxica no aplica sobre documentos en inglés; por eso la lista de procedimientos fuera de alcance es explícita |
| Rutas que superan MAX_PATH en Windows | Prefijo de ruta extendida en `src/rag/extract.py` |
| 91 fragmentos con caracteres de control, 77 bytes nulos | Saneados: un byte nulo rompe SQLite, JSON y toda biblioteca en C |

---

## 4. Evidencia de proceso

### Cómo se trabajó con IA

El desarrollo se hizo con un agente de codificación bajo tres reglas explícitas,
visibles en el historial de commits:

1. **Verificar antes de afirmar.** Ningún número de este informe es una
   estimación. Los modelos disponibles se comprobaron contra las API; la
   latencia, con turnos de voz reales; el recall, con el banco de 160 casos.
2. **Cada corrección nace de una falla observada.** El historial muestra el
   patrón: prueba manual → falla concreta → diagnóstico → corrección → prueba de
   regresión que la fija.
3. **Los tests son el contrato.** Cuando una corrección rompió un test viejo, se
   revisó cuál de los dos tenía razón. En un caso el test ganó: al buscar
   calidez se escribió *«quédese tranquilo»* en el mensaje de escalación, y el
   test lo rechazó por falsa tranquilidad ante un síntoma de alarma.

### Trazabilidad de la evaluación

Cada llamada deja dos archivos en `logs/`:

- `llamada-{id}.jsonl` — una línea por turno con latencia, tokens, semáforo,
  motivos, hallazgos, citas y el texto de ambos lados.
- `llamada-{id}-resumen.json` — estado final consolidado, cronología, y el turno
  exacto en que se disparó la alerta.

Las métricas del README se recalculan desde esos archivos con
`python scripts/metricas_readme.py`. **No hay números escritos a mano.**

---

## 5. Gobernanza — arquitectura de controles trazable a ISO/IEC 42001

Cada elemento del harness es simultáneamente un control trazable al Anexo A de
ISO/IEC 42001. No es trabajo adicional: es el mismo artefacto nombrado con
vocabulario de norma.

| Exigencia del reto | Control ISO/IEC 42001 | Dónde vive |
|---|---|---|
| Escalar a humano ante bandera roja | Supervisión humana | `src/clinico/escalamiento.py` |
| Documento que sustenta cada respuesta | Trazabilidad y procedencia | `src/rag/grounding.py` |
| Consola de conocimiento vivo | Gestión de cambios del ciclo de vida | `POST`/`DELETE /api/documentos` |
| Registro estructurado al colgar | Registros y evidencia operacional | `src/observabilidad/metricas.py` |
| El agente dice «no sé» en vez de inventar | Transparencia hacia el usuario afectado | compuerta de grounding |
| Comprensión del problema | Evaluación de impacto del sistema de IA | este informe |

> **Declaración precisa:** esta solución presenta una *arquitectura de controles
> trazable al Anexo A de ISO/IEC 42001*. **No** afirma cumplir ni estar
> certificada en ISO 42001: esa norma describe un sistema de gestión
> organizacional que se certifica con auditoría y evidencia sostenida en el
> tiempo, y no se satisface con un repositorio construido en tres días.

---

## 6. Métricas

> Sección pendiente de recalcular sobre la sesión final de pruebas.
> Los valores vigentes están en el README y se regeneran con
> `python scripts/metricas_readme.py`.

| Métrica | Valor medido |
|---|---|
| Latencia P50 / P95 | 4.196 ms / 10.620 ms |
| Levantamiento (G2) | 6 min 07 s de 15 permitidos |
| Recall en rojo — motor aislado | 12/12 |
| Recall en rojo — pipeline conversacional completo | 10/12, **ninguno cae a verde** |
| Costo estimado por llamada | ~US$ 0,0006 |
| Suite de pruebas | 284 |
