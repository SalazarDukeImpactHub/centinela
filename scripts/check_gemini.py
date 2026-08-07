"""Verificación de disponibilidad de Gemini 1.5 Flash.

Existe por la misma razón que `check_groq.py`: la lista de modelos permitidos por
las bases del reto quedó desactualizada respecto a lo que los proveedores sirven
hoy. `llama-3.1-70b` ya había sido retirado de Groq. No se asume nada.

Comprueba:
  1. La credencial funciona.
  2. Gemini 1.5 Flash sigue disponible (no fue retirado).
  3. Responde en español y con qué latencia.

La credencial NUNCA se imprime.

Uso:
    python scripts/check_gemini.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
BASE = "https://generativelanguage.googleapis.com/v1beta"

# Candidatos en orden de preferencia. El primero es el que las bases nombran.
PREFERIDOS = [
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash-8b",
    "gemini-1.5-pro",
]

# Cloudflare y varios gateways rechazan el User-Agent por defecto de urllib.
# Groq devolvía HTTP 403 "error code: 1010" por esto — no era la credencial.
CABECERAS = {"User-Agent": "postop-voice-agent/0.1", "Content-Type": "application/json"}


def cargar_env(ruta: Path) -> None:
    if not ruta.exists():
        raise SystemExit(f"No existe {ruta}")
    for linea in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip("\"'"))


def pedir(url: str, cuerpo: dict | None = None) -> dict:
    datos = json.dumps(cuerpo).encode() if cuerpo else None
    req = urllib.request.Request(url, data=datos, headers=CABECERAS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def main() -> int:
    cargar_env(RAIZ / ".env")
    token = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    if not token:
        raise SystemExit("Falta GOOGLE_API_KEY (o GEMINI_API_KEY) en .env")
    print(f"Credencial encontrada (longitud {len(token)}). No se imprime su valor.")

    try:
        datos = pedir(f"{BASE}/models?key={token}&pageSize=200")
    except urllib.error.HTTPError as exc:
        cuerpo = exc.read().decode("utf-8", "replace")
        print(f"\nFALLA HTTP {exc.code}\n{cuerpo[:400]}")
        return 1
    except urllib.error.URLError as exc:
        print(f"\nFALLA de red: {exc.reason}")
        return 1

    disponibles = {
        m["name"].removeprefix("models/")
        for m in datos.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    }
    print(f"Conexión OK. {len(disponibles)} modelos con generateContent.\n")

    print("Candidatos de las bases:")
    for m in PREFERIDOS:
        print(f"  {'OK     ' if m in disponibles else 'AUSENTE'}  {m}")

    elegido = next((m for m in PREFERIDOS if m in disponibles), None)
    if not elegido:
        print("\nBLOQUEANTE: ningún Gemini candidato disponible.")
        print("Modelos Gemini en la cuenta:")
        for m in sorted(d for d in disponibles if "gemini" in d):
            print(f"  {m}")
        return 1

    prompt = (
        "Un paciente posoperatorio dice: 'me duele harto la herida y anoche tuve "
        "escalofríos'. Responde en una sola frase, en español."
    )
    cuerpo = {"contents": [{"parts": [{"text": prompt}]}]}
    inicio = time.perf_counter()
    try:
        r = pedir(f"{BASE}/models/{elegido}:generateContent?key={token}", cuerpo)
    except urllib.error.HTTPError as exc:
        print(f"\nFALLA en generación HTTP {exc.code}\n{exc.read().decode()[:400]}")
        return 1
    latencia = (time.perf_counter() - inicio) * 1000

    texto = r["candidates"][0]["content"]["parts"][0]["text"].strip()
    uso = r.get("usageMetadata", {})
    print(f"\nGeneración con '{elegido}': {latencia:.0f}ms")
    print(f"  tokens in={uso.get('promptTokenCount')} out={uso.get('candidatesTokenCount')}")
    print(f"  respuesta: {texto[:200]}")

    print(f"\nOK — usar '{elegido}' para razonamiento.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
