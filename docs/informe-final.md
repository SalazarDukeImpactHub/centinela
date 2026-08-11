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
| El paciente minimizador subreporta | **Resuelto y medido.** Un verde sin campos preguntados no es un alta, y además se detecta la minimización sistemática: ver §2.4 |
| Whisper transcribe mal las cifras | Rango fisiológico 30–43 °C, formas compuestas (*«30 y 5»*), y tope de dos reintentos antes de seguir |
| Inyección de prompt vía documentos subidos | La consola de G5 es un canal al prompt: el texto se sanea y se delimita como datos. La decisión clínica, además, no es inyectable |
| Credencial de Groq en repositorio público | Exigido por G2. Clave desechable, revocación el 18 de agosto |
| Corpus incompleto en la evaluación | Es lo esperado (§4). El agente declara el límite y la consola de carga en caliente es el mecanismo previsto |

### 2.4 Mejoras posteriores al primer cierre — todas medidas

Las cuatro líneas que este informe listaba como trabajo futuro se atacaron. Tres
se implementaron y una se descartó con el número que la descarta.

**Detección del minimizador — implementada.** Se contaron los marcadores de
minimización por llamada sobre los 160 casos:

| etiqueta | marcadores (mediana) |
|---|---|
| verde | 2 |
| amarillo | 3 |
| **rojo** | **6** |

El paciente que está PEOR es el que más resta importancia. Contraintuitivo hasta
que uno lo piensa: quien tiene 9 de dolor y dice *«un poquito molesto, uno
aguanta»* no informa mal — aguanta, que es otra cosa. La señal solo **pondera**
hallazgos existentes y nunca crea uno: sobre un cuadro verde no hace nada, y así
está fijado por test. Calibrada en 6 marcadores, **lleva el recall del pipeline
conversacional completo a 12/12** al costo de 2 verdes adicionales escalados.

**Puente bilingüe — implementado.** El corpus mezcla idiomas y 5 de 8 consultas
legítimas quedaban bloqueadas: el agente decía «no lo sé» sobre material que sí
tenía —29 fragmentos de colecistitis hablan de ducharse y *«¿cuándo me puedo
bañar?»* se rechazaba—. Tres correcciones: diccionario clínico auditable en vez
de traductor automático, consulta formulada en el registro del corpus (el
término suelto recupera con 0,816 y enmarcado como terminología clínica con
0,870), y recuperación ampliada de 15 a 40 candidatos porque los fragmentos
correctos no entraban en la red angosta. **Resultado: 13/13**, sin costo de
precisión — las seis consultas que deben bloquearse siguen bloqueadas.

**Barge-in — implementado.** El paciente puede interrumpir al agente mientras
habla, reusando la detección de voz de la escucha automática con un umbral más
alto para distinguir su voz del eco del parlante.

**Reranker cruzado — evaluado y descartado con el número que lo descarta.**
Se midió con `scripts/experimento_reranker.py` sobre las mismas 13 consultas de
calibración, usando el cross-encoder multilingüe más liviano disponible
(`mmarco-mMiniLMv2-L12-H384-v1`) — es decir, el mejor caso posible de latencia:

| | |
|---|---|
| Latencia media sobre 40 candidatos | **15.418 ms** (mín 788, máx 46.164) |
| Sobre un turno de 5.131 ms | **+300 %** |
| Carga del modelo al arrancar | 162 s |
| Consultas legítimas que aprobaría | **0 de 7** |

No se descartó solo por caro: **acierta peor**. Rechaza las siete consultas que
el corpus sí responde, y le asigna 1,961 —el puntaje más alto de toda la
tabla— a *«cuidados tras una amigdalectomía»*, que es exactamente la que hay que
bloquear porque el corpus no cubre ese procedimiento.

La razón es la misma que hizo funcionar el puente bilingüe: el reranker fue
entrenado sobre pares pregunta-respuesta de búsqueda web, y este corpus son
guías de práctica clínica que no responden preguntas — las declaran. Un modelo
entrenado para otra tarea no mejora por ser más grande.

**Conclusión:** la compuerta en capas, que cuesta ~50 ms y acierta 13/13, es
mejor que un reranker que cuesta 15 s y acierta 5/13. El experimento queda en el
repositorio para que la decisión sea verificable.

### 2.5 De cuestionario a conversación — y lo que costó aprenderlo

Las llamadas de prueba dejaron una crítica que ninguna métrica mostraba: el
agente sonaba a formulario. Tres conductas concretas, todas medidas con sondas
sobre el pipeline real:

| Conducta | Qué hacía |
|---|---|
| Preguntaba lo ya contestado | ante *«no he tenido fiebre, la herida seca, dolor dos y camino bien»* —los cuatro temas en un turno— preguntaba por la herida igual, y al turno siguiente decía *«no le entendí lo de la herida»* |
| Contestaba con su propia agenda | *«me preocupa la herida»* recibía *«disculpe, no le entendí la temperatura»* y la misma pregunta otra vez |
| Repetía el mismo acuse | una vez escalado, *«entiendo, eso sí es importante»* en todos los turnos siguientes |

