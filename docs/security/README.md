# Revisiones de seguridad — protocolo

Cada fase del desarrollo cierra con una revisión de seguridad documentada aquí.
Formato de archivo: `FN-revision-seguridad.md` (una por fase que toque superficie de ataque).

| Fase | Foco | Evidencia |
|---|---|---|
| F0 | Manejo de secretos (.env), superficie del compose | `F0-revision-seguridad.md` |
| F1–F2 | Validación de entradas, sanitización hacia RAG y modelo, path traversal en consola de carga | `F2-revision-seguridad.md` |
| F4 | Batería de inyección de prompt ejecutable + resultados | `F4-revision-seguridad.md` |
| Pre-entrega | Audit OWASP A01–A10 completo | `audit-final.md` |

## Riesgos aceptados

| Riesgo | Por qué se acepta | Mitigación |
|---|---|---|
| API key de Groq incluida en el repo público | Exigido por la compuerta G2 del reto ("credenciales, URLs y accesos incluidos") | Key desechable creada para la evaluación · revocación inmediata post-evaluación (18 ago) · sin otros permisos asociados |

Cada revisión reporta: alcance, hallazgos por severidad (Critical/High/Medium/Low),
fixes aplicados con commit asociado, y pendientes justificados.
