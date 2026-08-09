"""Extracción estructurada del cuadro clínico desde el habla del paciente.

Esta es la pieza difícil. El motor de escalamiento ya alcanza recall 12/12 sobre
los 160 casos cuando recibe un cuadro clínico correcto (docs/baseline-triaje.md);
todo lo que se pierda de acá en adelante es error de extracción.

El paciente no habla como una tabla clínica. Dice "me arde aquí abajito" y "tuve
como un frío raro anoche". El dataset trae cinco estilos y el más peligroso está
sobre-representado: 928 turnos de `minimizador_sintomas`, que responde "no, nada,
todo bien" mientras tiene 38.5 de fiebre.

Principio: el modelo SOLO extrae lo que el paciente dijo. No decide, no tranquiliza
y no interpreta gravedad. Lo que no fue dicho vuelve como `null`, nunca como un
valor normal inventado — esa distinción es la que dispara la repregunta y evita el
falso negativo.
"""

from __future__ import annotations

import json

from src.clinico import alarmas
from src.clinico import fiebre as fiebre_deteccion
from src.clinico.escalamiento import CuadroClinico, Herida, Movilidad
from src.modelo.cliente import ClienteLocal, Respuesta

# Esquema forzado por Ollama. Los null son parte del contrato, no un descuido:
# "no lo dijo" y "lo dijo y está normal" son estados clínicamente distintos.
ESQUEMA = {
    "type": "object",
    "properties": {
        "dolor_nrs": {"type": ["integer", "null"], "minimum": 0, "maximum": 10},
        "fiebre_c": {"type": ["number", "null"], "minimum": 34, "maximum": 43},
        "menciona_fiebre_sin_medir": {"type": "boolean"},
        "herida": {
            "type": "string",
            "enum": ["normal", "eritema_leve", "secrecion_purulenta", "desconocido"],
        },
        "movilidad": {
            "type": "string",
            "enum": ["normal", "limitada_esperada", "incapacitante_nueva", "desconocido"],
        },
        # Enum en el ESQUEMA, no en el prompt. Con la lista solo en el system
        # prompt, el 3B inventó "dolor_toracico" en un turno donde el paciente
        # describía secreción en la herida, y "desconocido" como si fuera un
        # síntoma. Un síntoma de alarma inventado dispara escalamientos por el
        # motivo equivocado: el semáforo acierta por accidente y el registro
        # clínico queda falseado.
        "sintomas_alarma": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "dificultad_respiratoria",
                    "dolor_toracico",
                    "sangrado_activo",
                    "vomito_persistente",
                    "desorientacion",
                    "sincope",
                    "dehiscencia_herida",
                ],
            },
        },
        "evasivo": {"type": "boolean"},
    },
    "required": [
        "dolor_nrs",
        "fiebre_c",
        "menciona_fiebre_sin_medir",
        "herida",
        "movilidad",
        "sintomas_alarma",
        "evasivo",
    ],
}

SISTEMA = """Extraes datos clínicos de lo que dice un paciente posoperatorio colombiano.

REGLAS ABSOLUTAS:
- Extraes SOLO lo que el paciente dijo. No infieres, no completas, no tranquilizas.
- Si no lo mencionó, va null o "desconocido". NUNCA asumas que algo está normal.
- El paciente usa lenguaje coloquial y regionalismos. "Harto"/"un resto" = mucho.
  "Aguantable" = leve. "Me arde", "me late", "me punza" describen dolor.
- Si dice que tuvo fiebre o escalofríos pero no dio número, deja fiebre_c en null
  y pon menciona_fiebre_sin_medir en true.
- evasivo es true si esquiva la pregunta, minimiza o cambia de tema.

CÓMO DESCRIBE LA HERIDA UN PACIENTE (casi nunca usa términos médicos):
- secrecion_purulenta: "líquido amarillo", "le sale algo", "está supurando",
  "un líquido verdoso", "pus", "mancha la gasa", "huele feo", "sale materia".
  Cualquier salida de líquido que no sea sangre clara va acá.
- eritema_leve: "está roja", "rosadita", "coloradita alrededor", "un poco
  hinchada", "caliente al tacto".
- normal: solo si dice explícitamente que la vio y está bien.
- desconocido: si no la mencionó o no la ha mirado.

sintomas_alarma: lista solo los que el paciente describa. Si no describe ninguno,
devuelve una lista vacía []. Nunca inventes un síntoma que no dijo."""

# Escala verbal → NRS. El paciente casi nunca da un número; da una palabra.
ESCALA_VERBAL = {
    "sin dolor": 0,
    "leve": 2,
    "aguantable": 3,
    "moderado": 5,
    "fuerte": 7,
    "muy fuerte": 8,
    "insoportable": 10,
}


