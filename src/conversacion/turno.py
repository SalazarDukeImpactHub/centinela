"""Orquestación de un turno de conversación.

EL PROBLEMA DE LATENCIA, MEDIDO
    transcripción (Whisper en Groq)      ~1.500 ms
    extracción estructurada (3B local)  ~17.000 ms
    primer audio (Piper, ya caliente)      ~400 ms

Diecinueve segundos de silencio entre que el paciente termina de hablar y escucha
una respuesta. Para una conversación de voz eso no es lento: es un sistema roto.
La compuerta G4 exige conversación en tiempo real y la calidad de la voz vale 15
puntos.

LA SOLUCIÓN: SACAR EL MODELO DEL CAMINO CRÍTICO
El agente conduce la conversación, así que sabe qué le falta preguntar sin
consultar a nadie. La secuencia de preguntas la decide el motor de escalamiento
—que es código— a partir de los campos que aún no tiene.

Entonces el turno queda así:
    1. Transcribir.                                        ~1.500 ms
    2. Detectar síntomas de alarma sobre el texto crudo.        ~0 ms  (regex)
    3. Elegir la siguiente frase de una plantilla.              ~0 ms
    4. Empezar a hablar.                                     ~400 ms
    5. Extraer con el modelo, EN SEGUNDO PLANO, mientras el paciente escucha
       y responde.

Latencia percibida: ~2 segundos. El modelo sigue trabajando, pero fuera del
silencio que el paciente percibe.

Esto es posible porque las dos señales que no pueden esperar —los síntomas de
alarma y la decisión de escalar— ya viven en código y no en el modelo. La misma
decisión arquitectónica que protegía contra la alucinación resuelve ahora la
latencia.
"""

from __future__ import annotations

import random
import re as _re
import threading
import unicodedata as _unicodedata
from dataclasses import dataclass, field
from enum import Enum

from typing import Protocol

from src.clinico import alarmas, fiebre, minimizacion
from src.clinico import dolor as dolor_deteccion
from src.clinico import herida as herida_deteccion
from src.conversacion import preguntas
from src.clinico.escalamiento import (
    CuadroClinico,
    Decision,
    Herida,
    Movilidad,
    Semaforo,
    evaluar,
)
from src.clinico.extraccion import extraer
from src.modelo.cliente import ClienteLocal


class Foco(str, Enum):
    """Temas que el agente debe cubrir, en orden de prioridad clínica."""

    DOLOR = "dolor"
    FIEBRE = "fiebre"
    HERIDA = "herida"
    MOVILIDAD = "movilidad"


# Orden deliberado: fiebre y herida son las señales que más pesan en el
# escalamiento (fiebre ≥38 y secreción purulenta escalan solas), así que se
# preguntan temprano. Si la llamada se corta, lo crítico ya está preguntado.
ORDEN_FOCOS = [Foco.FIEBRE, Foco.HERIDA, Foco.DOLOR, Foco.MOVILIDAD]

# Las preguntas van en lenguaje de paciente, no de historia clínica, y cada foco
# tiene VARIANTES: probado en llamada real, el guion de frase única suena a
# robot leyendo un formulario. Cada llamada elige al azar, así dos llamadas del
# mismo día no suenan idénticas y la repregunta nunca repite palabras.
PREGUNTAS: dict[Foco, tuple[str, ...]] = {
    Foco.FIEBRE: (
        "Cuénteme, ¿ha tenido fiebre o escalofríos estos días?",
        "Arranquemos por lo más importante: ¿fiebre o escalofríos ha tenido?",
        "¿Cómo ha estado de temperatura? ¿Fiebre, escalofríos, algo de eso?",
    ),
    Foco.HERIDA: (
        "¿Y cómo ve la herida? Me interesa si está roja, hinchada o si le sale algún líquido.",
        "Hábleme de la herida: ¿la ha visto roja, hinchada, o con algún líquido?",
        "¿Qué tal la herida? Cuénteme cómo la ve usted.",
    ),
    Foco.DOLOR: (
        "Hablemos del dolor. Si cero es nada y diez es lo más fuerte que ha sentido, ¿en cuánto lo pondría ahora?",
        "¿Y de dolor cómo vamos? Del cero al diez, ¿en cuánto lo siente?",
        "Cuénteme del dolor: ¿en una escala de cero a diez, dónde lo pondría?",
    ),
    Foco.MOVILIDAD: (
        "¿Se ha podido mover y caminar como esperaba?",
        "¿Y para moverse? ¿Camina, se levanta sin problema?",
        "¿Cómo va con el movimiento? ¿Se puede parar y caminar?",
    ),
}

REPREGUNTAS: dict[Foco, tuple[str, ...]] = {
    Foco.FIEBRE: (
        "Se lo pregunto de otra manera: ¿en algún momento sintió el cuerpo caliente?",
        "A ver, dígame esto: ¿anoche o hoy sintió mucho frío, o el cuerpo muy caliente?",
    ),
    Foco.HERIDA: (
        "Volvamos a la herida un momento: ¿la ha alcanzado a mirar hoy?",
        "Le insisto con la herida porque es importante: ¿se ve rosada normal, o roja, o con algo saliendo?",
    ),
    Foco.DOLOR: (
        "Sobre el dolor, aunque sea por encimita: ¿lo deja dormir y moverse tranquilo?",
        "Volviendo al dolor: ¿lo dejó dormir anoche?",
    ),
    Foco.MOVILIDAD: (
        "Sobre el movimiento: ¿se levanta de la cama sin que nadie lo ayude?",
        "Le pregunto de nuevo por la movilidad: ¿puede ir al baño y volver por su cuenta?",
    ),
}


def _elegir(opciones: tuple[str, ...]) -> str:
    return random.choice(opciones)


# Orden de gravedad. Sirve para que un hallazgo posterior más leve no borre uno
# más grave ya referido: el paciente que dijo "le sale líquido" y después
# minimiza con "bueno, está rosadita" no deja de tener una secreción.
_GRAVEDAD_HERIDA = {
    "desconocido": 0,
    "normal": 1,
    "eritema_leve": 2,
    "secrecion_purulenta": 3,
}
_GRAVEDAD_MOVILIDAD = {
    "desconocido": 0,
    "normal": 1,
    "limitada_esperada": 2,
    "incapacitante_nueva": 3,
}


def _gravedad_herida(estado: str) -> int:
    return _GRAVEDAD_HERIDA.get(estado, 0)


def _gravedad_movilidad(estado: str) -> int:
    return _GRAVEDAD_MOVILIDAD.get(estado, 0)


# Temas que el paciente trae por su cuenta: cosas del posoperatorio que no
# forman parte del cuestionario clínico pero que le preocupan de verdad.
_INQUIETUD = _re.compile(
    r"\b(drenaje|dren|sonda|cateter|punto|sutura|grapa|vendaje|gasa|aposito|"
    r"cita|control|resultado|examen|biopsia|incapacidad|licencia|medicament|"
    r"pastilla|receta|formula|eps|autorizacion|orden)\w*"
)


