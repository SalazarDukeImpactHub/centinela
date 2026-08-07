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


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")


def contiene_pregunta(texto: str) -> bool:
    normalizado = _normalizar(texto)
    return any(p.search(normalizado) for p in _INTERROGATIVAS)


def habla_un_tercero(texto: str) -> bool:
    normalizado = _normalizar(texto)
    return any(p.search(normalizado) for p in _TERCEROS)
