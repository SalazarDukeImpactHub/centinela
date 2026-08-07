# Revisión de seguridad — F2 (capa de conocimiento)

**Fecha:** 2026-08-07 · **Alcance:** `src/rag/` (extracción, fragmentación, índice,
compuerta de grounding), `scripts/ingest.py`
**Fuera de alcance:** pipeline de voz y consolas web (no implementados aún);
infraestructura y CI (inexistentes en esta fase).

**Veredicto:** ENTREGABLE CON OBSERVACIONES

## Método

Revisión adversarial independiente con un modelo distinto al que escribió el
código, más verificación empírica de cada hallazgo reportado. Dos de los cinco
hallazgos del revisor resultaron **falsos** al reproducirlos, y se documentan como
tales: aceptar hallazgos sin verificarlos habría llevado a "corregir" comportamiento
que ya era correcto.

## Hallazgos confirmados y resueltos

### 🟠 ALTO — Inyección de prompt a través de documentos indexados

**Vector:** la compuerta G5 obliga a exponer una consola de carga de documentos, y
el jurado la usa con material propio. El texto de esos documentos llega al prompt
del modelo, de modo que un PDF preparado puede intentar darle instrucciones.

La rúbrica penaliza el caso por nombre: *"que el agente obedezca instrucciones que
contradicen su misión. Anula el apartado correspondiente y se anota textualmente"*.

**Resuelto** en `src/rag/saneamiento.py`, con defensa en dos frentes:

1. Neutralización de patrones de instrucción sobre el texto recuperado —órdenes de
   ignorar instrucciones, reasignación de rol, marcadores de turno inyectados,
   intentos de suprimir el escalamiento.
2. Delimitación explícita del bloque de documentación, con los delimitadores
   neutralizados dentro del propio texto para que un documento no pueda cerrarlos
   y escribir fuera del bloque.

**Verificado:** 14 ataques neutralizados, 6 textos clínicos legítimos intactos
(`tests/test_saneamiento.py`, 38 pruebas).

**Detalle que casi lo deja pasar:** la primera versión no detectaba `"actuás como
un médico"` porque el voseo lleva tilde y el patrón buscaba `actua`. Se resolvió
comparando sobre texto sin acentos con un mapa 1:1 que preserva longitud, de modo
que las posiciones halladas sirven para sustituir sobre el original.

**Límite declarado:** ninguna defensa por patrones es completa. El diseño no depende
de que lo sea: la decisión clínica vive en código —motor de escalamiento, detector
de alarmas, compuerta de grounding— y no en lo que el modelo haga con el texto.

### 🟡 MEDIO — La consola podía caerse ante metadatos incompletos

`IndiceClinico.documentos()` accedía a las claves de metadatos sin verificar. Un
chunk escrito por otra versión del código, o un índice restaurado a medias, dejaba
a la operación sin poder listar ni borrar documentos — y G5 se verifica desde ahí.

**Resuelto** con acceso defensivo en `src/rag/index.py`.

## Hallazgos reportados y NO confirmados

| Reportado | Verificación |
|---|---|
| "Consultas en español quedan bloqueadas contra corpus en inglés: *drenaje después de colecistectomía* se bloquea" | **Falso.** Esa consulta pasa. También *dieta después de colecistectomía*. |
| "Consultas largas exigen demasiadas coincidencias: 6 términos requieren 4" | **Falso.** La consulta citada tiene 5 términos distintivos, exige 3 y pasa. |
| "Patrón de división por cero en `total_chars`" | El propio revisor reconoce que está protegido. Sin impacto. |

## Hallazgo propio durante la verificación

La consulta *"¿cuándo puedo comer normal después de la operación de vesícula?"*
—español coloquial contra documentación en inglés— **sí queda bloqueada**. El
revisor apuntó al problema correcto con el ejemplo equivocado.

Es un falso negativo de la compuerta: el agente declara que no sabe cuando podría
responder. **Conservador y seguro, pero mejorable.** Queda registrado como deuda
para F3, con dos caminos posibles: consultar el índice también con la consulta
traducida, o incorporar un mapa de términos clínicos español↔inglés.

## Riesgo aceptado (sin cambios)

| Riesgo | Motivo | Mitigación |
|---|---|---|
| API key en el repositorio público | Exigido por G2 | Key desechable · revocación el 18 de agosto |

## Incidente resuelto durante la sesión

El archivo `.env` con la credencial de Groq quedó dentro del clon del repositorio
público del kit, **sin estar en su `.gitignore`** — aparecía como archivo sin
seguir, y un `git add -A` lo habría publicado. Se movió al repositorio de la
solución, donde sí está ignorado, verificando la copia antes de eliminar el
original.