class ResultadoExtraccion:
    def __init__(self, cuadro: CuadroClinico, crudo: dict, respuesta: Respuesta) -> None:
        self.cuadro = cuadro
        self.crudo = crudo
        self.respuesta = respuesta

    @property
    def evasivo(self) -> bool:
        return bool(self.crudo.get("evasivo", False))

    @property
    def fiebre_sin_medir(self) -> bool:
        """El paciente dijo tener fiebre pero no la midió.

        Clínicamente NO es lo mismo que no tener fiebre: es un dato faltante que
        obliga a repreguntar o a tratar el caso con mayor cautela.
        """
        return bool(self.crudo.get("menciona_fiebre_sin_medir", False))


def _fusionar(previo: CuadroClinico, nuevo: dict) -> CuadroClinico:
    """Acumula lo averiguado a lo largo de la llamada.

    Un dato ya obtenido no se pierde porque el paciente no lo repita en el turno
    siguiente. Y un valor confirmado no vuelve a 'desconocido': solo se actualiza
    si el paciente aporta información nueva.
    """
    dolor = nuevo.get("dolor_nrs")
    fiebre = nuevo.get("fiebre_c")

    herida = Herida(nuevo.get("herida", "desconocido"))
    if herida is Herida.DESCONOCIDO:
        herida = previo.herida

    movilidad = Movilidad(nuevo.get("movilidad", "desconocido"))
    if movilidad is Movilidad.DESCONOCIDO:
        movilidad = previo.movilidad

    alarmas = list(dict.fromkeys(previo.sintomas_alarma + nuevo.get("sintomas_alarma", [])))

    # La sospecha de fiebre sin medir se conserva hasta que llegue una cifra:
    # es lo que hace que el agente pida el número en vez de seguir de largo.
    fiebre_final = fiebre if fiebre is not None else previo.fiebre_c
    sin_medir = bool(nuevo.get("menciona_fiebre_sin_medir")) or previo.fiebre_referida_sin_medir

    return CuadroClinico(
        dolor_nrs=dolor if dolor is not None else previo.dolor_nrs,
        fiebre_c=fiebre_final,
        herida=herida,
        movilidad=movilidad,
        sintomas_alarma=alarmas,
        fiebre_referida_sin_medir=sin_medir and fiebre_final is None,
        # Se arrastra explícitamente: la fusión construye un cuadro NUEVO, y
        # todo campo que no se copie acá se pierde en cada extracción. Los
        # marcadores de minimización se acumulan durante toda la llamada y
        # quedaban en cero, dejando la regla sin efecto.
        marcadores_minimizacion=previo.marcadores_minimizacion,
    )


def _esquema_enfocado(foco: str | None) -> dict:
    """Recorta el esquema a lo que el agente realmente preguntó.

    Medido: con el esquema completo, el 3B rellena campos que nadie mencionó.
    Ante "me duele harto" inventó secreción purulenta y dolor torácico — porque
    el vocabulario de heridas estaba en el prompt y el modelo lo copió en lugar
    de leer al paciente.

    Un modelo pequeño no puede ignorar un campo que se le ofrece. La solución no
    es insistir en el prompt: es no ofrecerle el campo. El agente conduce la
    conversación, así que siempre sabe sobre qué preguntó.
    """
    if foco is None:
        return ESQUEMA

    campos_por_foco = {
        "dolor": ["dolor_nrs"],
        "fiebre": ["fiebre_c", "menciona_fiebre_sin_medir"],
        "herida": ["herida"],
        "movilidad": ["movilidad"],
    }
    campos = campos_por_foco.get(foco)
    if not campos:
        return ESQUEMA

    # `evasivo` viaja siempre: detectar evasión es independiente del tema y el
    # modelo lo hace bien. `sintomas_alarma` NO: se detecta en código
    # (src/clinico/alarmas.py) porque el 3B copiaba la lista del prompt.
    campos = [*campos, "evasivo"]
    return {
        "type": "object",
        "properties": {c: ESQUEMA["properties"][c] for c in campos},
        "required": campos,
    }


