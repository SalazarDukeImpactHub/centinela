# Línea base del motor de escalamiento

**Fecha:** 2026-08-07 (F0) · **Script:** [`scripts/baseline_triage.py`](../scripts/baseline_triage.py)

## Resultado

Umbrales clínicos deterministas aplicados a los 160 casos etiquetados del dataset:

```
pred      amarillo  rojo  verde
real
amarillo        25     0      0
rojo             0    12      0
verde           44     0     79

Recall en rojo:          12/12
Falsos negativos rojo:   0
Verdes sobre-escalados:  44
Exactitud global:        0.725
```

## Umbrales

| Nivel | Condición (OR) |
|---|---|
| **Rojo** | fiebre ≥ 38.0 °C · secreción purulenta · dolor NRS ≥ 8 |
| **Amarillo** | fiebre ≥ 37.5 °C · eritema leve · dolor NRS ≥ 4 · movilidad incapacitante nueva |
| **Verde** | resto |

## Qué prueba y qué no

**Sí prueba:** los umbrales, aplicados a un cuadro clínico correcto, reproducen el
ground truth con recall perfecto en rojo. La lógica de decisión no necesita que el
modelo sea inteligente — necesita extracción correcta.

**No prueba:** que el agente logre extraer ese cuadro conversando con un paciente
evasivo, confundido o minimizador. Ese es el problema difícil (F1, extracción
estructurada).

Este resultado es el **techo del motor de escalamiento**. La distancia entre este
número y el del pipeline conversacional completo *es* el error de extracción — y esa
es la métrica que hay que perseguir en F1.

## Los 44 sobre-escalados

Verdes clasificados como amarillo. Es una calibración deliberada, no un defecto:
la rúbrica declara la asimetría clínica de forma explícita —el falso negativo es la
falla catastrófica—. Un amarillo cuesta una llamada de verificación; un rojo perdido
cuesta un paciente.

El disparador principal es `eritema_leve` (11 de los 44 verdes lo presentan) y
`dolor_nrs ≥ 4`. Ajustable en F1 si el sobre-escalamiento resulta excesivo en la
conversación real, **nunca a costa del recall en rojo**.

## Señal clínica observada

| Label | dolor NRS (min–media–máx) | fiebre °C (min–media–máx) |
|---|---|---|
| verde | 0 – 2.33 – 6 | 36.2 – 36.93 – 37.9 |
| amarillo | 2 – 4.40 – 6 | 36.5 – 37.31 – 37.9 |
| rojo | 5 – 6.08 – 9 | 37.9 – 38.37 – 39.5 |

`herida = secrecion_purulenta` aparece en 3 casos, **los tres rojo** — señal perfecta.
`arquetipo_trayectoria = complicacion_real` cubre los 12 rojos (más 5 amarillos y 7 verdes),
pero es metadato del generador: **no es observable conversando** y no se usa en el motor.
