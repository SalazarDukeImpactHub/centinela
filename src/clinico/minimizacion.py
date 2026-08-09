"""Detección de minimización sistemática. Determinista, en código.

MEDIDO sobre los 160 casos del dataset, contando marcadores de minimización por
conversación:

    verde     media 2,7 por caso
    amarillo  media 3,8
    rojo      media 6,4

El paciente que está PEOR es el que más resta importancia. Es contraintuitivo
hasta que uno lo piensa: quien tiene 9 de dolor y dice "un poquito molesto, uno
aguanta" no está informando mal — está aguantando, que es otra cosa.

Para qué sirve: los dos falsos negativos que quedaban en el banco conversacional
son exactamente eso. Pacientes cuyo cuadro real era rojo y que reportaron
verde con tanta consistencia que ningún extractor podía sacar otra cosa. La
señal no está en LO QUE dicen sino en CÓMO lo dicen.

Esto NO reemplaza al motor de escalamiento: lo alimenta con una señal más. La
decisión sigue siendo determinista y auditable.
"""

from __future__ import annotations

import re
import unicodedata

# Cómo minimiza un paciente colombiano. Se agrupan por función para poder
# explicar en el registro POR QUÉ se consideró que estaba minimizando.
MARCADORES = {
    "resta_intensidad": [
        r"\bun poquit[oa]\b",
        r"\bpoquit[oa] no mas\b",
        r"\bapenas\b",
        r"\bcasi no\b",
        r"\blevecit[oa]\b",
        r"\bnada del otro mundo\b",
    ],
    "normaliza": [
        r"\bes normal\b",
        r"\beso es normal\b",
        r"\bnormal despues de\b",
        r"\buno ya sabe\b",
        r"\bcomo uno se siente despues\b",
        r"\bes de la cicatrizacion\b",
        r"\bsera (por|de) (los medicamentos|la operacion|el estres|los nervios)\b",
    ],
    "aguanta": [
        r"\buno aguanta\b",
        r"\bse aguanta\b",
        r"\bya se me pasa\b",
        r"\bahi voy\b",
        r"\bmas o menos, ahi normal\b",
    ],
    "tranquiliza_al_agente": [
        r"\bno se preocupe\b",
        r"\btranquil[oa] doctor",
        r"\bno es nada\b",
        r"\bnada grave\b",
        r"\bno hay problema\b",
    ],
}

_COMPILADOS = {
    grupo: [re.compile(p) for p in patrones] for grupo, patrones in MARCADORES.items()
}

# Umbral calibrado contra los 160 casos. Ver `scripts/banco_conversacional.py`.
MARCADORES_PARA_SOSPECHA = 4


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")


def marcadores_en(texto: str) -> list[str]:
    """Grupos de minimización presentes en un turno del paciente."""
    normalizado = _normalizar(texto)
    return [
        grupo
        for grupo, patrones in _COMPILADOS.items()
        if any(p.search(normalizado) for p in patrones)
    ]


def contar(texto: str) -> int:
    """Cuántos marcadores de minimización trae un turno."""
    normalizado = _normalizar(texto)
    return sum(
        1
        for patrones in _COMPILADOS.values()
        for p in patrones
        if p.search(normalizado)
    )
