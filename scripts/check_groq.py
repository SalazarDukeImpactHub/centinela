"""Verificación de conectividad y disponibilidad de modelo en Groq.

Comprueba tres cosas antes de que la arquitectura dependa de ellas:
  1. La credencial funciona.
  2. El modelo de razonamiento elegido sigue disponible (no fue retirado).
  3. Whisper Large V3 sigue disponible para STT.

La credencial NUNCA se imprime, ni completa ni parcial: este script está pensado
para correr con la salida a la vista.

Uso:
    python scripts/check_groq.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# Modelos que la arquitectura necesita. La lista de modelos de razonamiento
# permitidos por las bases del reto es cerrada; estos son los candidatos Groq.
RAZONAMIENTO_PREFERIDOS = [
    "llama-3.1-70b-versatile",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]
STT_PREFERIDOS = ["whisper-large-v3", "whisper-large-v3-turbo"]


def cargar_env(ruta: Path) -> None:
    """Carga un .env simple en os.environ sin dependencias externas."""
    if not ruta.exists():
        raise SystemExit(f"No existe {ruta}. Creá el archivo con GROQ_API_KEY=...")
    for linea in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip("\"'"))


def pedir(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main() -> int:
    cargar_env(RAIZ / ".env")
    token = os.environ.get("GROQ_API_KEY", "")
    if not token:
        raise SystemExit("GROQ_API_KEY no está definida en .env")
    print(f"Credencial encontrada (longitud {len(token)}). No se imprime su valor.")

    try:
        datos = pedir("https://api.groq.com/openai/v1/models", token)
    except urllib.error.HTTPError as exc:
        print(f"\nFALLA HTTP {exc.code}: {exc.reason}")
        if exc.code == 401:
            print("La credencial fue rechazada. Verificá que copiaste la key completa.")
        return 1
    except urllib.error.URLError as exc:
        print(f"\nFALLA de red: {exc.reason}")
        return 1

    disponibles = sorted(m["id"] for m in datos.get("data", []))
    print(f"Conexión OK. {len(disponibles)} modelos disponibles.\n")

    razonamiento = [m for m in RAZONAMIENTO_PREFERIDOS if m in disponibles]
    stt = [m for m in STT_PREFERIDOS if m in disponibles]

    print("Razonamiento (candidatos permitidos por las bases):")
    for m in RAZONAMIENTO_PREFERIDOS:
        print(f"  {'OK   ' if m in disponibles else 'AUSENTE'}  {m}")
    print("\nSTT:")
    for m in STT_PREFERIDOS:
        print(f"  {'OK   ' if m in disponibles else 'AUSENTE'}  {m}")

    print("\nTodos los modelos Llama disponibles en la cuenta:")
    for m in disponibles:
        if "llama" in m.lower():
            print(f"  {m}")

    if not razonamiento:
        print("\nBLOQUEANTE: ningún modelo de razonamiento candidato está disponible.")
        print("Revisar la lista de arriba y reevaluar la decisión de arquitectura.")
        return 1
    if not stt:
        print("\nADVERTENCIA: Whisper no disponible en Groq. Evaluar STT alternativo.")

    print(f"\nOK — usar '{razonamiento[0]}' para razonamiento.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
