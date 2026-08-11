# Auditoría de seguridad pre-entrega

**Fecha:** 2026-08-10 · **Alcance:** toda la superficie expuesta del sistema
(API HTTP, consola web, manejo de archivos, secretos, contenedor).
**Método:** revisión de código dirigida por superficie de ataque + verificación
ejecutada contra el sistema corriendo. Cada hallazgo se comprobó antes de
declararlo y se re-comprobó después del arreglo.

---

## Resumen

| Severidad | Hallazgos | Estado |
|---|---|---|
| **Alta** | 1 | corregido y con prueba |
| Media | 1 | documentado, no aplicado — ver justificación |
| Baja | 0 | — |
| Riesgos aceptados | 2 | declarados abajo |

Superficies verificadas sin hallazgo: inyección de prompt hacia el RAG y el
modelo, XSS en la consola, CORS, endpoint de eliminación, manejo de secretos.

---

## ALTA · Path traversal en la carga de documentos

**Dónde:** `POST /api/documentos` — `src/api/app.py`

El nombre del archivo llegaba del cliente y se concatenaba directo contra la
carpeta de subidas:

```python
destino = SUBIDAS / archivo.filename       # antes
```

La única validación era que el nombre terminara en `.pdf`. **Se comprobó la
extensión, no la ruta.** `pathlib` no sanea nada, y dos formas medidas escapaban
de la carpeta:

| `filename` enviado | Dónde escribía |
|---|---|
| `../../../fuera.pdf` | dos niveles arriba del proyecto |
| `C:/Windows/Temp/evil.pdf` | ruta absoluta — `pathlib` descarta la base |

**Impacto.** Escritura de archivo arbitraria, limitada a la extensión `.pdf`. No
es solo un problema de disco: sobrescribir un PDF del propio corpus alcanza para
envenenar lo que el agente le cita a un paciente — el mismo ataque contra el que
existe `saneamiento.py`, entrando por otra puerta. Y el contenedor corre como
root (ver hallazgo siguiente), lo que amplía a dónde se puede escribir.

**Corrección.** `_nombre_seguro()` descarta cualquier ruta y se queda con el
nombre del archivo, normalizando las dos barras porque el servidor corre en
Linux y el cliente puede ser Windows. Además, cinturón y tirantes: se verifica
que el destino resuelto caiga dentro de la carpeta de subidas, de modo que si el
saneo cambia algún día, la puerta siga cerrada.

**Prueba:** `tests/test_seguridad_carga.py` — 6 vectores de escape, 3
comprobaciones de destino, 6 rechazos de nombre inválido, y 4 nombres legítimos
que deben seguir pasando para no romper la demostración G5.

**Corrección de la revisión F2.** La revisión de la fase F2 declaraba *«path
traversal en consola de carga»* dentro de su alcance. No estaba cubierto. Se
deja constancia acá en vez de editar aquel documento: una revisión de seguridad
que se reescribe hacia atrás deja de ser evidencia.

---

## MEDIA · El contenedor corre como root

**Dónde:** `Dockerfile` — sin instrucción `USER`.

La imagen no crea un usuario sin privilegios, así que el proceso corre como root
dentro del contenedor. Es endurecimiento estándar (OWASP A05), y habría agravado
el hallazgo anterior mientras estuvo abierto.

**Corrección recomendada** (dos líneas, antes del `CMD`):

```dockerfile
RUN useradd --create-home --uid 10001 centinela && chown -R centinela /app
USER centinela
```

**Por qué NO se aplicó en esta entrega.** La compuerta G2 mide el levantamiento
con cronómetro y está verificada en 6 min 07 s. Cambiar el usuario del
contenedor toca permisos de los volúmenes montados (`chroma_data`, `logs`,
`subidas`) y exige volver a medir G2 de punta a punta. Aplicar un cambio no
verificado sobre el camino de arranque evaluado, a horas de la entrega, es peor
riesgo que el que corrige. Queda documentado con el arreglo escrito para
aplicarlo apenas se pueda re-medir.

---

## Verificado sin hallazgo

| Superficie | Cómo se comprobó | Resultado |
|---|---|---|
| Inyección de prompt hacia el RAG y el modelo | `pytest tests/test_saneamiento.py` | 38 pruebas · batería de ataques bloqueada |
| XSS en la consola | inyección de `<img src=x onerror=alert(1)>` en el habla del paciente, renderizada en el resumen | llega a pantalla **como texto** · 0 elementos creados |
| Escapado de la transcripción y las citas | revisión de `web/consola.js` | todo el contenido de origen externo pasa por `esc()` |
| CORS | revisión de `src/api/app.py` | sin middleware permisivo · el navegador aplica la política restrictiva por defecto |
| Eliminación de documentos | revisión de `DELETE /api/documentos/{doc_id}` | opera solo sobre el índice vectorial · no toca el sistema de archivos |
| Secretos en el repositorio | `.gitignore` + inspección de archivos versionados | `.env` y `*.env` excluidos · ninguna credencial versionada fuera de la declarada abajo |
| Grabaciones de audio del paciente | `src/api/app.py` | el archivo temporal se borra en `finally` tras transcribir · no queda audio en disco |
| Registros de llamada con habla del paciente | `.gitignore` | `logs/` excluido · el habla del paciente nunca sale del equipo por el repositorio |

---

## Riesgos aceptados

| Riesgo | Por qué se acepta | Mitigación |
|---|---|---|
| **API key de Groq incluida en el repositorio público** | Exigido por la compuerta G2 del reto («credenciales, URLs y accesos incluidos») | Key desechable creada para la evaluación · revocación inmediata post-evaluación (18 ago) · sin otros permisos asociados |
| **La API no tiene autenticación** | Es una consola de demostración que el jurado debe poder levantar y usar sin credenciales. El puerto se publica en `8080:8080`, accesible desde la red local | Sin datos reales de pacientes · sin persistencia entre reinicios más allá de `logs/`, que no se versiona · **no apto para producción sin una capa de autenticación y TLS**, y así se declara |

---

## Qué haría falta antes de un uso real

Esto es un prototipo de evaluación, no un sistema clínico desplegable. Lo mínimo
antes de tocar datos de pacientes reales:

1. **Autenticación y autorización** por rol (operador / clínico / auditor), con
   TLS terminado antes de la aplicación.
2. **Usuario sin privilegios en el contenedor** — ya escrito arriba.
3. **Política de retención de `logs/`**: hoy guardan el habla del paciente
   textual y sin cifrar, indefinidamente. Necesita cifrado en reposo, plazo de
   borrado y registro de acceso.
4. **Límite de tamaño y de tasa** en la carga de documentos: hoy no hay tope, y
   un PDF grande bloquea un hilo del pool de proceso.