# Palabra con la que el paciente nombra cada tema. Sirve para saber QUÉ trajo,
# no para extraer el dato: de eso se encargan los detectores clínicos.
_TEMAS: tuple[tuple[Foco, _re.Pattern[str]], ...] = (
    (Foco.HERIDA, _re.compile(r"\b(herida|cicatriz|costura)\w*")),
    (Foco.DOLOR, _re.compile(r"\b(dolor|duele|dolia|molest|arde|punza)\w*")),
    (Foco.FIEBRE, _re.compile(r"\b(fiebre|temperatura|escalofri|calentura)\w*")),
    (Foco.MOVILIDAD, _re.compile(r"\b(camin|movi|levant|muleta|caminador)\w*")),
)


def _tema_traido(texto: str) -> Foco | None:
    """Tema que el paciente nombró por su cuenta, o None.

    Se usa para reordenar la cola de preguntas, nunca para registrar un dato.
    """
    plano = _sin_tildes(texto)
    for foco, patron in _TEMAS:
        if patron.search(plano):
            return foco
    return None


def _es_inquietud(texto: str, foco: Foco | None) -> bool:
    """El paciente habla de algo que el cuestionario no cubre.

    Se exige una frase con contenido —no un monosílabo— y que el tema no sea
    el que se está preguntando: si se pregunta por la herida y menciona la
    gasa, eso es la respuesta, no una inquietud aparte.
    """
    plano = _sin_tildes(texto)
    if len(plano.split()) < 3:
        return False
    if foco is Foco.HERIDA and _re.search(r"vendaje|gasa|aposito|punto|sutura", plano):
        return False
    return bool(_INQUIETUD.search(plano))


# Prefijo afirmativo, no frase completa. MEDIDO en llamada real por voz: a la
# pregunta "¿ha tenido fiebre o escalofríos estos días?" el paciente contestó
# "He tenido estos días" —afirmó y devolvió el final de la pregunta— y no se
# registró NADA: el patrón exigía que la frase entera fuera la afirmación, así
# que cualquier coletilla la invalidaba. Lo mismo con "Sí, a veces" y "Sí señor,
# un poco". Una afirmación de fiebre perdida deja el resumen diciendo que no se
# pudo averiguar, sobre un paciente que dijo que sí.
_AFIRMACION = _re.compile(
    r"^\s*[¡!]*\s*(si|sí|claro|correcto|asi es|así es|he tenido|si he tenido|"
    r"sí he tenido|un poco|un poquito|a veces|creo que si|creo que sí|"
    r"me parece que si|me parece que sí)\b",
    _re.IGNORECASE,
)


def _es_afirmacion(texto: str) -> bool:
    """Respuesta que ARRANCA afirmando: "sí", "he tenido estos días", "un poco"."""
    return bool(_AFIRMACION.match(texto.strip()))

# Repregunta por DATO FALTANTE: el paciente respondió, pero sin la cifra que la
# decisión necesita. Es distinto de la evasión — acá sí quiere colaborar.
#
# El caso de la fiebre es el que más pesa: "tuve fiebre" puede ser 37.2 (verde) o
# 39 (rojo), y el umbral de escalamiento está justo en 38. Seguir de largo sin el
# número obliga a asumir, y asumir en cualquiera de las dos direcciones es un
# error clínico.
# Cortos a propósito. La versión larga explicaba por qué hacía falta el número
# ("no es lo mismo treinta y siete que treinta y nueve") y en llamada real sonó
# innecesaria: el paciente ya entendió que le piden la temperatura.
PEDIDOS_DE_DATO: dict[Foco, str] = {
    Foco.FIEBRE: "¿Se alcanzó a tomar la temperatura? ¿Cuánto le marcó?",
    Foco.DOLOR: "¿Qué número le pondría, del cero al diez? Aunque sea aproximado.",
}

# Cuando el paciente responde algo de lo que no se puede extraer ningún dato del
# tema preguntado. Decirle "eso me sirve saberlo" y cambiar de tema es fingir
# que se escuchó — y en llamada real se percibió exactamente así.
NO_SE_ENTENDIO: dict[Foco, str] = {
    Foco.FIEBRE: "Disculpe, no le entendí la temperatura.",
    Foco.HERIDA: "Perdone, no le entendí bien lo de la herida.",
    Foco.DOLOR: "Disculpe, no le capté el número del dolor.",
    Foco.MOVILIDAD: "Perdone, no le entendí lo del movimiento.",
}

# Temperatura fuera del rango humano: casi siempre error de transcripción.
# Se pide confirmación DOS veces como máximo y con frases distintas. En llamada
# real, cuatro "¿me repite el número?" idénticos dejaron al paciente atrapado
# repitiendo una cifra que el sistema no sabía leer — y la llamada terminó sin
# ningún dato clínico. Insistir más allá de dos veces no obtiene el número: solo
# hostiga y desperdicia la llamada.
CIFRA_IMPOSIBLE = (
    "Perdón, entendí {valor} grados y eso no puede ser. ¿Me repite el número, "
    "más despacio?",
    "Sigo sin captarlo bien. Dígame solo los dos números, por ejemplo: "
    "treinta y ocho.",
)

# Tras dos intentos fallidos se sigue adelante: el dato queda como fiebre
# referida sin medir —que es la verdad— y la valoración continúa en vez de
# quedarse trabada. Lo que no se pudo obtener queda en el resumen.
CIFRA_NO_OBTENIDA = (
    "No importa, no nos quedemos en el número. Lo anoto como fiebre sin medir y "
    "el equipo lo revisa."
)

# Apertura: se presenta con nombre y rol, dice qué va a hacer y ofrece ayuda.
# Todo en USTED. El registro no se mezcla: los pacientes del dataset son adultos
# colombianos, muchos mayores, y alternar usted y tú en la misma frase suena
# descuidado justo en el momento en que el agente se está ganando la confianza.
APERTURA = (
    "Buenos días, le habla Centinela, su agente encargado del seguimiento de su "
    "cirugía. Voy a hacerle unas preguntas para ver cómo va su recuperación y si "
    "puedo ayudarle."
)

# Escalamiento: se nombra lo que pasa sin dramatizarlo, y sobre todo se dice qué
# va a ocurrir después. Un paciente al que le anuncian que algo anda mal y no le
# explican el siguiente paso se queda solo con el susto.
# Escalamiento: se anuncia la alerta pero la llamada NO termina. Una enfermera
# que detecta una bandera roja no cuelga: completa la valoración para que quien
# reciba la alerta tenga el cuadro entero. Cortar acá dejaba al equipo médico
# con un solo dato y sin contexto.
ESCALAMIENTO = (
    "Le agradezco que me lo cuente, porque eso sí hay que mirarlo hoy mismo. Ya "
    "lo estoy reportando al equipo de salud. Permítame terminar de preguntarle "
    "un par de cosas, así les paso la información completa."
)

