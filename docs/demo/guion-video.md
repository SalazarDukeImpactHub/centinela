# Guion del video — entregable 04

**15 puntos.** Tres partes obligatorias: demo con grabación de pantalla, y las
dos preguntas de cierre **frente a cámara**.

Duración sugerida: **7–8 minutos.** No hay límite estipulado, pero el jurado ve
muchos videos. Lo que no aporta, resta.

| Bloque | Tiempo |
|---|---|
| Presentación (quién sos, qué es Centinela) | 0:30 |
| Llamada 1 — paciente que va bien | 1:30 |
| Llamada 2 — paciente que escala | 2:00 |
| Pregunta 1 frente a cámara | 1:30 |
| Pregunta 2 frente a cámara | 2:30 |

## Antes de grabar

```bash
docker compose up -d
```

Esperá a que el punto de la cabecera esté **verde** y diga que índice, voz,
modelo y transcripción están en pie. Es la prueba de que corre de verdad.

**Grabá cada llamada varias veces y quedate con la mejor toma.** Whisper es
probabilístico: la misma frase vuelve distinta entre tomas —medido, convirtió
*«un ocho»* en *«un no-chove»*—. Repetir una toma no es hacer trampa; es grabar
un demo.

Hablá **pausado y completo**. Los guiones de abajo están verificados contra el
contenedor: cada frase produce la respuesta que se indica.

---

## Llamada 1 — el paciente que va bien (1:30)

Muestra que el agente conversa, cita la guía con archivo y página, **declara su
límite** cuando no sabe, y cierra en verde sin dar de alta a ciegas.

| # | Lo que decís | Lo que hace el agente |
|---|---|---|
| 1 | *No he tenido fiebre.* | Lo anota y pasa a la herida |
| 2 | *¿Cuándo me puedo bañar?* | **Responde con la guía y la cita**: archivo y página en el panel derecho |
| 3 | *La herida la veo seca y limpia.* | Anota y pasa al movimiento |
| 4 | *El dolor es un dos, y camino bien.* | Cubre los dos temas de una y ofrece cerrar |
| 5 | *¿Qué dosis de tramadol tomo?* | **Declara el límite**: «no la puedo responder con la documentación que manejo, y prefiero no adivinar» |
| 6 | *No, nada más.* | Cierre verde con señales de alarma para consultar |

**Señalá en pantalla:** la cita con archivo y página en el turno 2, y en el turno
5 el panel que dice qué capa de la compuerta bloqueó la consulta.

**La frase para decir encima del turno 5:** *«No alucina una dosis. Declara que
no lo sabe y se lo deja anotado al equipo.»*

---

## Llamada 2 — el paciente que escala (2:00)

Es la llamada importante. Muestra que el paciente trae su propia agenda, que los
escalofríos sin termómetro son un dato, que la secreción escala, y que **el
agente no cuelga al escalar**.

| # | Lo que decís | Lo que hace el agente |
|---|---|---|
| 1 | *Doctora, no me quitan el drenaje y eso me tiene preocupada.* | *«Eso se lo anoto tal cual para el equipo»* — y sigue con su pregunta |
| 2 | *Sí he tenido escalofríos estos días.* | *«Los escalofríos son importantes»* → pide la temperatura · pasa a **amarillo** |
| 3 | *No me la tomé.* | Lo acepta como respuesta y avanza |
| 4 | *La herida la veo hinchada y con líquido.* | **ROJO** · *«sospecha de infección de sitio operatorio»* · avisa al equipo **y sigue preguntando** |
| 5 | *Un ocho de dolor.* | Segundo motivo rojo: dolor 8/10 |
| 6 | *Camino con dificultad.* | Anota y ofrece cerrar |
| 7 | *No, nada más.* | Cierre rojo + *«lo que me comentó también queda anotado»* |

**Los tres momentos que hay que señalar:**

1. **Turno 1** — *«El paciente no vino a contestar mi cuestionario. Vino con su
   propia preocupación, y queda registrada con sus palabras.»*
2. **Turno 4** — el semáforo en rojo. *«Nadie dijo la palabra infección. Lo dedujo
   una regla en código: fiebre referida sin medir junto a un hallazgo en la
   herida. Eso es lo que un modelo de lenguaje no debería estar decidiendo.»*
3. **Turno 4, otra vez** — *«Y no colgó. Una enfermera que detecta una bandera
   roja no cuelga: completa la valoración para que quien recibe la alerta tenga
   el cuadro entero.»*

**Cerrá mostrando el resumen**: semáforo, los tres motivos, el cuadro clínico, la
inquietud del paciente textual, y el costo de la llamada.

