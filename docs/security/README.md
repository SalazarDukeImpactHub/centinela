# Revisiones de seguridad

Cada revisión reporta alcance, hallazgos por severidad, correcciones aplicadas
con su prueba, y pendientes justificados.

| Momento | Foco | Evidencia |
|---|---|---|
| F1–F2 | Validación de entradas y sanitización hacia el RAG y el modelo | [`F2-revision-seguridad.md`](F2-revision-seguridad.md) |
| Continuo | Batería ejecutable de inyección de prompt | [`tests/test_saneamiento.py`](../../tests/test_saneamiento.py) — 38 pruebas, corre en cada `pytest` |
| Pre-entrega | Auditoría completa de la superficie expuesta | [`audit-final.md`](audit-final.md) |
| Pre-entrega | Path traversal en la carga de documentos | [`tests/test_seguridad_carga.py`](../../tests/test_seguridad_carga.py) — 19 pruebas |

## Sobre la trazabilidad de estas revisiones

La batería de inyección vive como **pruebas ejecutables y no como un documento**
a propósito: un informe de seguridad envejece en silencio, una prueba que se
rompe avisa. Corre en cada `pytest tests/`.

Por la misma razón, la revisión F2 no se editó hacia atrás cuando la auditoría
pre-entrega encontró que una de las superficies que declaraba en su alcance —el
path traversal en la consola de carga— no estaba realmente cubierta. La
corrección se documenta en `audit-final.md`. Una revisión de seguridad que se
reescribe hacia atrás deja de ser evidencia.

## Riesgos aceptados

Se declaran, con su justificación y mitigación, en
[`audit-final.md`](audit-final.md#riesgos-aceptados).
