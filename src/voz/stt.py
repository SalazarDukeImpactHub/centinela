"""Transcripción de voz a texto con Whisper Large V3 en Groq.

Whisper es reconocimiento de voz, no un modelo de lenguaje que razone: la
restricción de modelos del reto aplica al razonamiento del agente, y el propio
documento declara que "el reconocimiento de voz [...] es decisión tuya". El
razonamiento corre en Llama 3.2 3B local, que sí está en la lista permitida.

Se usa Groq porque su latencia de transcripción es de decenas de milisegundos por
segundo de audio, y en una conversación de voz el presupuesto de latencia ya está
consumido casi entero por la inferencia local del modelo de lenguaje.

GOTCHA: Groq está detrás de Cloudflare y rechaza el User-Agent por defecto de
urllib con un HTTP 403 "error code: 1010". El error parece de credencial y no lo
es. Toda petición debe llevar User-Agent propio.
"""

from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODELO = "whisper-large-v3"
AGENTE = "postop-voice-agent/0.1"

# El corpus y los pacientes son colombianos: fijar el idioma evita que Whisper
# detecte mal en audios cortos o ruidosos, que es justo la capa 2 del dataset.
IDIOMA = "es"

# Vocabulario que Whisper transcribe mal sin contexto. El prompt de Whisper no es
# una instrucción: es un sesgo de vocabulario para el decodificador.
CONTEXTO = (
    "Llamada de seguimiento posoperatorio en Colombia. Términos frecuentes: "
    "apendicectomía, colecistectomía, colectomía, mastectomía, artroplastia, "
    "herida quirúrgica, drenaje, secreción, eritema, escalofríos, febrícula, "
    "analgésico, acetaminofén, tramadol, EPS."
)


class ErrorTranscripcion(RuntimeError):
    pass


@dataclass
class Transcripcion:
    texto: str
    segundos_audio: float
    latencia_ms: float


def _multipart(campos: dict[str, str], archivo: Path) -> tuple[bytes, str]:
    """Arma un cuerpo multipart/form-data sin dependencias externas."""
    frontera = f"----postop{uuid.uuid4().hex}"
    tipo = mimetypes.guess_type(archivo.name)[0] or "application/octet-stream"
    partes: list[bytes] = []

    for clave, valor in campos.items():
        partes.append(
            f"--{frontera}\r\n"
            f'Content-Disposition: form-data; name="{clave}"\r\n\r\n'
            f"{valor}\r\n".encode()
        )

    partes.append(
        f"--{frontera}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{archivo.name}"\r\n'
        f"Content-Type: {tipo}\r\n\r\n".encode()
    )
    partes.append(archivo.read_bytes())
    partes.append(f"\r\n--{frontera}--\r\n".encode())

    return b"".join(partes), f"multipart/form-data; boundary={frontera}"


def transcribir(audio: Path, *, token: str | None = None) -> Transcripcion:
    """Transcribe un archivo de audio. Devuelve texto y duración para el costo."""
    import time

    clave = token or os.environ.get("GROQ_API_KEY", "")
    if not clave:
        raise ErrorTranscripcion("GROQ_API_KEY no está definida")
    if not audio.exists():
        raise ErrorTranscripcion(f"no existe {audio}")

    cuerpo, tipo = _multipart(
        {
            "model": MODELO,
            "language": IDIOMA,
            "prompt": CONTEXTO,
            "response_format": "verbose_json",
            "temperature": "0",
        },
        audio,
    )

    peticion = urllib.request.Request(
        URL,
        data=cuerpo,
        headers={
            "Authorization": f"Bearer {clave}",
            "Content-Type": tipo,
            "User-Agent": AGENTE,  # sin esto, Cloudflare devuelve 403/1010
        },
    )

    inicio = time.perf_counter()
    try:
        with urllib.request.urlopen(peticion, timeout=120) as respuesta:
            datos = json.load(respuesta)
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", "replace")[:300]
        raise ErrorTranscripcion(f"HTTP {exc.code}: {detalle}") from exc
    except urllib.error.URLError as exc:
        raise ErrorTranscripcion(f"red: {exc.reason}") from exc
    latencia = (time.perf_counter() - inicio) * 1000

    return Transcripcion(
        texto=datos.get("text", "").strip(),
        segundos_audio=float(datos.get("duration", 0.0)),
        latencia_ms=latencia,
    )
