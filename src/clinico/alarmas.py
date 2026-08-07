"""Detección de síntomas de alarma. Determinista, en código.

Por qué no lo hace el modelo: se midió. Con la lista de síntomas en el prompt,
llama3.2:3b la copiaba en lugar de leer al paciente — inventó `dolor_toracico`
en turnos donde el paciente hablaba de fiebre, de la herida o del dolor abdominal.
Un síntoma de alarma inventado escala por el motivo equivocado: el semáforo puede
acertar por accidente mientras el registro clínico queda falseado, y la rúbrica
audita el registro.

Los síntomas de alarma son la señal de mayor consecuencia del sistema. Ponerlos en
manos de un modelo de 3B que demostró copiar el prompt sería construir sobre arena.

Mismo principio que el motor de escalamiento: lo que no se puede permitir que falle
no se le pide al modelo.
"""

from __future__ import annotations

import re
import unicodedata

# Cada síntoma se describe por como REALMENTE habla un paciente colombiano.
# Las expresiones se comparan sobre texto sin tildes y en minúsculas.
PATRONES: dict[str, list[str]] = {
    "dificultad_respiratoria": [
        r"no puedo respirar",
        r"me falta (el )?aire",
        r"me ahogo",
        r"cuesta respirar",
        r"dificultad para respirar",
        r"agitad[oa]",
        r"sin aliento",
    ],
    "dolor_toracico": [
        r"dolor en el pecho",
        r"me duele el pecho",
        r"opresion en el pecho",
        r"aprieta el pecho",
        r"punzada en el pecho",
    ],
    "sangrado_activo": [
        r"sangr(a|ando|e mucho)",
        r"esta botando sangre",
        r"no para de sangrar",
        r"mucha sangre",
        r"se empapo de sangre",
    ],
    "vomito_persistente": [
        r"vomit(o|ando|e) (todo|mucho|sin parar)",
        r"no (puedo|logro) retener",
        r"devuelvo todo",
        r"llevo (\w+ )?dias vomitando",
        r"no para de vomitar",
    ],
    "desorientacion": [
        r"no se donde estoy",
        r"esta confundid[oa]",
        r"no reconoce",
        r"habla incoherenc",
        r"desorientad[oa]",
    ],
    "sincope": [
        r"me desmay",
        r"se desmay",
        r"perdi el conocimiento",
        r"me dio un mareo y me cai",
        r"me desvaneci",
    ],
    "dehiscencia_herida": [
        r"se (me )?abrio la herida",
        r"se solto (el|la) punt",
        r"se reventaron los puntos",
        r"la herida se abrio",
        r"se salieron los puntos",
    ],
}

_COMPILADOS = {
    sintoma: [re.compile(p) for p in patrones] for sintoma, patrones in PATRONES.items()
}


def _normalizar(texto: str) -> str:
    """Minúsculas y sin tildes: el paciente escribe y habla sin acentuar."""
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")


def detectar(texto: str) -> list[str]:
    """Devuelve los síntomas de alarma presentes en el habla del paciente.

    Calibrado hacia la sensibilidad: ante un posible síntoma de alarma preferimos
    escalar de más. El costo de un falso positivo es una llamada de verificación;
    el de un falso negativo, un paciente.
    """
    normalizado = _normalizar(texto)
    encontrados = [
        sintoma
        for sintoma, patrones in _COMPILADOS.items()
        if any(p.search(normalizado) for p in patrones)
    ]
    return encontrados