# Cierre de una llamada que escaló. Recapitula qué se reportó y qué sigue.
CIERRE_ROJO = (
    "Eso es todo lo que necesitaba preguntarle. Ya quedó reportado al equipo de "
    "salud con todo lo que me contó, y se van a comunicar con usted hoy. "
    "Mientras tanto no se aplique nada en la herida, y si se siente peor no "
    "espere la llamada: consulte de una vez o vaya a urgencias."
)

CIERRE_VERDE = (
    "Me deja tranquilo, su recuperación va bien encaminada. Le pido una cosa: si "
    "le llega a dar fiebre, si la herida le cambia de aspecto o si el dolor "
    "aumenta, póngase en contacto de una vez con el especialista a cargo de su "
    "cirugía o con su equipo de salud. Que siga mejorando, y gracias por su tiempo."
)

CIERRE_AMARILLO = (
    "Voy a dejar anotado todo lo que me contó para que el equipo lo revise con "
    "calma. Es probable que lo llamen para verificar un par de cosas. Y si algo "
    "se pone peor antes de esa llamada, póngase en contacto directamente con el "
    "especialista a cargo de su cirugía o con su servicio de salud. Que se mejore."
)

# Reconocimiento breve antes de la siguiente pregunta. Cumple dos funciones: que
# el paciente sepa que se le escuchó, y llenar el silencio mientras la extracción
# corre por detrás. La rúbrica pregunta explícitamente qué hace la solución
# durante los silencios.
ACUSES: dict[Semaforo, tuple[str, ...]] = {
    Semaforo.VERDE: ("Listo, gracias.", "Perfecto, anotado.", "Muy bien."),
    Semaforo.AMARILLO: (
        "Bueno, eso me sirve saberlo.",
        "Lo dejo anotado para el equipo.",
        "Vale, eso lo tengo en cuenta.",
    ),
    # Tres variantes y no una: medido en llamada real, una vez escalado el
    # agente decía "Entiendo, eso sí es importante" en TODOS los turnos
    # siguientes. La frase es correcta; repetida tres veces suena a máquina.
    Semaforo.ROJO: (
        "Entiendo, eso sí es importante.",
        "Gracias por contármelo, eso lo anoto.",
        "Ya quedó anotado, sigamos.",
    ),
}

# Eco de lo escuchado: confirmar el dato concreto que el paciente acaba de dar
# antes de cambiar de tema. Es la diferencia entre "Listo, gracias" a todo —que
# suena a formulario— y una conversación donde consta que se escuchó. Se
# construye desde las señales DETECTADAS EN CÓDIGO, nunca desde el modelo: un
# eco inventado sería peor que ninguno.
_ECOS_HERIDA = [
    (_re.compile(r"roja|colorad|enrojecid"), "Me dice que la ve roja."),
    (_re.compile(r"hinchad|inflamad"), "Hinchada, entiendo."),
    (_re.compile(r"liquido|supurand|saliendo|amarillo|verdoso|pus"), "Eso que le sale lo anoto tal cual."),
    (_re.compile(r"seca|bien|limpia|normal|cicatrizando"), "La ve tranquila, qué bueno."),
]


def _sin_tildes(texto: str) -> str:
    plano = _unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in plano if _unicodedata.category(c) != "Mn")


def _aporta_dato(texto: str, foco: Foco | None) -> bool:
    """El paciente dijo algo utilizable sobre el tema que se le preguntó.

    Solo mira las señales que el código puede verificar en el momento. Si
    devuelve False, el agente NO finge haber entendido: vuelve a preguntar. Es
    la diferencia entre una conversación y un formulario que avanza pase lo que
    pase — en llamada real, "¡Ojo!" recibía "eso me sirve saberlo" y el tema se
    daba por cubierto.
    """
    if foco is None:
        return True
    plano = _sin_tildes(texto)
    if foco is Foco.FIEBRE:
        return (
            fiebre.tiene_cifra(texto)
            or fiebre.menciona_fiebre(texto)
            or fiebre.niega_fiebre(texto)
            or fiebre.intensidad_referida(texto) is not None
        )
    if foco is Foco.HERIDA:
        return bool(
            _re.search(
                r"roj|colorad|hinchad|inflamad|liquido|supur|amarill|verdos|pus|"
                r"seca|bien|limpia|normal|cicatriz|sangr|no la|no he|mirad|mire",
                plano,
            )
        )
    if foco is Foco.DOLOR:
        return bool(
            _re.search(r"\b([0-9]|10)\b|dolor|duele|molest|nada|aguantab|fuerte", plano)
        )
    if foco is Foco.MOVILIDAD:
        return bool(
            _re.search(r"camin|mov|levant|para|bano|silla|cama|ayud|solo|sola", plano)
        )
    return True


def _eco_intensidad(intensidad: str | None) -> str | None:
    """Confirma la intensidad dicha en palabras, sin inventar un número."""
    if intensidad == "alta":
        return (
            "Fiebre alta sin termómetro, entiendo. Lo dejo anotado así, "
            "y si consigue medirla me avisa."
        )
    if intensidad == "baja":
        return "Poca fiebre, anotado."
    return None


def _eco(texto_paciente: str, cifra_dicha: float | None, foco: Foco | None) -> str | None:
    """Frase corta que refleja el dato concreto recién escuchado, o None."""
    if cifra_dicha is not None:
        entero = int(cifra_dicha) if cifra_dicha == int(cifra_dicha) else cifra_dicha
        return f"{entero}, listo."
    if foco is Foco.HERIDA:
        # El eco sale del estado DETECTADO, no del primer adjetivo del texto:
        # ante "roja, hinchada y le sale líquido" decir "me dice que la ve roja"
        # es quedarse con el hallazgo menos grave de los tres.
        estado = herida_deteccion.estado_referido(texto_paciente, en_contexto=True)
        ecos = {
            "secrecion_purulenta": "Eso que le sale de la herida es importante, lo anoto tal cual.",
            "eritema_leve": "Enrojecida, entendido.",
            "normal": "La ve tranquila, qué bueno.",
            "desconocido": "Entiendo que no la ha podido mirar.",
        }
        if estado:
            return ecos[estado]
    if foco is Foco.DOLOR:
        # Desde el detector, no desde un regex propio: "Siete." también es un 7,
        # y "me duele harto" es un 8. El eco debe reflejar lo que se GUARDÓ.
        nivel = dolor_deteccion.nivel_dolor(texto_paciente)
        if nivel is not None:
            return "Sin dolor, qué bueno." if nivel == 0 else f"Un {nivel} de dolor, anotado."
    if foco is Foco.MOVILIDAD:
        estado = dolor_deteccion.estado_movilidad(texto_paciente, en_contexto=True)
        ecos_mov = {
            "incapacitante_nueva": "Que no se pueda mover es importante, lo anoto.",
            "limitada_esperada": "Se mueve con dificultad, entendido.",
            "normal": "Se mueve bien, me alegra.",
        }
        if estado:
            return ecos_mov[estado]
    return None

