"""Detección de dolor y movilidad. Determinista, en código.

Mismo motivo que `fiebre.py` y `herida.py`: la extracción del modelo corre en
segundo plano y llega un turno tarde, o no llega. Dos fallas medidas en llamada
real lo obligaron:

  - El paciente dijo "Siete." y recibió "no le capté el número del dolor": el
    detector solo miraba dígitos, y por teléfono la gente dice las cifras en
    palabras. Con "Número 7" sí funcionó, pero el agente igual respondió
    "Un 7 de dolor, anotado" SIN GUARDARLO — el resumen cerró con "sin dato".
    Un eco que afirma haber anotado algo que no anotó es peor que no ecoar.

  - El paciente dijo "No me puedo mover" y el turno se interpretó como una
    pregunta (por el "puedo") y se buscó en el corpus. La movilidad quedó
    "desconocido" con el paciente diciendo que no puede moverse.
"""

from __future__ import annotations

import re
import unicodedata

# Números del 0 al 10 dichos en palabras, como llegan por teléfono.
PALABRAS_NUMERO = {
    "cero": 0, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
}

# Escala verbal → NRS. El paciente casi nunca da un número espontáneamente.
ESCALA_VERBAL = {
    "insoportable": 10, "terrible": 9, "horrible": 9,
    "muy fuerte": 8, "harto": 8, "resto": 8, "muchisimo": 8,
    "fuerte": 7, "bastante": 7, "mucho": 7,
    "moderado": 5, "regular": 5, "mas o menos": 5, "medio": 5,
    "aguantable": 3, "leve": 3, "poquito": 2, "poco": 2, "poquita": 2,
    "nada": 0, "ninguno": 0, "no me duele": 0, "ya no duele": 0,
}

MOVILIDAD_INCAPACITANTE = [
    r"\bno (me )?puedo (mover|caminar|levantar|parar)",
    r"\bno (logro|consigo) (moverme|caminar|levantarme|pararme)",
    r"\bcasi no (puedo|me puedo) (mover|caminar|levantar)",
    r"\bno me (muevo|levanto)\b",
    r"\bnecesito (que me ayuden|ayuda) para",
    r"\bno me aguanto (de pie|parado|parada)",
    r"\bpostrad|\ben cama todo\b",
]

MOVILIDAD_LIMITADA = [
    r"\bcon (dificultad|ayuda|bast[oó]n|caminador|muletas)",
    r"\bme cuesta (caminar|moverme|levantarme|pararme)",
    r"\bdespacio\b",
    r"\bpoquito a poco\b",
    r"\bapenas (camino|me muevo)",
    r"\blento\b",
]

MOVILIDAD_NORMAL = [
    r"\bcamino (bien|normal|sin problema|sola|solo)",
    r"\bme muevo (bien|normal|sin problema)",
    r"\bsin (problema|dificultad|ayuda)\b",
    r"\bnormal\b",
    r"\bbien\b",
    r"\bpuedo caminar\b",
    r"\bme levanto (bien|sola|solo|sin ayuda)",
]

_INCAPACITANTE = [re.compile(p) for p in MOVILIDAD_INCAPACITANTE]
_LIMITADA = [re.compile(p) for p in MOVILIDAD_LIMITADA]
_NORMAL_MOV = [re.compile(p) for p in MOVILIDAD_NORMAL]

# "cero" a "diez" en palabras, o un dígito suelto de 0 a 10.
_RE_DIGITO = re.compile(r"\b(10|[0-9])\b")
_RE_PALABRA = re.compile(
    r"\b(cero|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\b"
)


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")


def nivel_dolor(texto: str) -> int | None:
    """Intensidad del dolor 0-10 según lo dicho, o None si no se puede saber.

    Acepta dígitos ("un 7"), palabras ("siete") y escala verbal ("me duele
    harto"). Sin esto, la mitad de las respuestas por teléfono se perdían.
    """
    normalizado = _normalizar(texto)

    # Una negación de dolor es un cero explícito, no una ausencia de dato.
    if re.search(r"\bno (me )?(duele|tengo dolor)\b|\bnada de dolor\b", normalizado):
        return 0

    m = _RE_DIGITO.search(normalizado)
    if m:
        return int(m.group(1))

    m = _RE_PALABRA.search(normalizado)
    if m:
        return PALABRAS_NUMERO[m.group(1)]

    # Escala verbal: se prueba de mayor a menor longitud para que "muy fuerte"
    # gane sobre "fuerte" y "mas o menos" sobre "medio".
    for frase in sorted(ESCALA_VERBAL, key=len, reverse=True):
        if re.search(rf"\b{re.escape(frase)}\b", normalizado):
            return ESCALA_VERBAL[frase]
    return None


def estado_movilidad(texto: str) -> str | None:
    """Estado de movilidad según lo dicho: valor del enum `Movilidad`, o None.

    El orden es clínico: la incapacidad manda. "No me puedo mover" y "puedo
    caminar" en la misma llamada no se promedian — la primera es la queja.
    """
    normalizado = _normalizar(texto)
    if any(p.search(normalizado) for p in _INCAPACITANTE):
        return "incapacitante_nueva"
    if any(p.search(normalizado) for p in _LIMITADA):
        return "limitada_esperada"
    if any(p.search(normalizado) for p in _NORMAL_MOV):
        return "normal"
    return None
