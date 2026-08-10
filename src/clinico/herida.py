"""Detección del estado de la herida. Determinista, en código.

Por qué no lo resuelve la extracción del modelo: porque llega tarde y porque se
equivoca en el peor caso. La extracción corre en segundo plano, así que cuando
el paciente dice "roja, hinchada y también le sale líquido" el agente todavía no
lo sabe y responde con un eco parcial —"me dice que la ve roja"— y sigue.

Caso real medido: ese turno describía una SECRECIÓN, que escala a rojo por sí
sola, y la llamada continuó en amarillo con "eritema leve" registrado. Un
falso negativo en secreción purulenta es exactamente lo que la rúbrica castiga
con más dureza.

Mismo principio que `alarmas.py` y `fiebre.py`: lo que no puede llegar tarde no
se le pide al modelo.
"""

from __future__ import annotations

import re
import unicodedata

# Cómo describe un paciente colombiano la secreción. Casi nunca dice "pus" ni
# "purulenta": dice que "le sale algo", que "está botando" o nombra el color.
SECRECION = [
    r"\bsale\b.{0,20}\b(liquido|algo|pus|materia|agua|sangre|secrecion)",
    r"\b(liquido|pus|materia|secrecion)\b.{0,20}\bsal(e|iendo)",
    # El paciente no siempre usa un verbo. En llamada real dijo "hinchada y con
    # líquido" y se registró como ERITEMA LEVE: el detector exigía la palabra
    # "sale" y se quedó con el hallazgo menos grave, degradando una secreción
    # —que escala sola— a amarillo. La sola presencia de líquido o secreción
    # nombrada sobre la herida ya es el hallazgo.
    r"\bcon (liquido|secrecion|pus|materia|algo)\b",
    r"\btiene\b.{0,15}\b(liquido|secrecion|pus|materia)\b",
    r"\b(liquido|secrecion)\b.{0,15}\b(amarill|verdos|blanc|espes|feo|raro)",
    r"\bbotando\b",
    r"\bsupura",
    r"\bpus\b",
    r"\bmateria\b",
    r"\bamarill|\bverdos|\bverde\b",
    r"\bmancha.{0,15}(gasa|apósito|aposito|venda|ropa)",
    r"\bhuele (feo|mal|maluco)",
    r"\bmal olor\b",
    r"\bdrena(ndo|je)?\b.{0,15}\b(mucho|feo|raro)",
]

# Eritema: enrojecimiento e hinchazón sin secreción. Amarillo, no rojo.
ERITEMA = [
    r"\broj(a|o|ita|ito)\b",
    r"\bcolorad(a|o|ita)\b",
    r"\benrojecid",
    r"\bhinchad|\binflamad",
    r"\bcaliente\b.{0,20}\b(herida|zona|alrededor)",
    r"\bherida\b.{0,20}\bcaliente\b",
    r"\bmorad(a|o)\b",
]

# Herida vista y sin hallazgos.
#
# Todas estas palabras son LAXAS: "bien", "normal" y "nada raro" describen
# cualquier cosa. MEDIDO sobre los 160 casos: el paciente decía "no muy bien la
# verdad, me despierto varias veces" —hablando de cómo durmió— y se registraba
# la herida como NORMAL. Por eso solo cuentan si el agente acaba de preguntar
# por la herida, o si la frase la nombra. Declarar sana una herida de la que
# nadie habló es la clase de dato que después sostiene una decisión clínica.
NORMAL = [
    r"\bnormal\b",
    r"\bbien\b",
    r"\bseca\b",
    r"\blimpia\b",
    r"\bcicatriz(ando)?\b",
    r"\bno le veo nada\b",
    r"\bnada raro\b",
    r"\bsin nada\b",
]

# Ancla: la frase tiene que estar hablando de la herida.
_MENCIONA_HERIDA = re.compile(
    r"\b(herida|cicatriz|costura|corte|punto|sutura|grapa|incision|zona|"
    r"vendaje|gasa|aposito|curacion|curaci)\w*"
)

# El paciente NO la ha mirado: no es "normal", es desconocido. Confundirlos
# registra como revisada una herida que nadie vio.
NO_MIRADA = [
    r"\bno la he (mirado|visto|revisado|destapado)",
    r"\bni la he (mirado|visto)",
    r"\bno me he (fijado|mirado)",
    r"\bno la miro\b",
    r"\bno he podido ver",
    r"\bme da (cosa|miedo|susto) (mirarla|verla|destaparla)",
    r"\btiene (vendaje|gasa|aposito)",
]

_SECRECION = [re.compile(p) for p in SECRECION]
_ERITEMA = [re.compile(p) for p in ERITEMA]
_NORMAL = [re.compile(p) for p in NORMAL]
_NO_MIRADA = [re.compile(p) for p in NO_MIRADA]


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")


def _negado(normalizado: str, patron: re.Pattern[str]) -> bool:
    """El hallazgo aparece dentro de una negación.

    MEDIDO sobre los 160 casos: sin esto, "nada de pus ni nada raro" y "se ve
    rojita pero nada de pus" se registraban como SECRECIÓN PURULENTA —el
    hallazgo más grave del sistema, que escala solo—. Trece pacientes verdes
    escalaban por una secreción que el paciente estaba negando.

    Se busca un negador en los 30 caracteres previos al hallazgo, sin cruzar
    puntuación fuerte: "no le sale nada" niega, pero "le sale líquido. No tengo
    fiebre" no niega la secreción.
    """
    for m in patron.finditer(normalizado):
        ventana = normalizado[max(0, m.start() - 30) : m.start()]
        ventana = re.split(r"[.;]", ventana)[-1]
        if not re.search(r"\b(no|nada de|sin|ni|tampoco|ningun[ao]?)\b", ventana):
            return False  # al menos una aparición NO está negada
    return True


def menciona_herida(texto: str) -> bool:
    return bool(_MENCIONA_HERIDA.search(_normalizar(texto)))


def estado_referido(texto: str, *, en_contexto: bool = False) -> str | None:
    """Estado de la herida según lo dicho: el valor del enum `Herida`, o None.

    El orden importa y es clínico: la secreción manda sobre todo lo demás. Una
    herida "roja, hinchada y con líquido" es secreción purulenta —rojo— y no
    eritema leve. Quedarse con el primer hallazgo mencionado sería quedarse con
    el menos grave.

    Las negaciones se respetan: el paciente que dice "nada de pus" está negando
    la secreción, no reportándola.
    """
    normalizado = _normalizar(texto)

    for p in _SECRECION:
        if p.search(normalizado) and not _negado(normalizado, p):
            return "secrecion_purulenta"
    for p in _ERITEMA:
        if p.search(normalizado) and not _negado(normalizado, p):
            return "eritema_leve"
    if any(p.search(normalizado) for p in _NO_MIRADA):
        return "desconocido"

    # "Normal" solo cuenta si se está hablando de la herida: ni el agente puede
    # dar por sana una herida que nadie mencionó, ni "no muy bien" es un informe
    # sobre ella. Y se respeta la negación igual que en los hallazgos.
    if not en_contexto and not _MENCIONA_HERIDA.search(normalizado):
        return None
    for p in _NORMAL:
        if p.search(normalizado) and not _negado(normalizado, p):
            return "normal"
    return None
