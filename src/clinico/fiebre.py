"""Detección de fiebre referida sin cifra. Determinista, en código.

Por qué no lo resuelve la extracción del modelo: porque llega tarde. La
extracción corre en segundo plano para no bloquear la conversación, así que
cuando el paciente dice "tuve fiebre" el agente todavía no lo sabe y avanzaría
al tema siguiente. Pedir la cifra un turno después suena a que no lo escuchó.

Y la cifra importa más que casi cualquier otro dato: el umbral de escalamiento
está en 38.0 °C, y "me sentí afiebrado" abarca desde 37.2 —verde— hasta 39
—rojo—. Asumir alta es alarmismo; asumir normal es el falso negativo que la
rúbrica castiga con más dureza. La única salida correcta es preguntar.

Mismo principio que `alarmas.py`: lo que no puede llegar tarde, no se le pide al
modelo.
"""

from __future__ import annotations

import re
import unicodedata

# Cómo nombra un paciente colombiano la sensación febril, con y sin la palabra
# "fiebre". Muchos nunca la usan.
MENCIONES_FIEBRE = [
    r"\bfiebre\b",
    r"\bfebril\b",
    r"\bafiebrad[oa]\b",
    r"\bcalentura\b",
    r"\bescalofri",
    r"\bdestemplad[oa]\b",
    r"\bcuerpo caliente\b",
    r"\bme senti caliente\b",
    # "Acalorada" es como muchas pacientes nombran la fiebre sin usar la
    # palabra. Caso real del dataset: "he estado como acalorada un poco" con
    # 38.9 °C de temperatura real — se perdía entero.
    r"\bacalorad[oa]\b",
    r"\bsofocad[oa]\b",
    r"\bhirviendo\b",
    r"\bcaliente por dentro\b",
    r"\bardiendo\b",
    r"\btirit",
    r"\bsudor(es|acion)?\s+(en la noche|nocturn)",
    r"\bfrio raro\b",
]

# Una cifra de temperatura, en dígitos o en palabras. El paciente dice "treinta y
# ocho" tan seguido como "38", y por teléfono ambas llegan igual de válidas.
CIFRAS_TEMPERATURA = [
    r"\b3[5-9][.,]?\d?\b",          # 37, 38.5, 39,2
    r"\b4[0-2][.,]?\d?\b",          # 40 a 42
    r"treinta\s+y\s+(cinco|seis|siete|ocho|nueve)",
    r"cuarenta(\s+y\s+(uno|dos))?",
    r"\bgrados?\b.*\b\d",
]

# Negaciones de la sensación febril. Sin esto, "fiebre no he tenido" disparaba
# el pedido de cifra: el detector veía la palabra y no veía el "no". El paciente
# colombiano niega en ambos órdenes — "no he tenido fiebre" y "fiebre no he
# tenido" — así que se cubren los dos.
NEGACIONES = [
    r"\b(?:no|tampoco|nada de|sin|ni)\b[^.,;]{0,40}?"
    r"(?:fiebre|calentura|escalofri\w*|destemplad\w*|afiebrad\w*)",
    r"(?:fiebre|calentura|escalofri\w*|destemplad\w*|afiebrad\w*)"
    r"[^.,;]{0,30}?\b(?:no|nada|ninguna?|tampoco)\b",
]

_MENCIONES = [re.compile(p) for p in MENCIONES_FIEBRE]
_CIFRAS = [re.compile(p) for p in CIFRAS_TEMPERATURA]
_NEGACIONES = [re.compile(p) for p in NEGACIONES]


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")


def niega_fiebre(texto: str) -> bool:
    """El paciente niega la sensación febril, en cualquiera de los dos órdenes."""
    normalizado = _normalizar(texto)
    return any(p.search(normalizado) for p in _NEGACIONES)


# Formas en que el paciente dice que NO PUDO medirse la temperatura. Es una
# respuesta, no una evasión: MEDIDO en llamada por voz contra el contenedor, a
# "¿se alcanzó a tomar la temperatura?" contestó "No me la tomé" y recibió
# "disculpe, no le entendí la temperatura". Entendió perfecto — no hay número
# porque no hubo termómetro, y eso ya está registrado como fiebre sin medir.
_SIN_MEDIR = [
    re.compile(r"\bno (me la|me lo|la|lo) (tome|medi|he tomado|he medido|habia tomado)"),
    # El paciente no siempre mete el pronombre. En llamada real dijo "no me medí
    # la temperatura" y el patrón exigía "no me la medí", así que no lo vio.
    re.compile(r"\bno me (tome|medi|he tomado|he medido)\b"),
    re.compile(r"\bno (tengo|tenemos|hay|tenia) termometro"),
    re.compile(r"\bsin termometro\b"),
    re.compile(r"\bno me (la )?pude tomar"),
    re.compile(r"\bno alcance a (tomarme|medirme|tomarla|tomar)"),
    re.compile(r"\bnunca me la (tome|he tomado)"),
]

