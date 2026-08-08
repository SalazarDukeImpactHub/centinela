"""Cliente del modelo de razonamiento local (Ollama).

Modelo declarado: llama3.2:3b — de la lista cerrada de modelos permitidos por las
bases del reto. La elección está forzada y documentada: de los cuatro permitidos,
Google retiró Gemini 1.5 Flash y Groq retiró Llama 3.1 70B durante 2026, de modo
que los dos únicos obtenibles hoy son los locales.

Se descartó llama3.2:1b por calidad, no por velocidad: ante "escalofríos"
respondió "escarmiento" e inventó palabras. En contexto clínico eso es
inadmisible. El 3B responde coherente a costa de la mitad de la velocidad.

Presupuesto de tokens — medido en i3-1005G1 de 2 núcleos:
    llama3.2:3b   5.6 tok/s   carga inicial 14.7s (una sola vez)

A esa velocidad cada token generado cuesta ~180 ms. Por eso el modelo produce
SOLO extracción estructurada compacta, nunca prosa larga: la decisión clínica ya
está resuelta en código y el texto hablado se mantiene corto a propósito.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

URL_BASE = "http://localhost:11434"
MODELO = "llama3.2:3b"
TIMEOUT = 180


@dataclass
class Respuesta:
    texto: str
    tokens_entrada: int
    tokens_salida: int
    latencia_ms: float
    modelo: str

    @property
    def tokens_por_segundo(self) -> float:
        segundos = self.latencia_ms / 1000
        return self.tokens_salida / segundos if segundos else 0.0


class ModeloNoDisponible(RuntimeError):
    """Ollama no responde o el modelo no está descargado."""


class ClienteLocal:
    def __init__(self, modelo: str = MODELO, url: str = URL_BASE) -> None:
        self.modelo = modelo
        self.url = url

    def _pedir(self, ruta: str, cuerpo: dict) -> dict:
        req = urllib.request.Request(
            f"{self.url}{ruta}",
            data=json.dumps(cuerpo).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.load(resp)
        except urllib.error.URLError as exc:
            raise ModeloNoDisponible(
                f"Ollama no responde en {self.url}. ¿Está corriendo 'ollama serve'?"
            ) from exc

    def disponible(self) -> bool:
        try:
            datos = self._pedir("/api/show", {"model": self.modelo})
            return "error" not in datos
        except ModeloNoDisponible:
            return False

    def generar(
        self,
        prompt: str,
        *,
        sistema: str | None = None,
        esquema: dict | None = None,
        max_tokens: int = 200,
        temperatura: float = 0.0,
    ) -> Respuesta:
        """Genera texto. Con `esquema`, Ollama fuerza JSON válido contra él.

        Forzar el esquema en lugar de pedirlo por prompt es deliberado: un modelo
        de 3B obedece instrucciones de formato de manera poco fiable, y un JSON
        malformado a mitad de una llamada de voz no tiene recuperación elegante.
        La temperatura es 0 porque esto es extracción, no redacción.
        """
        cuerpo: dict = {
            "model": self.modelo,
            "prompt": prompt,
            "stream": False,
            # num_thread 2: la extracción corre en segundo plano mientras el
            # turno siguiente necesita CPU para sintetizar voz. Medido: con el
            # modelo acaparando los 4 hilos lógicos, un turno que llegaba durante
            # la extracción tardó 58 s. Dejarle 2 hilos al resto del pipeline
            # alarga la extracción —que a nadie apura: es asíncrona— y protege
            # la latencia percibida, que es la métrica que puntúa.
            "options": {
                "temperature": temperatura,
                "num_predict": max_tokens,
                "num_thread": 2,
            },
        }
        if sistema:
            cuerpo["system"] = sistema
        if esquema:
            cuerpo["format"] = esquema

        inicio = time.perf_counter()
        datos = self._pedir("/api/generate", cuerpo)
        latencia = (time.perf_counter() - inicio) * 1000

        return Respuesta(
            texto=datos.get("response", "").strip(),
            tokens_entrada=datos.get("prompt_eval_count", 0),
            tokens_salida=datos.get("eval_count", 0),
            latencia_ms=latencia,
            modelo=self.modelo,
        )
