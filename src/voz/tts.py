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

# Cadencia. Se comparó contra es_AR-daniela-high (más natural pero factor de
# tiempo real 0.99 — al borde de entrecortarse en esta máquina) y se conservó
# es_MX-claude-high, cuatro veces más rápida. La naturalidad se gana con ritmo,
# no con timbre.
#
# Probado en llamada real: 1.12 se percibió arrastrado. La naturalidad viene de
# las PAUSAS entre oraciones, no de estirar cada palabra — estirarlas suena a
# grabación en cámara lenta. Velocidad neutra y silencios cortos entre frases.
VELOCIDAD = 1.0  # neutro: 1.12 sonaba arrastrado en llamada real

# Silencios entre unidades de habla. Piper no los produce solo: sin esto, todas
# las oraciones salen pegadas con la misma cadencia plana, que es lo que delata
# a una voz sintética antes que el timbre.
PAUSA_ORACION_MS = 220
PAUSA_PREGUNTA_MS = 300  # una pregunta pide más aire: invita a responder
PAUSA_COMA_MS = 130

# Números y símbolos que Piper lee mal o deletrea. Se expanden a palabras antes
# de sintetizar: "38.5" leído dígito por dígito arruina el momento más
# importante de la llamada.
DECENAS = {30: "treinta", 40: "cuarenta"}
UNIDADES = {
    0: "", 1: "uno", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco",
    6: "seis", 7: "siete", 8: "ocho", 9: "nueve",
}


def _temperatura_en_palabras(entero: int, decimal: str | None) -> str:
    """38.5 -> 'treinta y ocho punto cinco'. Piper lo deletrea si no."""
    decena = (entero // 10) * 10
    unidad = entero % 10
    if decena not in DECENAS:
        return f"{entero}"
    texto = DECENAS[decena]
    if unidad:
        texto += f" y {UNIDADES[unidad]}"
    if decimal and decimal != "0":
        texto += f" punto {UNIDADES[int(decimal)]}"
    return texto


_RE_TEMPERATURA = re.compile(r"\b(3[0-9]|4[0-2])[.,](\d)\b")
_RE_GRADOS = re.compile(r"\s*°\s*C\b")
_RE_ESCALA = re.compile(r"\b(\d{1,2})\s*/\s*10\b")


def preparar_para_voz(texto: str) -> str:
    """Reescribe el texto como se dice, no como se escribe.

    Las cifras clínicas son el contenido más importante de la llamada y son
    justo lo que un sintetizador arruina: "38.5 °C" leído como dígitos sueltos
    y una letra pierde al paciente en el peor momento.
    """
    texto = _RE_TEMPERATURA.sub(
        lambda m: _temperatura_en_palabras(int(m.group(1)), m.group(2)), texto
    )
    texto = _RE_GRADOS.sub(" grados", texto)
    texto = _RE_ESCALA.sub(lambda m: f"{m.group(1)} de diez", texto)
    return texto


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

    def _silencio(self, ms: int, muestra: FragmentoAudio) -> bytes:
        """PCM silencioso de la duración pedida, en el formato de la voz."""
        cuadros = int(muestra.frecuencia * ms / 1000)
        return b"\x00" * (cuadros * muestra.canales * muestra.ancho_muestra)

    def _sintetizar_texto(self, texto: str, *, pausa_final_ms: int = 0) -> FragmentoAudio | None:
        from piper import SynthesisConfig

        config = SynthesisConfig(length_scale=VELOCIDAD)
        trozos = list(self.voz.synthesize(preparar_para_voz(texto), config))
        if not trozos:
            return None
        fragmento = FragmentoAudio(
            pcm=b"".join(t.audio_int16_bytes for t in trozos),
            frecuencia=trozos[0].sample_rate,
            canales=trozos[0].sample_channels,
            ancho_muestra=trozos[0].sample_width,
        )
        if pausa_final_ms:
            fragmento.pcm += self._silencio(pausa_final_ms, fragmento)
        return fragmento

    def por_oraciones(self, texto: str) -> Iterator[FragmentoAudio]:
        """Emite un fragmento por oración, en orden, con su pausa al final.

        Consumir este iterador y reproducir cada fragmento apenas llega es lo que
        mantiene baja la latencia percibida.

        La pausa entre oraciones es lo que más acerca la voz a un hablante real:
        sin ella todas las frases salen pegadas con cadencia plana, que delata a
        un sintetizador antes que el timbre. Una pregunta lleva pausa más larga
        porque invita a responder, y el silencio es parte de la invitación.
        """
        oraciones = [o.strip() for o in FIN_DE_ORACION.split(texto.strip()) if o.strip()]
        for i, oracion in enumerate(oraciones):
            ultima = i == len(oraciones) - 1
            if ultima:
                pausa = 0
            elif oracion.rstrip().endswith("?"):
                pausa = PAUSA_PREGUNTA_MS
            else:
                pausa = PAUSA_ORACION_MS
            fragmento = self._sintetizar_texto(oracion, pausa_final_ms=pausa)
            if fragmento:
                yield fragmento

    def sintetizar(self, texto: str) -> FragmentoAudio | None:
        """Sintetiza el texto completo, respetando las pausas entre oraciones."""
        fragmentos = list(self.por_oraciones(texto))
        if not fragmentos:
            return None
        cabeza = fragmentos[0]
        return FragmentoAudio(
            pcm=b"".join(f.pcm for f in fragmentos),
            frecuencia=cabeza.frecuencia,
            canales=cabeza.canales,
            ancho_muestra=cabeza.ancho_muestra,
        )

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
