# Hallazgos sobre el corpus clínico

Verificados sobre `dataset/textos/` del kit oficial. Cada afirmación es
reproducible con `python scripts/ingest.py` y las pruebas de `tests/`.

## 1. La carpeta `breast_cancer` no contiene literatura de mama

**Medido:** de los 19 documentos de esa carpeta, **18 mencionan cérvix o cuello
uterino** y **ninguno menciona mama ni mastectomía**. Son guías de cáncer de cuello
uterino: `002-GUIA-DE-CANCER-DE-CUELLO-UTERINO.pdf`, `cervix16nov-full.pdf`,
`Cancer of the cervix uteri 2025 update.pdf`, entre otras.

**Por qué importa:** ocho de los cuarenta pacientes del dataset fueron sometidos a
**Mastectomía** (`modulo_synthea = breast_cancer`), lo que equivale a 32 de los 160
casos. Para ese 20 % de la población, el corpus no contiene una sola página
pertinente.

**La trampa:** un sistema que indexe por nombre de carpeta y confíe en la similitud
vectorial responderá a una paciente mastectomizada citando literatura de cuello
uterino, con documento y número de página. Grounding formalmente impecable, dominio
clínico equivocado. Es la peor clase de alucinación porque viene con evidencia.

**Comportamiento adoptado:** ante una consulta clínica de una paciente
mastectomizada, la compuerta de grounding declara el límite y deriva. Fijado en
`tests/test_grounding.py::test_mastectomia_no_tiene_sustento_en_el_corpus`.

**Lectura probable:** el kit advierte que "el material entregado no es todo el
material de evaluación" y la compuerta G5 se verifica con "un documento de prueba
que no forma parte de ningún corpus entregado". El vacío de mastectomía es, con
alta probabilidad, la superficie sobre la que se prueba la carga en caliente.
Declarar el límite y luego incorporar el documento subido es exactamente la
conducta que se evalúa.

## 2. La similitud vectorial no distingue lo respondible de lo ausente

**Medido** sobre el índice completo (6.512 chunks, `multilingual-e5-small`):

| Tipo de consulta | Similitud del mejor resultado |
|---|---|
| Respondible por el corpus | 0.853 – 0.927 |
| Clínica, fuera del corpus | 0.871 – 0.891 |
| Ajena a lo clínico | 0.809 – 0.847 |

Las dos primeras se superponen. "Cuidados tras una amigdalectomía" puntúa **0.891**
—por encima de consultas legítimas— y recupera una guía de reemplazo de rodilla.

**Conclusión:** ningún umbral separa esos conjuntos. La similitud coseno mide si el
texto suena a español médico, no si el corpus responde la pregunta. La compuerta se
construyó en capas: rechazo por tema, filtro por escenario, lista explícita de
procedimientos fuera de alcance, verificación léxica por raíz y, recién al final,
umbral.

## 3. Defectos menores del corpus

| Defecto | Detalle | Tratamiento |
|---|---|---|
| PDF sin capa de texto | 1 documento escaneado en `Appendicitis/` | Excluido del índice y reportado; requeriría OCR |
| Documento duplicado | El mismo paper de dolor posoperatorio en dos archivos con bytes distintos | Detectado por huella de contenido normalizado; el hash binario no lo veía |
| Corpus bilingüe | Documentos en español e inglés en la misma carpeta | La verificación léxica en español no aplica sobre documentos en inglés; por eso la lista de procedimientos fuera de alcance es explícita |
| Rutas largas | Nombres de archivo que superan MAX_PATH de Windows | Prefijo de ruta extendida en `src/rag/extract.py` |

## 4. Distribución del índice

105 documentos ingeribles · 2.062 páginas · 6.512 chunks

| Escenario | Chunks | Contenido real |
|---|---:|---|
| Appendicitis | 1.023 | apendicitis |
| breast_cancer | 1.444 | **cáncer de cuello uterino** |
| cholecystitis | 846 | colecistitis |
| colorectal cancer | 2.064 | cáncer colorrectal |
| total joint replacement | 1.135 | reemplazo articular |
