"""Carga de configuración desde .env.

Existe como módulo propio porque la carga estaba duplicada en los scripts y
AUSENTE en el servidor: la API arrancaba sin credenciales y solo lo descubría al
primer turno de voz, con un error 500 y la interfaz muda.

Cargar temprano y verificar al arrancar es más barato que diagnosticarlo durante
una demostración.
"""

from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ENV = RAIZ / ".env"

# Claves que el sistema necesita, con qué se rompe si falta cada una.
REQUERIDAS = {
    "GROQ_API_KEY": "transcripción de voz (Whisper). Sin ella no hay conversación.",
}


def cargar(ruta: Path | None = None) -> Path | None:
    """Carga el .env en el entorno. Devuelve la ruta usada, o None si no existe.

    No pisa variables ya definidas: el entorno del proceso manda sobre el archivo,
    que es lo que permite inyectar credenciales en un contenedor sin tocar el .env.
    """
    archivo = ruta or ENV
    if not archivo.exists():
        return None

    for linea in archivo.read_text(encoding="utf-8", errors="replace").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip("\"'"))
    return archivo


def faltantes() -> dict[str, str]:
    """Claves requeridas que no están definidas, con su consecuencia."""
    return {k: v for k, v in REQUERIDAS.items() if not os.environ.get(k)}