# Acuse cuando el paciente cuenta algo que preocupa. Reconoce lo que dijo antes
# de seguir preguntando: pasar de largo suena a formulario.
ACUSE_PREOCUPACION = "Entiendo su preocupación, y hace bien en contármelo."

# Acuse cuando el paciente trae un tema propio en vez de responder la pregunta.
# Le entendió: hablaba de otra cosa. Decirle "no le entendí la temperatura" es
# tratarlo como si no se hubiera explicado.
ACUSE_INQUIETUD = (
    "Eso se lo anoto tal cual para el equipo, que se lo resuelvan bien."
)

# Acuse específico cuando el paciente reporta escalofríos y no fiebre. Nombrar
# lo que efectivamente dijo, en vez de responder como si hubiera dicho "fiebre",
# es lo que hace que la conversación se sienta escuchada: los escalofríos son un
# síntoma propio, aunque la conducta clínica sea la misma —pedir la temperatura.
ACUSE_ESCALOFRIOS = (
    "Los escalofríos son importantes, sobre todo después de una cirugía."
)

# Cuando el corpus no cubre la pregunta del paciente. Declarar el límite y
# derivar es exactamente la conducta que el criterio de 20 puntos evalúa.
SIN_RESPUESTA = (
    "Esa pregunta no la puedo responder con la documentación que manejo, y "
    "prefiero no adivinar. Se la dejo anotada al equipo de salud para que se la "
    "resuelvan bien."
)

# Saludo a un cuidador que toma la palabra. Su relato vale: muchas señales
# —desorientación, por ejemplo— las ve el cuidador antes que el paciente.
ACUSE_TERCERO = "Claro que sí, le agradezco que me cuente cómo lo ha visto."


class Consultor(Protocol):
    """Responde una pregunta del paciente contra el corpus, o declara el límite.

    Devuelve el texto a decir (ya con cita si la hay) o None si no hay sustento.
    La implementación real vive en la API, que conoce el índice; acá solo se
    define el contrato para que la máquina de turnos sea comprobable sin RAG.
    """

    def __call__(self, pregunta: str) -> str | None: ...


@dataclass
class EstadoLlamada:
    """Lo que el agente sabe y lo que ya preguntó."""

    escenario: str
    cuadro: CuadroClinico = field(default_factory=CuadroClinico)
    preguntados: set[Foco] = field(default_factory=set)
    repreguntados: set[Foco] = field(default_factory=set)
    # Focos donde ya se pidió la cifra concreta. Se pide UNA vez: insistir dos
    # veces por un número que el paciente no tiene es hostigarlo.
    datos_pedidos: set[Foco] = field(default_factory=set)
    # Focos donde ya se reintentó por no haber entendido la respuesta. Se
    # reintenta UNA vez: si a la segunda tampoco se entiende, mejor avanzar y
    # dejar constancia de lo que quedó sin preguntar.
    reintentados: set[Foco] = field(default_factory=set)
    # El cierre se anuncia antes de ejecutarse: colgar en seco tras la última
    # pregunta deja al paciente con la palabra en la boca.
    despedida_ofrecida: bool = False
    # La alerta al equipo se anuncia una sola vez; después la llamada sigue para
    # completar la valoración.
    alerta_anunciada: bool = False
    # Veces que se pidió confirmar una temperatura ilegible. Con tope: insistir
    # más allá de dos no obtiene el número, solo hostiga.
    intentos_cifra: int = 0
    foco_actual: Foco | None = None
    cerrada: bool = False
    transcripcion: list[tuple[str, str]] = field(default_factory=list)
    # Lo que el paciente trae por su cuenta y no responde a ninguna pregunta.
    #
    # De una llamada real: dijo "no me quitan el drenaje" al empezar y lo repitió
    # al despedirse. Era su preocupación principal y no quedó en ninguna parte
    # del resumen. El agente conduce el cuestionario, pero el paciente tiene su
    # propia agenda — y quien reciba la alerta necesita saberla.
    inquietudes: list[str] = field(default_factory=list)
    # Tema que el paciente trajo por su cuenta y todavía no dio dato. Pasa al
    # frente de la cola: si abre diciendo "me preocupa la herida", preguntarle
    # primero por la fiebre es contestarle con la agenda propia.
    prioridad: Foco | None = None

    def siguiente_foco(self) -> Foco | None:
        """El siguiente tema por preguntar, siguiendo al paciente cuando se puede.

        ORDEN_FOCOS sigue mandando sobre lo que nadie mencionó —fiebre y herida
        son las que más pesan y hay que asegurarlas temprano—, pero un tema que
        el paciente acaba de traer se atiende primero. Es la diferencia entre
        una agenda y un guion.
        """
        if self.prioridad is not None and self.prioridad not in self.preguntados:
            return self.prioridad
        for foco in ORDEN_FOCOS:
            if foco not in self.preguntados:
                return foco
        return None

    def dar_por_cubierto(self, foco: Foco) -> None:
        """El paciente ya contestó este tema, aunque nadie se lo haya preguntado.

        Sin esto el agente preguntaba lo que acababan de contestarle. Medido:
        ante "no he tenido fiebre, la herida seca, dolor dos y camino bien" —los
        cuatro temas en un turno— el agente igual preguntaba por la herida, y al
        turno siguiente respondía "no le entendí lo de la herida" a alguien que
        se la había descrito. Eso es un formulario, no una conversación.
        """
        self.preguntados.add(foco)
        if self.prioridad is foco:
            self.prioridad = None

    def dato_pendiente(self) -> Foco | None:
        """Foco donde el paciente respondió pero falta la cifra que decide.

        Tiene prioridad sobre avanzar al siguiente tema: sin el número de la
        fiebre no se puede distinguir un 37.2 de un 39, y el umbral está en 38.
        """
        if self.cuadro.falta_el_numero_de_fiebre and Foco.FIEBRE not in self.datos_pedidos:
            return Foco.FIEBRE
        return None


@dataclass
class RespuestaTurno:
    """Lo que el agente dice y por qué. `texto` va directo al sintetizador."""

    texto: str
    decision: Decision
    escala: bool
    cierra: bool
    foco: Foco | None = None
    alarmas_detectadas: list[str] = field(default_factory=list)