# Habló de haberse tomado (o no) la temperatura, en cualquier sentido. Aunque no
# dé la cifra, ESTÁ respondiendo la pregunta: no merece un "no le entendí".
_HABLA_DE_MEDIRSE = re.compile(
    r"\b(tome|tomé|medi|medí|tomado|medido|termometro)\b.{0,25}\b(temperatura|fiebre)\b"
    r"|\b(temperatura|fiebre)\b.{0,25}\b(tome|medi|tomado|medido)\b"
    # "La tomé", "sí, me la medí": la respuesta corta a "¿se la tomó?". En
    # llamada real "La tomé." recibió "no le entendí la temperatura".
    r"|^\s*(si[, ]+)?(me )?la (tome|medi)\b"
    r"|\btermometro\b"
)


def hablo_de_medirse(texto: str) -> bool:
    """Mencionó el acto de tomarse la temperatura, con cifra o sin ella."""
    plano = re.sub(r"[,;]", " ", _normalizar(texto))
    return bool(_HABLA_DE_MEDIRSE.search(plano)) or no_pudo_medir(texto)


def no_pudo_medir(texto: str) -> bool:
    """Dijo que no se tomó la temperatura. Responde la pregunta: no hay cifra.

    Se quita la puntuación antes de comparar porque Whisper la inventa: la misma
    frase volvió como "No me la tomé." una vez y con una coma después del "no"
    la siguiente. Una coma no cambia lo que el paciente quiso decir, y hacer
    depender una decisión clínica de dónde el transcriptor puso una pausa es
    frágil justo donde no conviene serlo.
    """
    normalizado = re.sub(r"[,;]", " ", _normalizar(texto))
    normalizado = re.sub(r"\s+", " ", normalizado)
    return any(p.search(normalizado) for p in _SIN_MEDIR)


def menciona_fiebre(texto: str) -> bool:
    """El paciente describe sensación febril, con la palabra que sea.

    Una mención negada no es una mención: "fiebre no he tenido" habla de fiebre
    para decir que no la hay.
    """
    normalizado = _normalizar(texto)
    if any(p.search(normalizado) for p in _NEGACIONES):
        return False
    return any(p.search(normalizado) for p in _MENCIONES)


def tiene_cifra(texto: str) -> bool:
    """El texto contiene una temperatura, en dígitos o escrita en palabras."""
    normalizado = _normalizar(texto)
    return any(p.search(normalizado) for p in _CIFRAS)


# Intensidad dicha en palabras cuando el agente pide la cifra. Caso real: el
# agente preguntó la temperatura, el paciente respondió "Mucho." y el sistema
# dijo "eso me sirve saberlo" y cambió de tema — ignoró el síntoma. Una
# intensidad alta sin termómetro NO es un dato menor: es fiebre referida.
#
# Se compara por CONJUNTOS DE PALABRAS y no por expresión regular: es lo mismo
# de expresivo acá, y no depende del escapado — un patrón mal escapado falla en
# silencio, que en esta capa significa ignorar un síntoma sin que nadie lo note.
INTENSIDAD_ALTA = {
    "mucho", "mucha", "muchisimo", "muchisima", "altisima", "altisimo",
    "bastante", "harto", "harta", "fuerte", "terrible", "alta", "alto",
}
INTENSIDAD_BAJA = {
    "poquito", "poquita", "poco", "poca", "leve", "levemente", "apenas",
    "ligera", "ligero", "poquitico",
}


# Rango fisiológicamente posible en un ser humano vivo. Fuera de esto, la cifra
# es un error de transcripción o de lectura del termómetro, no un dato clínico.
#
# Caso real: el paciente dijo "38" y Whisper transcribió "58". El agente
# respondió "eso me sirve saberlo" y siguió. Una temperatura imposible tiene que
# frenar la conversación y pedir confirmación — dejarla pasar es peor que no
# haber preguntado, porque queda en el registro clínico.
TEMPERATURA_MINIMA_POSIBLE = 30.0
TEMPERATURA_MAXIMA_POSIBLE = 43.0

# Números que el paciente dice en respuesta a la pregunta por la temperatura
# pero que no pueden serlo. Se usan para distinguir "no dijo cifra" de
# "dijo una cifra imposible" — son situaciones distintas y se responden distinto.
_RE_CUALQUIER_NUMERO = re.compile(r"\b(\d{1,3})(?:[.,](\d))?\b")


def cifra_imposible(texto: str) -> float | None:
    """Devuelve el número dicho si es una temperatura imposible, o None.

    Un 58 o un 12 en respuesta a "¿cuánto marcó el termómetro?" es casi siempre
    un error de transcripción de voz. El agente debe pedir confirmación en vez
    de registrarlo o de ignorarlo en silencio.
    """
    for m in _RE_CUALQUIER_NUMERO.finditer(_normalizar(texto)):
        entero = int(m.group(1))
        decimal = int(m.group(2)) / 10 if m.group(2) else 0.0
        valor = entero + decimal
        if not (TEMPERATURA_MINIMA_POSIBLE <= valor <= TEMPERATURA_MAXIMA_POSIBLE):
            return valor
    return None


def _palabras(texto: str) -> set[str]:
    return set(re.findall(r"[a-z]+", _normalizar(texto)))


