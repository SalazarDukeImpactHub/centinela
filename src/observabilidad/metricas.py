"""Instrumentación de latencia, consumo y costo.

El README de la entrega debe reportar métricas obligatorias, y la rúbrica advierte
que se contrastan contra los logs de la sesión evaluada: *"Reportar números que no
se sostienen es peor que no reportarlos"*. Por eso las métricas se derivan del log
estructurado y no se calculan aparte.

Definición de latencia, tomada literal del enunciado: se mide **desde que el
paciente termina de hablar hasta que empieza a sonar el audio del agente**. No es
el tiempo del modelo ni el de la síntesis: es lo que el paciente percibe como
silencio. Cualquier otra medición infla el resultado a favor de uno.

Se instrumenta desde el primer commit del pipeline de voz justamente para no tener
que reconstruirla después contra un sistema ya armado.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median


@dataclass
class Etapa:
    """Tramo cronometrado dentro de un turno."""

    nombre: str
    ms: float


@dataclass
class TurnoMetrica:
    """Todo lo medible de un turno. Es la fuente de las métricas del README."""

    llamada_id: str
    turno: int
    # Latencia percibida: fin del habla del paciente -> inicio del audio del agente
    latencia_ms: float = 0.0
    etapas: list[Etapa] = field(default_factory=list)
    tokens_entrada: int = 0
    tokens_salida: int = 0
    llamadas_modelo: int = 0
    consultas_rag: int = 0
    modelo: str = ""
    escalado: bool = False
    semaforo: str = ""
    # Trazabilidad clínica POR TURNO. Sin esto, el registro guardaba números
    # pero no la historia: el semáforo de los primeros turnos se perdía en
    # cuanto cambiaba, y las citas de cada respuesta no quedaban en ninguna
    # parte. La rúbrica exige que cada respuesta pueda rastrearse hasta su
    # documento — eso solo se audita si quedó escrito.
    motivos: list[str] = field(default_factory=list)
    hallazgos: list[dict] = field(default_factory=list)
    citas: list[dict] = field(default_factory=list)
    grounding: dict | None = None
    texto_paciente: str = ""
    texto_agente: str = ""

    def registrar_etapa(self, nombre: str, ms: float) -> None:
        self.etapas.append(Etapa(nombre, ms))

    def como_dict(self) -> dict:
        return asdict(self)


class RegistroLlamada:
    """Acumula las métricas de una llamada y las persiste en JSONL.

    Una línea por turno. El formato es deliberadamente simple: el jurado tiene que
    poder abrir el archivo y comprobar que los números del README salen de ahí.
    """

    def __init__(self, llamada_id: str, ruta: Path) -> None:
        self.llamada_id = llamada_id
        self.ruta = ruta
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.turnos: list[TurnoMetrica] = []

    def nuevo_turno(self) -> TurnoMetrica:
        metrica = TurnoMetrica(llamada_id=self.llamada_id, turno=len(self.turnos) + 1)
        self.turnos.append(metrica)
        return metrica

    def persistir(self, metrica: TurnoMetrica) -> None:
        with self.ruta.open("a", encoding="utf-8") as f:
            f.write(json.dumps(metrica.como_dict(), ensure_ascii=False) + "\n")

    # -- Agregados para el README -------------------------------------------------

    @property
    def latencias(self) -> list[float]:
        return [t.latencia_ms for t in self.turnos if t.latencia_ms > 0]

    def p50(self) -> float:
        return median(self.latencias) if self.latencias else 0.0

    def p95(self) -> float:
        """Percentil 95 por interpolación lineal, como numpy pero sin la dependencia."""
        if not self.latencias:
            return 0.0
        ordenadas = sorted(self.latencias)
        if len(ordenadas) == 1:
            return ordenadas[0]
        posicion = 0.95 * (len(ordenadas) - 1)
        bajo = int(posicion)
        alto = min(bajo + 1, len(ordenadas) - 1)
        peso = posicion - bajo
        return ordenadas[bajo] * (1 - peso) + ordenadas[alto] * peso

    def totales(self) -> dict[str, float | int]:
        return {
            "turnos": len(self.turnos),
            "latencia_p50_ms": round(self.p50(), 1),
            "latencia_p95_ms": round(self.p95(), 1),
            "tokens_entrada": sum(t.tokens_entrada for t in self.turnos),
            "tokens_salida": sum(t.tokens_salida for t in self.turnos),
            "llamadas_modelo": sum(t.llamadas_modelo for t in self.turnos),
            "consultas_rag": sum(t.consultas_rag for t in self.turnos),
        }


@contextmanager
def cronometro(metrica: TurnoMetrica, nombre: str):
    """Cronometra una etapa y la registra en el turno.

    Uso:
        with cronometro(m, "stt"):
            texto = transcribir(audio)
    """
    inicio = time.perf_counter()
    try:
        yield
    finally:
        metrica.registrar_etapa(nombre, (time.perf_counter() - inicio) * 1000)


# -- Costo -----------------------------------------------------------------------

# El modelo de razonamiento corre local, así que su costo monetario es cero. La
# rúbrica exige extrapolar a precios de API productiva y explicar el cálculo, de
# modo que se declara la referencia usada en lugar de informar "gratis".
#
# Referencia: precio público de Llama 3.1 8B en proveedores de inferencia serverless
# a agosto de 2026, como sustituto comparable de Llama 3.2 3B (no tiene precio de
# API porque su despliegue habitual es local).
USD_POR_MILLON_ENTRADA = 0.05
USD_POR_MILLON_SALIDA = 0.08

# Whisper Large V3 en Groq, facturado por hora de audio.
USD_POR_HORA_AUDIO = 0.111


def costo_estimado(
    tokens_entrada: int, tokens_salida: int, segundos_audio: float = 0.0
) -> dict[str, float]:
    """Costo extrapolado de una llamada, desglosado para poder auditarlo."""
    razonamiento = (
        tokens_entrada / 1_000_000 * USD_POR_MILLON_ENTRADA
        + tokens_salida / 1_000_000 * USD_POR_MILLON_SALIDA
    )
    transcripcion = segundos_audio / 3600 * USD_POR_HORA_AUDIO
    return {
        "razonamiento_usd": round(razonamiento, 6),
        "transcripcion_usd": round(transcripcion, 6),
        "total_usd": round(razonamiento + transcripcion, 6),
    }
