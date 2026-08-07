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


def refiere_fiebre_sin_cifra(texto: str) -> bool:
    """Dijo tener fiebre pero no dio el número. Hay que pedírselo en este turno."""
    return menciona_fiebre(texto) and not tiene_cifra(texto)