def _instrucciones(foco: str | None) -> str:
    """Instrucción específica del foco, sin vocabulario de otros temas.

    Incluir el vocabulario de heridas cuando se pregunta por dolor es lo que
    inducía la alucinación: el modelo copiaba los ejemplos del prompt.
    """
    guias = {
        "dolor": (
            "Extrae SOLO la intensidad del dolor en escala 0-10.\n"
            'Lenguaje coloquial: "harto"/"un resto"/"casi no aguanto" = 8, "fuerte" = 7, '
            '"moderado"/"más o menos" = 5, "aguantable"/"poquito" = 3.\n'
            "dolor_nrs = 0 SOLO si dice explícitamente que no le duele nada.\n"
            'CUIDADO: "todo bien", "normal", "no se preocupe" NO son un 0. Son respuestas '
            "genéricas que no hablan del dolor: dolor_nrs = null y evasivo = true."
        ),
        "fiebre": (
            "Extrae SOLO la temperatura en grados Celsius.\n"
            "Si dice CUALQUIER número, extraelo aunque dude: "
            '"creo que como 38" = 38, "38 y algo" = 38, "casi 39" = 38.9.\n'
            "menciona_fiebre_sin_medir = true cuando describe sensación febril SIN número: "
            '"me sentí caliente", "tuve escalofríos", "sentí un frío raro", "destemplado", '
            '"me dio como fiebre", "sudé mucho en la noche".\n'
            "Si no habla de fiebre ni de frío ni de calentura: null y false."
        ),
        "herida": (
            "Extrae SOLO el estado de la herida.\n"
            "- secrecion_purulenta: cualquier líquido que no sea sangre clara — "
            '"líquido amarillo", "le sale algo", "supura", "mancha la gasa", "huele feo".\n'
            '- eritema_leve: "roja", "coloradita", "hinchada", "caliente al tacto".\n'
            "- normal: solo si dice que la vio y está bien.\n"
            "- desconocido: si no la mencionó o no la ha mirado."
        ),
        "movilidad": (
            "Extrae SOLO la movilidad.\n"
            "- incapacitante_nueva: no puede caminar o moverse como antes.\n"
            "- limitada_esperada: se mueve con dificultad propia del posoperatorio.\n"
            "- normal: camina y se mueve sin problema.\n"
            "- desconocido: si no lo mencionó."
        ),
    }
    base = guias.get(foco or "", "Extrae los datos clínicos que el paciente mencionó.")
    # Deliberadamente NO se enumeran síntomas de alarma acá: nombrarlos hacía que
    # el modelo los copiara. Los detecta alarmas.detectar() sobre el texto crudo.
    return f"{base}\n\nevasivo: true si esquiva la pregunta, minimiza o cambia de tema."


def extraer(
    cliente: ClienteLocal,
    texto_paciente: str,
    *,
    previo: CuadroClinico | None = None,
    pregunta_agente: str | None = None,
    foco: str | None = None,
) -> ResultadoExtraccion:
    """Extrae el cuadro clínico de un turno del paciente.

    `pregunta_agente` da contexto: "¿tuvo fiebre?" seguido de "sí, un poquito"
    solo es interpretable sabiendo qué se preguntó.

    `foco` recorta el esquema al tema preguntado ("dolor", "fiebre", "herida",
    "movilidad"). Reduce alucinación y tokens generados a la vez.
    """
    contexto = f"El agente preguntó: {pregunta_agente}\n" if pregunta_agente else ""
    prompt = (
        f"{contexto}El paciente respondió: {texto_paciente}\n\n{_instrucciones(foco)}"
    )

    respuesta = cliente.generar(
        prompt, sistema=SISTEMA, esquema=_esquema_enfocado(foco), max_tokens=150
    )

    try:
        crudo = json.loads(respuesta.texto)
    except json.JSONDecodeError:
        # El esquema forzado lo vuelve improbable, pero un fallo de extracción no
        # puede degradar en un cuadro "normal" inventado: se devuelve desconocido.
        crudo = {}

    # VALIDACIÓN CONTRA EL TEXTO CRUDO: el modelo no puede introducir un número
    # que el paciente nunca dijo. Medido: ante "me sentí muy afiebrada" —sin
    # cifra— el 3B extrajo fiebre_c=38, un valor inventado que quedó mandando
    # sobre la corrección posterior del paciente y disparó un escalamiento con
    # motivo falso. Si el texto no trae cifra, la sospecha queda como lo que es.
    if crudo.get("fiebre_c") is not None and not fiebre_deteccion.tiene_cifra(
        texto_paciente
    ):
        crudo["fiebre_c"] = None
        crudo["menciona_fiebre_sin_medir"] = True

    # Los síntomas de alarma NO los produce el modelo: se detectan sobre el texto
    # crudo del paciente. Si el modelo cayera por completo, esta señal sobrevive.
    crudo["sintomas_alarma"] = alarmas.detectar(texto_paciente)

    cuadro = _fusionar(previo or CuadroClinico(), crudo)
    return ResultadoExtraccion(cuadro, crudo, respuesta)