La corrección **no** fue darle conducción al modelo: eso rompería la tesis de
toda la solución. Los cuatro temas siguen fijos y en orden clínico. Lo que cambia
es que el cuestionario pasó a ser la **agenda** del agente en vez de su guion —
tres reglas, todas en código determinista:

1. **Se tacha lo ya contestado.** Si un detector llenó el dato, el tema queda
   cubierto aunque nadie lo haya preguntado.
2. **Se sigue el tema que trae el paciente.** El tema nombrado pasa al frente de
   la cola y el pendiente vuelve después con *«volvamos a»*. Cambiar de tema no
   es lo mismo que no responder: la evasión real sigue recibiendo su reintento.
3. **Las palabras laxas exigen ancla.** *«Bien»*, *«normal»* y *«despacio»* solo
   valen si el agente acaba de preguntar por ese tema o la frase lo nombra.

**La tercera regla es la que enseñó el orden de las cosas, y costó una
regresión.** La primera versión —solo las reglas 1 y 2— bajó el recall del banco
conversacional de **12/12 a 11/12**. La causa: *«no muy bien la verdad, me
despierto varias veces»* —el paciente hablando de cómo durmió— registraba la
**herida como normal**, daba el tema por cubierto, la llamada terminaba tres
turnos antes y la señal de minimización nunca llegaba al umbral que escalaba ese
caso. En la misma revisión aparecieron dos falsos positivos gemelos: *«la veo
bien»* —sobre la herida— escribía movilidad normal, y *«como uno se siente»* se
anotaba como dolor 1/10.

El aprendizaje es transferible a cualquier agente que decida qué preguntar a
partir de lo que ya extrajo:

> Cuando los temas se tachan solos, **un falso positivo del detector deja de ser
> un dato de más y pasa a ser una pregunta que no se hace.** La fluidez no se
> compra aflojando los detectores.

Medido sobre los 160 casos de la capa ruidosa: recall en rojo **12/12** y verdes
sobre-escalados **57/123**, contra 61/123 antes del cambio. La conversación se
volvió más corta y más precisa a la vez.

**Dos fallas más, encontradas ejecutando una llamada completa contra la API real
—con transcripción de Whisper, no con texto simulado—:**

El paciente abrió con *«no me quitan el drenaje y eso me tiene preocupada»* y
recibió *«disculpe, no le entendí la temperatura»*. Le había entendido perfecto:
hablaba de otra cosa. El drenaje no es ninguno de los cuatro focos, así que no
entraba por la regla 2. Ahora se le reconoce lo que trajo —queda anotado para el
equipo, textual— y se repite la pregunta sin tratarlo como si no se hubiera
explicado. Si insiste sin responder, vuelve el reintento normal: reconocer no es
un bucle.

Y el resumen le informaba al equipo *«quedó sin preguntar: fiebre»* sobre un
paciente que había contestado *«fiebre no he tenido, nada»*. El campo se
consideraba faltante por no tener cifra. **Negar es contestar**: decirle a quien
recibe la alerta que un tema quedó sin explorar cuando sí se exploró es peor que
no decir nada. La negación se registra como el dato que es.

Las inquietudes, además, ahora se muestran en la consola —el resumen que ve el
equipo clínico— y no solo en el JSON. Se renderizan textuales y escapadas, como
el resto de la transcripción: se verificó que una carga inyectada en el habla del
paciente llega a pantalla como texto y no como marcado.

### Qué cambiaría con más tiempo

1. **Traducción completa del corpus**, no solo de la consulta. El diccionario
   cubre el vocabulario posoperatorio, pero una pregunta muy fuera de ese
   campo semántico seguiría sin puente.
2. **Modelo de confianza por paciente** que aprenda del historial de sus
   llamadas anteriores, no solo de la actual.
3. **Barge-in con cancelación de eco real**, para bajar el umbral de
   interrupción sin riesgo de que el agente se interrumpa a sí mismo.

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

### Auditoría de seguridad pre-entrega

Documentada en [`docs/security/audit-final.md`](security/audit-final.md). El
hallazgo de severidad alta merece constar acá porque contradice una revisión
anterior: el endpoint de carga de documentos concatenaba el nombre de archivo del
cliente directo contra la carpeta de subidas. Se validaba la extensión, **no la
ruta**, así que `../../../fuera.pdf` y `C:/Windows/Temp/evil.pdf` escapaban de la
carpeta. Escritura de archivo arbitraria limitada a `.pdf` — suficiente para
sobrescribir un documento del corpus y envenenar lo que el agente le cita a un
paciente. Corregido, con 19 pruebas que lo fijan.

La revisión de la fase F2 declaraba esa superficie dentro de su alcance y no la
cubría. **No se editó ese documento hacia atrás.** La corrección se registra en
la auditoría nueva: una revisión de seguridad que se reescribe deja de ser
evidencia, y el valor del historial está justamente en que muestre lo que se pasó
por alto.

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
| Recall en rojo — pipeline conversacional completo | 12/12, **ninguno cae a verde** |
| Verdes sobre-escalados (capa ruidosa) | 57/123 |
| Costo estimado por llamada | ~US$ 0,0006 |
| Suite de pruebas | 390 |
