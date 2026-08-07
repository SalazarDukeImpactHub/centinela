"""Síntesis de voz con Piper, local y en streaming.

Piper corre en CPU sin GPU y empieza a emitir audio casi de inmediato. Esa
propiedad es la que importa acá: en una conversación, lo que el paciente percibe
como demora es el tiempo hasta el PRIMER sonido, no hasta el último.

Por eso la síntesis se hace por oraciones y se emite a medida que se produce. El
paciente escucha la primera frase mientras todavía se sintetiza la segunda, y la
latencia percibida cae a una fracción del tiempo total de síntesis.

Voz: es_MX-claude-high. Español mexicano, el acento neutro más cercano al
colombiano entre las voces disponibles de Piper.
"""

from __future__ import annotations

import re
import time
import wave
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from piper import PiperVoice

RAIZ = Path(__file__).resolve().parents[2]
VOZ_POR_DEFECTO = RAIZ / "models" / "piper" / "es_MX-claude-high.onnx"

# Corte por oración: el audio empieza a sonar apenas está lista la primera.
FIN_DE_ORACION = re.compile(r"(?<=[.!?])\s+")


@dataclass
class FragmentoAudio:
    pcm: bytes
    frecuencia: int
    canales: int
    ancho_muestra: int

    @property
    def segundos(self) -> float:
        bytes_por_segundo = self.frecuencia * self.canales * self.ancho_muestra
        return len(self.pcm) / bytes_por_segundo if bytes_por_segundo else 0.0


class Sintetizador:
    """Envuelve Piper. La voz se carga una sola vez y queda residente."""

    def __init__(self, modelo: Path | None = None) -> None:
        ruta = modelo or VOZ_POR_DEFECTO
        if not ruta.exists():
            raise FileNotFoundError(
                f"No existe la voz {ruta}.\n"
                "Descargala con:\n"
                "  python -m piper.download_voices es_MX-claude-high --data-dir models/piper"
            )
        self.voz = PiperVoice.load(str(ruta))
        self._caliente = False

    def calentar(self) -> float:
        """Fuerza la primera inferencia. Devuelve cuánto costó, en milisegundos.

        MEDIDO: la primera síntesis tras cargar la voz tarda ~28 segundos, contra
        ~350 ms las siguientes. Es el costo de optimización del grafo ONNX, y se
        paga una sola vez.

        Sin este calentamiento explícito, ese costo lo paga el PRIMER TURNO DE CADA
        LLAMADA: el paciente saluda y espera medio minuto. Con él, se paga al
        levantar el servicio, antes de que nadie llame.
        """
        if self._caliente:
            return 0.0
        inicio = time.perf_counter()
        self._sintetizar_texto("Hola.")
        self._caliente = True
        return (time.perf_counter() - inicio) * 1000

    def _sintetizar_texto(self, texto: str) -> FragmentoAudio | None:
        trozos = list(self.voz.synthesize(texto))
        if not trozos:
            return None
        return FragmentoAudio(
            pcm=b"".join(t.audio_int16_bytes for t in trozos),
            frecuencia=trozos[0].sample_rate,
            canales=trozos[0].sample_channels,
            ancho_muestra=trozos[0].sample_width,
        )

    def por_oraciones(self, texto: str) -> Iterator[FragmentoAudio]:
        """Emite un fragmento por oración, en orden.

        Consumir este iterador y reproducir cada fragmento apenas llega es lo que
        mantiene baja la latencia percibida.
        """
        for oracion in FIN_DE_ORACION.split(texto.strip()):
            if not oracion.strip():
                continue
            fragmento = self._sintetizar_texto(oracion.strip())
            if fragmento:
                yield fragmento

    def sintetizar(self, texto: str) -> FragmentoAudio | None:
        """Sintetiza el texto completo de una vez."""
        return self._sintetizar_texto(texto)

    def medir_primer_audio(self, texto: str) -> tuple[float, float]:
        """Devuelve (ms hasta el primer fragmento, ms hasta completar todo).

        La primera cifra es la que se reporta como latencia percibida; la segunda
        sirve para saber si la síntesis alcanza a ir más rápido que la reproducción.
        """
        inicio = time.perf_counter()
        primero: float | None = None
        for _ in self.por_oraciones(texto):
            if primero is None:
                primero = (time.perf_counter() - inicio) * 1000
        total = (time.perf_counter() - inicio) * 1000
        return (primero or total), total

    def a_wav(self, texto: str, destino: Path) -> Path:
        """Escribe el texto sintetizado a un WAV. Para evidencia y depuración."""
        fragmento = self.sintetizar(texto)
        if fragmento is None:
            raise ValueError("la síntesis no produjo audio")
        destino.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destino), "wb") as w:
            w.setnchannels(fragmento.canales)
            w.setsampwidth(fragmento.ancho_muestra)
            w.setframerate(fragmento.frecuencia)
            w.writeframes(fragmento.pcm)
        return destino
