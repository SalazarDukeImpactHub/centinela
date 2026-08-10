# Demostración de conocimiento vivo (compuerta G5)

La compuerta se verifica con *"un documento de prueba que no forma parte de
ningún corpus entregado"*. Este es ese documento — y el tema no es casual.

## Por qué mastectomía

La carpeta `breast_cancer` del kit **contiene literatura de cáncer de cuello
uterino**: 18 de sus 19 documentos mencionan cérvix y ninguno menciona mama,
mientras 8 de los 40 pacientes del dataset fueron mastectomizados
([`docs/corpus-hallazgos.md`](../corpus-hallazgos.md)).

Eso convierte la demostración en algo más fuerte que *"subo un PDF y aparece en
una lista"*: se prueba que el agente **declara el límite antes**, **cita
después**, y **vuelve a declararlo** al eliminarlo. Las tres cosas en el tema
donde el corpus tiene un hueco real.

## El documento

[`cuidados-tras-mastectomia-DEMO.pdf`](cuidados-tras-mastectomia-DEMO.pdf) —
11 KB, 1 página, 3 fragmentos. Guía de cuidados en casa clínicamente correcta,
marcada de forma visible como material de demostración.

Se regenera con:

```bash
python scripts/generar_documento_demo.py
```

## El guion, paso a paso

Con el servicio en pie (`docker compose up` → http://localhost:8080):

### 1 · Antes de subirlo — el agente declara el límite

En la **interfaz de llamada**, preguntar por voz:

> *"¿Cuándo me retiran el drenaje?"*

El agente responde que no lo sabe y que lo anota para el equipo. El panel
derecho muestra la capa de la compuerta que bloqueó.

**Esto es lo que hay que mostrar primero.** No inventa, y —más importante— no
cita literatura de cuello uterino con formato impecable.

### 2 · Subir el documento

Pestaña **Conocimiento** → arrastrar el PDF. La fila aparece con su estado de
procesamiento y termina en `Indexado`, con el número de fragmentos.

### 3 · El agente lo usa

Volver a la llamada y repetir la misma pregunta.

Ahora responde citando **archivo y página**, y la cita aparece en el panel
*"de dónde saca la respuesta"*.

### 4 · Eliminarlo

Pestaña **Conocimiento** → `Eliminar`. El aviso confirma **cuántos fragmentos
se borraron**, no un mensaje optimista.

### 5 · El agente lo olvida

Repetir la pregunta una tercera vez. Vuelve a declarar el límite, sin citas.

## Verificación automatizada

El mismo ciclo, sin interfaz:

```bash
pytest tests/test_conocimiento_vivo.py -v
```

Ocho pruebas: alta, uso, baja, olvido, persistencia tras reinicio, idempotencia
—el mismo archivo dos veces no duplica—, aislamiento entre documentos, y borrar
un documento inexistente sin romper la consola.

## Resultado medido

| Paso | Resultado |
|---|---|
| 1 · Antes | bloqueado — *"el material recuperado no trata el tema consultado"* |
| 2 · Subir | `doc_id` asignado · 3 fragmentos · índice 6.512 → 6.515 |
| 3 · Usar | cita `cuidados-tras-mastectomia-DEMO.pdf (p. 1)` |
| 4 · Eliminar | 3 fragmentos eliminados · índice 6.515 → **6.512** |
| 5 · Olvidar | bloqueado, cero citas |

El índice vuelve **exacto** a su tamaño original: no quedan residuos.