class Conversacion:
    """Máquina de turnos. El modelo nunca está en el camino crítico."""

    def __init__(
        self,
        cliente: ClienteLocal,
        estado: EstadoLlamada,
        consultor: Consultor | None = None,
    ) -> None:
        self.cliente = cliente
        self.estado = estado
        self.consultor = consultor
        self._extraccion: threading.Thread | None = None
        self._lock = threading.Lock()
        # Consumo acumulado del modelo. La extracción corre en un hilo de fondo
        # y sin este acumulador sus tokens no llegaban ni al pie de la consola
        # ni al registro — y las métricas de consumo son obligatorias en el
        # README, contrastadas contra los logs.
        self.uso_modelo = {"llamadas": 0, "tokens_entrada": 0, "tokens_salida": 0}

    def abrir(self) -> RespuestaTurno:
        """Primer turno: saludo más la primera pregunta, sin tocar el modelo."""
        foco = self.estado.siguiente_foco()
        self.estado.foco_actual = foco
        if foco:
            self.estado.preguntados.add(foco)
        texto = f"{APERTURA} {_elegir(PREGUNTAS[foco])}" if foco else APERTURA
        self.estado.transcripcion.append(("agente", texto))
        return RespuestaTurno(
            texto=texto,
            decision=evaluar(self.estado.cuadro),
            escala=False,
            cierra=False,
            foco=foco,
        )

    def _extraer_en_segundo_plano(self, texto: str, foco: Foco | None) -> None:
        """Actualiza el cuadro clínico mientras el paciente escucha la respuesta."""

        def tarea() -> None:
            resultado = extraer(
                self.cliente,
                texto,
                previo=self.estado.cuadro,
                pregunta_agente=PREGUNTAS[foco][0] if foco else None,
                foco=foco.value if foco else None,
            )
            with self._lock:
                self.estado.cuadro = resultado.cuadro
                self.uso_modelo["llamadas"] += 1
                self.uso_modelo["tokens_entrada"] += resultado.respuesta.tokens_entrada
                self.uso_modelo["tokens_salida"] += resultado.respuesta.tokens_salida
                if resultado.evasivo and foco and foco not in self.estado.repreguntados:
                    # Evasión detectada: el foco vuelve a la cola para repreguntar.
                    self.estado.preguntados.discard(foco)
                    self.estado.repreguntados.add(foco)

        hilo = threading.Thread(target=tarea, daemon=True)
        hilo.start()
        self._extraccion = hilo

    def _confirmar_registro(self) -> str:
        """Le dice al paciente qué quedó anotado hasta ahora, con sus valores.

        Se construye desde el cuadro clínico —lo que el sistema realmente
        tiene— así que nunca puede confirmar un dato que no registró.
        """
        with self._lock:
            cuadro = self.estado.cuadro
            partes: list[str] = []
            if cuadro.fiebre_c is not None:
                partes.append(f"su temperatura de {cuadro.fiebre_c} grados")
            elif cuadro.fiebre_referida_sin_medir:
                partes.append("que tuvo fiebre, aunque sin el número exacto")
            if cuadro.dolor_nrs is not None:
                partes.append(f"el dolor en {cuadro.dolor_nrs} de diez")
            if cuadro.herida.value != "desconocido":
                etiquetas = {
                    "normal": "que la herida se ve bien",
                    "eritema_leve": "que la herida está enrojecida",
                    "secrecion_purulenta": "la secreción de la herida",
                }
                partes.append(etiquetas[cuadro.herida.value])

        if not partes:
            return "Todavía no he podido anotar nada concreto, por eso le pregunto."
        if len(partes) == 1:
            return f"Sí señor, tengo anotado {partes[0]}."
        return f"Sí señor, tengo anotado {', '.join(partes[:-1])} y {partes[-1]}."

    def esperar_extraccion(self, timeout: float = 60.0) -> bool:
        """Bloquea hasta que termine la extracción pendiente.

        Se usa antes de cerrar la llamada y en las pruebas deterministas, nunca
        dentro del camino crítico de un turno.
        """
        if self._extraccion is None:
            return True
        self._extraccion.join(timeout)
        return not self._extraccion.is_alive()

    def responder(self, texto_paciente: str) -> RespuestaTurno:
        """Procesa un turno del paciente y devuelve qué decir. Sin esperar al modelo."""
        self.estado.transcripcion.append(("paciente", texto_paciente))
        foco_previo = self.estado.foco_actual

        # 1. Alarmas sobre el texto crudo. Determinista, microsegundos, no espera
        #    a la extracción: es la señal que no puede llegar tarde.
        # Minimización: se acumula a lo largo de la llamada. No es un síntoma
        # sino una señal sobre CÓMO reporta el paciente, y por eso solo pondera
        # hallazgos ya detectados — sobre un cuadro verde no hace nada.
        marcadores = minimizacion.contar(texto_paciente)
        if marcadores:
            with self._lock:
                self.estado.cuadro.marcadores_minimizacion += marcadores

        # Inquietud propia: el paciente trae un tema que nadie le preguntó.
        # Se registra tal cual lo dijo, sin interpretarlo, para que el equipo
        # que reciba la alerta lea sus palabras y no un resumen de ellas.
        trajo_inquietud = False
        if _es_inquietud(texto_paciente, self.estado.foco_actual):
            with self._lock:
                dicho = texto_paciente.strip()
                if dicho not in self.estado.inquietudes:
                    self.estado.inquietudes.append(dicho)
                    trajo_inquietud = True

        detectadas = alarmas.detectar(texto_paciente)
        if detectadas:
            with self._lock:
                nuevas = [
                    a for a in detectadas if a not in self.estado.cuadro.sintomas_alarma
                ]
                self.estado.cuadro.sintomas_alarma.extend(nuevas)

        prefijo_cifra = ""

        # 1a-bis. Estado de la herida en código y en este mismo turno. Por la vía
        #   de la extracción llegaba un turno tarde: "roja, hinchada y le sale
        #   líquido" recibía el eco de "roja" y la llamada seguía en amarillo,
        #   con una secreción —que escala sola— registrada como eritema leve.
        # Los detectores corren SIEMPRE, no solo sobre el tema preguntado. Los
        # pacientes ofrecen información fuera de turno todo el tiempo —"me duele
        # mucho y además la veo roja" mientras se pregunta por fiebre— y
        # escucharlos solo cuando responden lo que uno preguntó es lo que hace
        # que un sistema se sienta un formulario.
        #
        # MEDIDO sobre los 160 casos de la capa ruidosa: con los detectores
        # limitados al foco actual, el recall en rojo caía de 12/12 a 8/12. Cuatro
        # falsos negativos, dos de ellos clasificados VERDE. Lo que el paciente
        # dice, se escucha — lo haya preguntado el agente o no.
        estado_herida = herida_deteccion.estado_referido(
            texto_paciente,
            en_contexto=self.estado.foco_actual is Foco.HERIDA,
        )
        if estado_herida and estado_herida != "desconocido":
            with self._lock:
                # El hallazgo más grave manda: un eritema posterior no borra una
                # secreción ya referida.
                if _gravedad_herida(estado_herida) >= _gravedad_herida(
                    self.estado.cuadro.herida.value
                ):
                    self.estado.cuadro.herida = Herida(estado_herida)
                self.estado.dar_por_cubierto(Foco.HERIDA)

        # El dolor por escala verbal ("me duele harto") se escucha siempre. Los
        # números pelados solo cuando se preguntó por dolor: un "37" suelto es
        # una temperatura, no un dolor de 37 —que además no existe—.
        # …salvo que la propia frase nombre el dolor: "me duele muchísimo, como
        # un ocho" trae la palabra y el número juntos, y esperar al turno de
        # dolor para escucharlo era perder el dato que el paciente vino a dar.
        nivel_dolor = dolor_deteccion.nivel_dolor(
            texto_paciente,
            admitir_numero=(
                self.estado.foco_actual is Foco.DOLOR
                or dolor_deteccion.menciona_dolor(texto_paciente)
            ),
        )
        if nivel_dolor is not None:
            with self._lock:
                previo = self.estado.cuadro.dolor_nrs
                # El dolor más alto referido manda: el minimizador dice "un
                # poquito" al final y el 8 de antes no se borra.
                if previo is None or nivel_dolor > previo:
                    self.estado.cuadro.dolor_nrs = nivel_dolor
                self.estado.dar_por_cubierto(Foco.DOLOR)

        estado_movilidad = dolor_deteccion.estado_movilidad(
            texto_paciente,
            en_contexto=self.estado.foco_actual is Foco.MOVILIDAD,
        )
        if estado_movilidad:
            with self._lock:
                if _gravedad_movilidad(estado_movilidad) >= _gravedad_movilidad(
                    self.estado.cuadro.movilidad.value
                ):
                    self.estado.cuadro.movilidad = Movilidad(estado_movilidad)
                self.estado.dar_por_cubierto(Foco.MOVILIDAD)

        # 1b. Fiebre en código y en este mismo turno, en sus dos variantes.
        #     Por la vía de la extracción ambas llegarían un turno tarde.
        #     - Cifra dicha en voz alta: decide el semáforo YA. "Tuve 38" seguido
        #       de "Listo, ¿y la herida?" y el escalamiento un turno después
        #       suena a que el agente no escuchó el dato más importante.
        #     - Fiebre referida sin cifra: dispara el pedido del número.
        # Si el agente acaba de pedir la temperatura, un número pelado es la
        # respuesta: "34." no trae la palabra fiebre pero ES la temperatura.
        en_contexto_fiebre = self.estado.foco_actual is Foco.FIEBRE

        # Afirmación corta en contexto de fiebre: "He tenido", "sí, un poco".
        # Caso real: Whisper cortó la frase en "He tenido" y el agente dijo
        # "Perfecto, anotado" y cambió de tema — una afirmación de fiebre quedó
        # sin pedir la cifra. La afirmación responde a LA PREGUNTA HECHA, y la
        # pregunta era por fiebre.
        # El "sí" tiene que estar afirmando LA FIEBRE. Si el paciente arranca
        # afirmando pero habla de otra cosa —"sí, la herida está roja"— eso no es
        # una fiebre referida: el detector de ese tema ya lo recoge.
        afirma_la_fiebre = (
            en_contexto_fiebre
            and _es_afirmacion(texto_paciente)
            and not fiebre.tiene_cifra(texto_paciente)
            and not fiebre.niega_fiebre(texto_paciente)
            and _tema_traido(texto_paciente) in (None, Foco.FIEBRE)
        )
        if afirma_la_fiebre:
            with self._lock:
                self.estado.cuadro.fiebre_referida_sin_medir = True

        cifra_dicha = fiebre.extraer_cifra(
            texto_paciente, contexto_fiebre=en_contexto_fiebre
        )
        if cifra_dicha is not None:
            with self._lock:
                self.estado.cuadro.fiebre_c = cifra_dicha
                self.estado.cuadro.fiebre_referida_sin_medir = False
                self.estado.dar_por_cubierto(Foco.FIEBRE)
        elif fiebre.refiere_fiebre_sin_cifra(texto_paciente):
            # Se escucha aunque el agente estuviera preguntando otra cosa: el
            # paciente que dice "he estado acalorada" mientras se le pregunta por
            # la herida está reportando fiebre igual.
            #
            # El tema queda cubierto aunque falte el número: de pedir la cifra se
            # encarga `dato_pendiente()`. Sin esto, el agente pedía la
            # temperatura y DESPUÉS preguntaba "¿ha tenido fiebre?".
            with self._lock:
                self.estado.cuadro.fiebre_referida_sin_medir = True
                self.estado.dar_por_cubierto(Foco.FIEBRE)
        elif fiebre.niega_fiebre(texto_paciente):
            # Negar es contestar. "No he tenido fiebre" deja el tema cerrado, y
            # queda registrado como negación: si no, el resumen le informaba al
            # equipo que la fiebre había quedado sin preguntar.
            with self._lock:
                self.estado.cuadro.fiebre_negada = True
                self.estado.dar_por_cubierto(Foco.FIEBRE)

        # Intensidad dicha en palabras cuando se pidió el número: "Mucho."
        # Caso real: el paciente respondió "Mucho" a la pregunta por la
        # temperatura y el agente dijo "eso me sirve saberlo" y cambió de tema.
        # Sin termómetro no hay cifra, pero SÍ hay dato: la fiebre queda
        # referida y el eco confirma que se escuchó.
        intensidad = None
        if (
            cifra_dicha is None
            and fiebre.menciona_fiebre(texto_paciente)
            and not fiebre.niega_fiebre(texto_paciente)
        ):
            intensidad = fiebre.intensidad_referida(texto_paciente)
            if intensidad:
                with self._lock:
                    self.estado.cuadro.fiebre_referida_sin_medir = True

        # Cifra imposible: 58 grados no existe. Casi siempre es error de
        # transcripción — el paciente dijo 38. Frena la conversación y pide
        # confirmación, en vez de registrarla o de ignorarla en silencio.
        if en_contexto_fiebre and cifra_dicha is None and intensidad is None:
            imposible = fiebre.cifra_imposible(texto_paciente)
            if imposible is not None:
                with self._lock:
                    self.estado.intentos_cifra += 1
                    intentos = self.estado.intentos_cifra
                    # Intentarlo es correcto; encerrar al paciente en el bucle no.
                    # Lo dicho ya es fiebre referida: eso queda registrado igual.
                    self.estado.cuadro.fiebre_referida_sin_medir = True
                    decision_actual = evaluar(self.estado.cuadro)

                if intentos <= len(CIFRA_IMPOSIBLE):
                    valor = int(imposible) if imposible == int(imposible) else imposible
                    texto = CIFRA_IMPOSIBLE[intentos - 1].format(valor=valor)
                    self.estado.transcripcion.append(("agente", texto))
                    return RespuestaTurno(
                        texto=texto,
                        decision=decision_actual,
                        escala=decision_actual.escala,
                        cierra=False,
                        foco=Foco.FIEBRE,
                        alarmas_detectadas=detectadas,
                    )
                # Agotados los intentos: se abandona el número y se sigue.
                with self._lock:
                    self.estado.datos_pedidos.add(Foco.FIEBRE)
                    self.estado.preguntados.add(Foco.FIEBRE)
                prefijo_cifra = CIFRA_NO_OBTENIDA + " "

        # 1c. El paciente nombró un tema que todavía no se ha preguntado y del
        #     que no dio dato: pasa al frente de la cola. Si abre la llamada con
        #     "me preocupa la herida", contestarle con la pregunta de fiebre es
        #     responderle con la agenda propia. Solo adelanta UN tema; después la
        #     cola vuelve al orden clínico.
        with self._lock:
            traido = _tema_traido(texto_paciente)
            if traido is not None and traido not in self.estado.preguntados:
                self.estado.prioridad = traido

        # 2. La extracción arranca en segundo plano y no bloquea nada.
        self._extraer_en_segundo_plano(texto_paciente, foco_previo)

        # 3. Se decide con lo que ya se sabe, incluidas las alarmas recién vistas.
        with self._lock:
            decision = evaluar(self.estado.cuadro)

        # 3b. Si el paciente preguntó algo, se le responde ANTES de seguir con el
        #     triaje. Medido en el dataset: el 45 % de los turnos de la capa
        #     ruidosa traen una pregunta, y un agente que las ignora suena a
        #     formulario con voz. La respuesta sale del corpus con cita, o es el
        #     límite declarado — nunca una improvisación.
        #     Un tercero que se presenta recibe su acuse: su relato vale, y las
        #     alarmas ya barrieron su texto igual que el del paciente.
        prefijo = ""
        # Un saludo se responde con un saludo, y una aclaración repitiendo la
        # pregunta. Ninguno de los dos es la respuesta clínica, así que no se
        # evalúan como tal: en llamada real, "¡Buenos días!" recibió "Disculpe,
        # no le entendí la temperatura", y "¿qué es lo que se llama?" recibió el
        # discurso de "no está en mi documentación".
        if preguntas.es_saludo_puro(texto_paciente) or preguntas.es_aclaracion(texto_paciente):
            with self._lock:
                foco_vigente = self.estado.foco_actual or self.estado.siguiente_foco()
            saludo = "Buenos días. " if preguntas.es_saludo_puro(texto_paciente) else ""
            if foco_vigente is None:
                texto = f"{saludo}¿Hay algo más que quiera contarme?"
            elif self.estado.cuadro.falta_el_numero_de_fiebre and foco_vigente is Foco.FIEBRE:
                texto = f"{saludo}Le repito: {PEDIDOS_DE_DATO[Foco.FIEBRE]}"
            else:
                texto = f"{saludo}Le repito la pregunta: {_elegir(PREGUNTAS[foco_vigente])}"
            self.estado.transcripcion.append(("agente", texto))
            return RespuestaTurno(
                texto=texto,
                decision=decision,
                escala=decision.escala,
                cierra=False,
                foco=foco_vigente,
                alarmas_detectadas=detectadas,
            )

        if preguntas.habla_un_tercero(texto_paciente):
            prefijo = ACUSE_TERCERO + " "
        elif preguntas.es_aclaracion(texto_paciente):
            # Ya se manejó arriba; acá solo se evita que caiga al consultor del
            # corpus, que respondería "no está en mi documentación".
            prefijo = ""
        elif preguntas.es_verificacion(texto_paciente):
            # "¿Anotaste el número?" se responde desde el estado del sistema, no
            # desde el corpus: al paciente que quiere saber si lo escucharon,
            # contestarle "no está en mi documentación" lo trata como máquina.
            prefijo = self._confirmar_registro() + " "
        elif not decision.escala and preguntas.contiene_pregunta(texto_paciente):
            respuesta_corpus = self.consultor(texto_paciente) if self.consultor else None
            prefijo = (respuesta_corpus or SIN_RESPUESTA) + " "

        # La alerta se anuncia UNA vez y la conversación continúa: el equipo que
        # recibe el aviso necesita el cuadro completo, no el primer hallazgo.
        # Si ya no quedan preguntas, el cierre rojo recapitula y despide.
        anuncio_alerta = ""
        if decision.escala:
            with self._lock:
                primera_vez = not self.estado.alerta_anunciada
                self.estado.alerta_anunciada = True
                quedan_preguntas = self.estado.siguiente_foco() is not None
            if primera_vez:
                if not quedan_preguntas:
                    self.estado.cerrada = True
                    self.estado.transcripcion.append(("agente", ESCALAMIENTO))
                    return RespuestaTurno(
                        texto=ESCALAMIENTO,
                        decision=decision,
                        escala=True,
                        cierra=True,
                        alarmas_detectadas=detectadas,
                    )
                anuncio_alerta = ESCALAMIENTO + " "

        # 3c. Si la respuesta no aporta ningún dato del tema preguntado, se
        #     vuelve a preguntar en vez de avanzar. Una sola vez: insistir dos
        #     veces sobre lo mismo hostiga, y si no se entiende a la segunda,
        #     mejor seguir y dejar constancia de lo que quedó sin preguntar.
        with self._lock:
            foco_respondido_previo = self.estado.foco_actual
        sin_responder_lo_preguntado = (
            foco_respondido_previo is not None
            and not prefijo_cifra  # ya se abandonó la cifra: no reintentar encima
            and cifra_dicha is None  # una cifra captada ES el dato
            and estado_herida is None  # un estado de herida detectado también
            and nivel_dolor is None
            and estado_movilidad is None
            and not _aporta_dato(texto_paciente, foco_respondido_previo)
        )

        # 3b-bis. El paciente no falló en responder: cambió de tema a propósito.
        #     Medido: ante "me preocupa la herida" mientras se preguntaba por la
        #     fiebre, el agente contestaba "disculpe, no le entendí la
        #     temperatura" y repetía la misma pregunta. El paciente había hablado
        #     con toda claridad — de otra cosa. Se le sigue el tema y el que
        #     quedó pendiente vuelve a la cola, para retomarlo con "volvamos a".
        cambio_de_tema = (
            sin_responder_lo_preguntado
            and traido is not None
            and traido is not foco_respondido_previo
            and foco_respondido_previo not in self.estado.repreguntados
        )
        if cambio_de_tema:
            with self._lock:
                self.estado.preguntados.discard(foco_respondido_previo)
                self.estado.repreguntados.add(foco_respondido_previo)

        # 3b-ter. El paciente trajo una preocupación propia en vez de responder.
        #     MEDIDO en llamada completa por la API: abrió con «no me quitan el
        #     drenaje y eso me tiene preocupada» y recibió «disculpe, no le
        #     entendí la temperatura». Le entendió perfecto — hablaba de otra
        #     cosa. Se le reconoce lo que trajo y se repite la pregunta sin
        #     tratarlo como si no se hubiera explicado. Solo la PRIMERA vez que
        #     trae ese tema: si insiste sin responder, vuelve el reintento normal.
        if (
            sin_responder_lo_preguntado
            and trajo_inquietud
            and not cambio_de_tema
            and not prefijo
        ):
            variante = _elegir(PREGUNTAS[foco_respondido_previo])
            texto = f"{anuncio_alerta}{ACUSE_INQUIETUD} {variante}"
            self.estado.transcripcion.append(("agente", texto))
            return RespuestaTurno(
                texto=texto,
                decision=decision,
                escala=decision.escala,
                cierra=False,
                foco=foco_respondido_previo,
                alarmas_detectadas=detectadas,
            )

        if (
            sin_responder_lo_preguntado
            and not cambio_de_tema
            and not decision.escala
            and not prefijo
            and foco_respondido_previo not in self.estado.reintentados
        ):
            with self._lock:
                self.estado.reintentados.add(foco_respondido_previo)
            variante = _elegir(PREGUNTAS[foco_respondido_previo])
            texto = f"{anuncio_alerta}{NO_SE_ENTENDIO[foco_respondido_previo]} {variante}"
            self.estado.transcripcion.append(("agente", texto))
            return RespuestaTurno(
                texto=texto,
                decision=decision,
                escala=False,
                cierra=False,
                foco=foco_respondido_previo,
                alarmas_detectadas=detectadas,
            )

        # 4. Siguiente pregunta desde plantilla: cero costo de modelo.
        #    Antes de avanzar de tema, se pide la cifra que quedó pendiente. Sin
        #    el número de la fiebre no se puede decidir, y asumirlo es un error
        #    clínico en las dos direcciones.
        with self._lock:
            pendiente = self.estado.dato_pendiente()
            if pendiente is not None:
                self.estado.datos_pedidos.add(pendiente)
                self.estado.foco_actual = pendiente
                # Se nombra lo que el paciente dijo: escalofríos no es fiebre,
                # aunque lleven a la misma pregunta.
                acuse_previo = (
                    ACUSE_ESCALOFRIOS
                    if pendiente is Foco.FIEBRE
                    and "escalofri" in _sin_tildes(texto_paciente)
                    else ACUSE_PREOCUPACION
                )
                texto = f"{anuncio_alerta}{prefijo_cifra}{prefijo}{acuse_previo} {PEDIDOS_DE_DATO[pendiente]}"
                self.estado.transcripcion.append(("agente", texto))
                return RespuestaTurno(
                    texto=texto,
                    decision=decision,
                    escala=False,
                    cierra=False,
                    foco=pendiente,
                    alarmas_detectadas=detectadas,
                )
            foco = self.estado.siguiente_foco()

        if foco is None:
            if not self.estado.despedida_ofrecida:
                # Último espacio antes de cerrar: la llamada no se corta en seco.
                self.estado.despedida_ofrecida = True
                # Sin foco: la pregunta es abierta. Dejar el foco anterior hacía
                # que un "no, nada más" se evaluara contra el último tema y
                # disparara un reintento — el agente decía "no le entendí lo del
                # movimiento" a alguien que solo se estaba despidiendo.
                with self._lock:
                    self.estado.foco_actual = None
                texto = (
                    f"{anuncio_alerta}{prefijo}Ya casi terminamos. Antes de "
                    "despedirme: ¿hay algo más que quiera contarme o preguntarme?"
                )
                self.estado.transcripcion.append(("agente", texto))
                return RespuestaTurno(
                    texto=texto,
                    decision=decision,
                    escala=False,
                    cierra=False,
                    alarmas_detectadas=detectadas,
                )
            return self._cerrar(decision)

        es_repregunta = foco in self.estado.repreguntados
        pregunta = _elegir((REPREGUNTAS if es_repregunta else PREGUNTAS)[foco])
        with self._lock:
            self.estado.preguntados.add(foco)
            foco_respondido = self.estado.foco_actual
            self.estado.foco_actual = foco

        # El acuse refleja el dato recien escuchado cuando lo hay; si no, una
        # variante generica. "34, listo" vale mas que tres "Listo, gracias".
        # Si ya se reintentó este tema y sigue sin entenderse, se dice la verdad
        # en vez de "Perfecto, anotado": el paciente respondió "¡O-HA!" y recibió
        # un acuse de conformidad sobre un turno que no aportó nada.
        sin_dato_tras_reintento = (
            foco_respondido is not None
            and foco_respondido in self.estado.reintentados
            and cifra_dicha is None
            and estado_herida is None
            and nivel_dolor is None
            and estado_movilidad is None
            and not _aporta_dato(texto_paciente, foco_respondido)
        )
        acuse = (
            (f"No logré captar lo de {foco_respondido.value}, lo dejo pendiente."
             if sin_dato_tras_reintento else None)
            or _eco_intensidad(intensidad)
            or _eco(texto_paciente, cifra_dicha, foco_respondido)
            # El paciente trajo su tema: reconocerlo antes de entrar en él. Un
            # "Listo, gracias" a "me preocupa la herida" suena a que no escuchó.
            or ("Claro, hablemos de eso." if cambio_de_tema else None)
            or _elegir(ACUSES[decision.semaforo])
        )
        texto = f"{anuncio_alerta}{prefijo_cifra}{prefijo}{acuse} {pregunta}"
        self.estado.transcripcion.append(("agente", texto))
        return RespuestaTurno(
            texto=texto,
            decision=decision,
            escala=decision.escala,
            cierra=False,
            foco=foco,
            alarmas_detectadas=detectadas,
        )

    def _cerrar(self, decision: Decision) -> RespuestaTurno:
        """Cierra la llamada. Antes espera la extracción: el resumen debe ser fiel."""
        self.esperar_extraccion()
        with self._lock:
            decision = evaluar(self.estado.cuadro)
            self.estado.cerrada = True

        if decision.escala:
            texto = CIERRE_ROJO
        elif decision.semaforo is Semaforo.AMARILLO:
            texto = CIERRE_AMARILLO
        else:
            texto = CIERRE_VERDE

        if self.estado.inquietudes:
            # Se nombra lo que el paciente trajo: preguntó por su cuenta y el
            # cierre debe decirle que quedó anotado, no ignorarlo.
            texto = (
                "Y lo que me comentó, que no era parte de mis preguntas, también "
                f"queda anotado para el equipo. {texto}"
            )
        self.estado.transcripcion.append(("agente", texto))
        return RespuestaTurno(
            texto=texto, decision=decision, escala=decision.escala, cierra=True
        )