def intensidad_referida(texto: str) -> str | None:
    """Intensidad de fiebre dicha en palabras: 'alta', 'baja' o None."""
    palabras = _palabras(texto)
    if palabras & INTENSIDAD_ALTA:
        return "alta"
    if palabras & INTENSIDAD_BAJA:
        return "baja"
    return None


def refiere_fiebre_sin_cifra(texto: str) -> bool:
    """Dijo tener fiebre pero no dio el número. Hay que pedírselo en este turno."""
    return menciona_fiebre(texto) and not tiene_cifra(texto)


# Números en palabras, como llegan por teléfono: "treinta y ocho y medio".
# El rango cubre 31-42: un paciente puede reportar 34 —hipotermia o termómetro
# mal puesto— y descartarlo por fuera de rango fue un bug real: el valor
# inventado por el modelo quedó mandando sobre la corrección del paciente.
_PALABRA_A_NUMERO = {
    "uno": 31, "dos": 32, "tres": 33, "cuatro": 34,
    "cinco": 35, "seis": 36, "siete": 37, "ocho": 38, "nueve": 39,
}
# El lookahead negativo descarta números con unidad ajena a la temperatura:
# "camino 40 minutos al día" contiene un 40 que no es fiebre. Sin esto, cualquier
# cifra del rango en cualquier contexto se tomaba como temperatura.
_RE_DIGITOS = re.compile(
    r"\b(3[1-9]|4[0-2])(?:[.,](\d))?\b"
    r"(?!\s*(?:minut|hora|dia|semana|mes|año|kilo|libra|pastilla|gota|vez|vece|paso|metro|cuadra|peso|mil))"
)
_RE_PALABRAS = re.compile(
    r"treinta\s+y\s+(uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)"
    r"(\s+(y\s+medio|punto\s+(\d)|coma\s+(\d)))?"
)


def extraer_cifra(texto: str, *, contexto_fiebre: bool = False) -> float | None:
    """Extrae la temperatura dicha, en dígitos o en palabras. None si no hay.

    Existe porque la cifra dicha en voz alta no puede esperar a la extracción del
    modelo: "tuve 38" seguido de "Listo, gracias, ¿y la herida?" y un escalamiento
    un turno después suena a que el agente no escuchó el dato más importante de la
    llamada. El umbral está en 38.0 — esta cifra decide el semáforo YA.

    `contexto_fiebre`: el agente ACABA de pedir la temperatura, así que un número
    pelado —"34."— es la respuesta aunque no venga con la palabra fiebre. Sin
    este contexto, la respuesta directa del paciente a la pregunta directa del
    agente se descartaba.
    """
    normalizado = _normalizar(texto)
    if not contexto_fiebre and not menciona_fiebre(normalizado) \
            and "temperatura" not in normalizado \
            and "termometro" not in normalizado and not tiene_cifra(normalizado):
        return None

    # Formas compuestas en dígitos, como las produce Whisper cuando el paciente
    # dicta el número por partes. Caso real: "treinta y cinco" llegó como
    # "30 y 5" y también como "3 y 5", y ambas se rechazaban como imposibles
    # mientras el paciente repetía la cifra correcta una y otra vez.
    m = re.search(r"\b(\d{1,2})\s*y\s*(\d)\b", normalizado)
    if m:
        primero, unidad = int(m.group(1)), int(m.group(2))
        if primero in (30, 40):          # "30 y 5" -> 35
            candidato = primero + unidad
        elif primero in (3, 4):          # "3 y 5"  -> 35 (dicta dígito a dígito)
            candidato = primero * 10 + unidad
        else:
            candidato = None
        if candidato is not None and 31 <= candidato <= 42:
            return float(candidato)

    # "38 y medio" en dígitos: el regex principal tomaría el 38 y perdería el medio.
    m = re.search(r"\b(3[1-9]|4[0-2])\s*y\s*medio\b", normalizado)
    if m:
        return float(m.group(1)) + 0.5

    # "37 y algo", "38 y pico", "37 y monedas": el paciente sabe el entero y no
    # el decimal. Tomarlo como el entero exacto es quedarse con el mejor caso, y
    # en el caso real del dataset esa lectura convirtió un 37.9 (amarillo) en un
    # 37.0 (verde). Se asume el punto medio, que es la lectura cautelosa.
    m = re.search(r"\b(3[1-9]|4[0-2])\s*y\s*(algo|pico|monedas|tantos|puchito)\b",
                  normalizado)
    if m:
        return float(m.group(1)) + 0.5

    m = _RE_DIGITOS.search(normalizado)
    if m:
        entero = int(m.group(1))
        decimal = int(m.group(2)) / 10 if m.group(2) else 0.0
        return float(entero) + decimal

    m = _RE_PALABRAS.search(normalizado)
    if m:
        valor = float(30 + _PALABRA_A_NUMERO[m.group(1)] - 30)
        if m.group(3):
            if "medio" in m.group(3):
                valor += 0.5
            elif m.group(4):
                valor += int(m.group(4)) / 10
            elif m.group(5):
                valor += int(m.group(5)) / 10
        return valor
    if "cuarenta" in normalizado and menciona_fiebre(normalizado):
        return 40.0
    return None
