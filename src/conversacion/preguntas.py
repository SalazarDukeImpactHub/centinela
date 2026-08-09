"""Detección de preguntas y de terceros en el habla del paciente. En código.

Medido sobre el dataset: 428 de 960 turnos de paciente en la capa ruidosa
contienen una pregunta, y hay 151 interrupciones de cuidadores. Un agente que
ignora la pregunta y sigue con su cuestionario suena a formulario con voz — y la
rúbrica evalúa qué pasa cuando el paciente se sale del guion.

La transcripción de voz no trae signos de interrogación confiables, así que la
detección no puede depender de un '?': se buscan las formas interrogativas con
que habla un paciente colombiano.
"""

from __future__ import annotations

import re
import unicodedata

# Formas interrogativas frecuentes en el dataset. No exhaustivas a propósito:
# ante la duda es mejor no tratar como pregunta (el flujo normal continúa) que
# interrumpir el triaje por un falso positivo.
INTERROGATIVAS = [
    r"\bpuedo\b",
    r"\bpodria\b",
    r"\bdebo\b",
    r"\bdeberia\b",
    r"\btengo que\b",
    r"\bes normal\b",
    r"\beso esta bien\b",
    r"\besta bien eso\b",
    r"\bsera que\b",
    r"\bque pasa si\b",
    r"\bcuando (puedo|debo|me )",
    r"\bcomo asi\b",
    r"\bcierto\s*\?",
    r"\bo no\s*\?",
    r"\?",
]

# Presentación de un tercero: cuidador, familiar. Se le agradece y se toma su
# relato como información del paciente — las alarmas ya barren el texto crudo.
TERCEROS = [
    r"\bsoy (el|la) (cuidador|cuidadora|enfermer|esposa|esposo|hij[oa]|herman[oa]|mam[aá]|pap[aá])\b",
    r"\ble hablo por (el|ella)\b",
    r"\byo lo (estoy cuidando|cuido)\b",
    r"\bpermitame contarle\b",
]

_INTERROGATIVAS = [re.compile(p) for p in INTERROGATIVAS]
_TERCEROS = [re.compile(p) for p in TERCEROS]

# Preguntas SIN signo de interrogación, como llegan de la transcripción de voz.
# Caso real: "Anotaste en cuánto tengo la fiebre el número" es una pregunta y
# ninguna forma clásica la reconocía — el agente la ignoró y siguió su guion.
# Son verbos con los que el paciente verifica que se le escuchó.
VERBOS_DE_VERIFICACION = {
    "anoto", "anoto", "anotaste", "anotaron", "anotó",
    "registro", "registraste", "registraron", "registró",
    "escucho", "escuchaste", "escuchó", "escucharon",
    "entendio", "entendiste", "entendió",
    "quedo", "quedó", "quedaron",
    "tiene", "tienes", "tenes",
    "apunto", "apuntaste", "apuntó",
}


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")


def contiene_pregunta(texto: str) -> bool:
    normalizado = _normalizar(texto)
    if any(p.search(normalizado) for p in _INTERROGATIVAS):
        return True
    return es_verificacion(texto)


# Saludos y cortesías puras. Caso real: el paciente abrió con "¡Buenos días!"
# y recibió "Disculpe, no le entendí la temperatura" — el saludo se evaluó como
# si fuera la respuesta clínica. A un saludo se responde con un saludo.
_SALUDOS = re.compile(
    r"^\s*(buenos dias|buenas tardes|buenas noches|buenas|hola|alo|"
    r"muy buenas|que tal|como esta|como le va)[\s.,!¡¿?]*$"
)

# Pedidos de aclaración: el paciente no entendió LA PREGUNTA. Caso real: "¿Qué
# es lo que se llama?" se buscó en el corpus clínico y recibió el discurso de
# "no está en mi documentación" — a alguien que solo pedía que le repitieran.
_ACLARACIONES = re.compile(
    r"^\s*(como|como asi|que|que cosa|que es eso|que es lo que|no entiendo|"
    r"no entendi|no le entendi|me repite|repita|otra vez|perdon|"
    r"como dice|como dijo|que me pregunto|que es lo que se llama)[\s.,!¡¿?]*$"
)


def _sin_signos_iniciales(texto: str) -> str:
    """Quita los signos de apertura del español, que Whisper siempre agrega.

    Los patrones de saludo y aclaración están anclados al inicio de la frase, y
    un "¡" o un "¿" delante los rompía: "¡Buenos días!" no coincidía con
    "buenos dias" y el saludo se evaluaba como respuesta clínica.
    """
    return _normalizar(texto).lstrip("¡¿!? \t")


def es_saludo_puro(texto: str) -> bool:
    """El turno es SOLO un saludo, sin contenido clínico."""
    return bool(_SALUDOS.match(_sin_signos_iniciales(texto)))


def es_aclaracion(texto: str) -> bool:
    """El paciente pide que le repitan o expliquen la pregunta."""
    return bool(_ACLARACIONES.match(_sin_signos_iniciales(texto)))


def es_verificacion(texto: str) -> bool:
    """El paciente pregunta si se registró lo que dijo: "¿anotaste el número?".

    No es una consulta clínica y no debe buscarse en el corpus: se responde
    desde el estado del propio sistema. Contestarle "no tengo esa información
    en la documentación" a alguien que solo quiere saber si lo escucharon es
    justamente lo que hace sentir al paciente que habla con una máquina.
    """
    normalizado = _normalizar(texto)
    palabras = re.findall(r"[a-z]+", normalizado)
    # El verbo aparece en las dos primeras posiciones: "anotaste...", "me anotó...".
    return any(p in VERBOS_DE_VERIFICACION for p in palabras[:2])


def habla_un_tercero(texto: str) -> bool:
    normalizado = _normalizar(texto)
    return any(p.search(normalizado) for p in _TERCEROS)