---

## Pregunta 1 — frente a cámara (1:30)

> *Si debes convencer a un cliente de que adopte el agente que construiste, ¿cómo
> presentarías el problema que resuelve, por qué tu solución es la adecuada y qué
> valor diferencial ofrece frente a otras alternativas?*

**Empezá por el paciente, no por la tecnología.**

**El problema.** Después de una cirugía, los días críticos son los primeros. Una
infección de sitio operatorio empieza con señales que el paciente no sabe leer:
un poco de fiebre, la herida más roja, algo de líquido. El seguimiento hoy
depende de que el paciente llame —y el que peor está es el que menos llama,
porque aguanta. Eso no es que informe mal: es que aguanta.

**La solución.** Centinela llama, conversa en el español que la gente habla de
verdad, y decide con reglas clínicas si el caso hay que verlo hoy. En 12 de 12
casos rojos del dataset lo detectó, sobre la conversación completa y no sobre
datos limpios.

**El valor diferencial** —y acá está el argumento fuerte—: **el comportamiento
clínico no depende de que el modelo de lenguaje sea inteligente.** Toda decisión
sobre la salud del paciente vive en código auditable. El modelo solo entiende
cómo habla la gente, corre fuera del camino crítico y **no puede introducir un
dato que el paciente no dijo**: su salida se valida contra el texto crudo.

Cerrá con esto: *«Si mañana cambia el modelo, la conducta clínica no cambia. Eso
es lo que un servicio de salud necesita poder auditar, y es lo que una solución
basada en prompts no le puede ofrecer.»*

---

## Pregunta 2 — frente a cámara (2:30)

> *Elige la decisión técnica más relevante: ¿qué alternativas evaluaste?, ¿por qué
> las descartaste?, ¿qué riesgos identificaste?, y si tuvieras dos semanas más,
> ¿qué cambiarías y por qué?*

**La decisión:** sacar el modelo de lenguaje de toda decisión clínica.

**No la tomé de entrada.** Cada responsabilidad bajó al código después de una
falla concreta y medida. Contá dos, que valen más que la lista entera:

- El 3B **inventó `dolor_torácico`** en turnos donde se hablaba de fiebre y de la
  herida, solo porque el síntoma estaba nombrado en el prompt.
- Extrajo **`fiebre_c=38` de un texto sin ninguna cifra**. La paciente había
  dicho 34.

**Las alternativas, con el número que las descarta** (esto es lo que casi nadie
va a traer):

| Alternativa | Por qué se descartó |
|---|---|
| Confiar la clasificación a un buen prompt | Las siete fallas medidas. Un prompt no es un contrato |
| BGE-M3 como embeddings, el sugerido por el reto | 1,5 fragmentos/s = **72 minutos** de indexación. Imposible bajo G2 |
| Voz `es_AR-daniela`, más natural | Factor de tiempo real **0,99**: al borde de entrecortarse |
| Un umbral único de similitud | Las consultas respondibles (0,853–0,927) **se superponen** con las clínicamente ausentes (0,871–0,891). Ningún umbral las separa |
| Reranker cruzado | +300 % de latencia y **0 de 7** consultas legítimas aprobadas. Acierta peor, no solo cuesta más |

**El beneficio inesperado** —contalo, porque es elegante—: la misma decisión que
protegía contra la alucinación resolvió la latencia. Como la decisión no depende
del modelo, el modelo corre en segundo plano mientras el paciente escucha. De
**19 segundos a 4,2 de P50**.

**Los riesgos.** El paciente que minimiza —y que es justamente el que está peor:
mediana de 6 marcadores en los rojos contra 2 en los verdes—. Whisper
transcribiendo mal las cifras. La inyección de prompt por los documentos que se
suben en caliente. Y uno que encontré en la auditoría pre-entrega: el nombre del
archivo subido iba directo a la ruta de destino, así que se podía escribir fuera
de la carpeta. Corregido, con 19 pruebas.

**Con dos semanas más:**

1. **Usuario sin privilegios en el contenedor.** Está identificado y escrito; no
   lo apliqué porque obliga a volver a medir G2 y no quise tocar el arranque
   evaluado sin poder verificarlo.
2. **Traducción completa del corpus**, no solo de la consulta.
3. **Política de retención de los registros**, que hoy guardan el habla del
   paciente sin cifrar.

Cerrá con la frase que resume el proyecto: *«Ninguna cifra de este informe es una
estimación. Cada corrección nació de una falla observada, y cada una dejó una
prueba de regresión que la fija.»*
