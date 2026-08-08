"""Motor de escalamiento clínico. Determinista, en código.

Principio rector: el comportamiento clínico NO puede depender de que el modelo sea
inteligente. El modelo extrae qué dijo el paciente; la decisión de escalar la toma
este módulo, con umbrales que no se negocian con un prompt.

Los umbrales están validados contra los 160 casos etiquetados del kit:
recall 12/12 en casos rojo, cero falsos negativos (ver docs/baseline-triaje.md).

Calibración deliberada hacia el recall: la rúbrica declara la asimetría clínica de
forma explícita —el falso negativo es la falla catastrófica—. Un amarillo de más
cuesta una llamada de verificación; un rojo perdido cuesta un paciente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Semaforo(str, Enum):
    VERDE = "verde"
    AMARILLO = "amarillo"
    ROJO = "rojo"

    @property
    def escala(self) -> bool:
        return self is Semaforo.ROJO


class Herida(str, Enum):
    NORMAL = "normal"
    ERITEMA_LEVE = "eritema_leve"
    SECRECION_PURULENTA = "secrecion_purulenta"
    DESCONOCIDO = "desconocido"


class Movilidad(str, Enum):
    NORMAL = "normal"
    LIMITADA_ESPERADA = "limitada_esperada"
    INCAPACITANTE_NUEVA = "incapacitante_nueva"
    DESCONOCIDO = "desconocido"


# Umbrales validados. Cambiar cualquiera exige volver a correr el banco de 160 casos.
FIEBRE_ROJO = 38.0
FIEBRE_AMARILLO = 37.5
# Por debajo de esto la temperatura reportada es hipotermia o una medición mal
# tomada. Ninguna de las dos se ignora: ambas ameritan verificar.
TEMPERATURA_BAJA = 35.5
DOLOR_ROJO = 8
DOLOR_AMARILLO = 4


@dataclass
class CuadroClinico:
    """Lo que el agente logró averiguar conversando.

    Todo es opcional a propósito: al inicio de la llamada no se sabe nada, y el
    agente debe poder decidir con información parcial. `None` significa "todavía
    no lo pregunté o el paciente no lo respondió", que es distinto de un valor
    normal — y esa diferencia es la que dispara la repregunta.
    """

    dolor_nrs: int | None = None
    fiebre_c: float | None = None
    herida: Herida = Herida.DESCONOCIDO
    movilidad: Movilidad = Movilidad.DESCONOCIDO
    sintomas_alarma: list[str] = field(default_factory=list)
    # El paciente dijo tener fiebre o escalofríos pero no dio un número.
    # NO es lo mismo que no tener fiebre ni que tenerla alta: "me sentí
    # afiebrado" puede ser 37.2 (verde) o 39 (rojo). Asumir cualquiera de las dos
    # es un error clínico, así que se registra el dato como lo que es —una
    # sospecha sin medir— y se pide el número.
    fiebre_referida_sin_medir: bool = False

    @property
    def campos_faltantes(self) -> list[str]:
        """Qué falta preguntar para poder decidir con fundamento."""
        faltan = []
        if self.dolor_nrs is None:
            faltan.append("dolor")
        if self.fiebre_c is None:
            faltan.append("fiebre")
        if self.herida is Herida.DESCONOCIDO:
            faltan.append("herida")
        return faltan

    @property
    def falta_el_numero_de_fiebre(self) -> bool:
        """Refirió fiebre pero no la midió: hay que pedir la cifra."""
        return self.fiebre_referida_sin_medir and self.fiebre_c is None

    @property
    def completo(self) -> bool:
        return not self.campos_faltantes


@dataclass
class Decision:
    semaforo: Semaforo
    motivos: list[str]
    cuadro: CuadroClinico

    @property
    def escala(self) -> bool:
        return self.semaforo.escala

    @property
    def requiere_indagar(self) -> bool:
        """Verde sin datos suficientes no es verde: es ignorancia.

        Declarar verde sin haber preguntado por fiebre o herida es precisamente el
        falso negativo que la rúbrica castiga. Mientras falten campos, el agente
        debe seguir indagando en lugar de tranquilizar al paciente.
        """
        return self.semaforo is Semaforo.VERDE and not self.cuadro.completo


# Síntomas que escalan por sí solos, sin importar el resto del cuadro.
SINTOMAS_ROJOS = {
    "dificultad_respiratoria",
    "dolor_toracico",
    "sangrado_activo",
    "vomito_persistente",
    "desorientacion",
    "sincope",
    "dehiscencia_herida",
}


def evaluar(cuadro: CuadroClinico) -> Decision:
    """Clasifica criticidad y explica por qué. Los motivos son auditables."""
    motivos_rojo: list[str] = []
    motivos_amarillo: list[str] = []

    for sintoma in cuadro.sintomas_alarma:
        if sintoma in SINTOMAS_ROJOS:
            motivos_rojo.append(f"síntoma de alarma: {sintoma}")

    if cuadro.fiebre_c is not None:
        if cuadro.fiebre_c >= FIEBRE_ROJO:
            motivos_rojo.append(f"fiebre {cuadro.fiebre_c} °C ≥ {FIEBRE_ROJO}")
        elif cuadro.fiebre_c >= FIEBRE_AMARILLO:
            motivos_amarillo.append(f"febrícula {cuadro.fiebre_c} °C")
        elif cuadro.fiebre_c < TEMPERATURA_BAJA:
            motivos_amarillo.append(
                f"temperatura {cuadro.fiebre_c} °C inusualmente baja — verificar medición"
            )
    elif cuadro.fiebre_referida_sin_medir:
        # Fiebre referida sin termómetro. No se asume alta —sería alarmismo— ni
        # se descarta —sería el falso negativo que la rúbrica castiga—. Queda en
        # amarillo hasta obtener la cifra: amarillo cuesta una llamada de
        # verificación, y eso es exactamente lo que hace falta acá.
        motivos_amarillo.append("fiebre referida sin medir")

    if cuadro.herida is Herida.SECRECION_PURULENTA:
        motivos_rojo.append("secreción purulenta en la herida")
    elif cuadro.herida is Herida.ERITEMA_LEVE:
        motivos_amarillo.append("eritema en la herida")

    if cuadro.dolor_nrs is not None:
        if cuadro.dolor_nrs >= DOLOR_ROJO:
            motivos_rojo.append(f"dolor {cuadro.dolor_nrs}/10 ≥ {DOLOR_ROJO}")
        elif cuadro.dolor_nrs >= DOLOR_AMARILLO:
            motivos_amarillo.append(f"dolor {cuadro.dolor_nrs}/10")

    if cuadro.movilidad is Movilidad.INCAPACITANTE_NUEVA:
        motivos_amarillo.append("movilidad incapacitante nueva")

    if motivos_rojo:
        return Decision(Semaforo.ROJO, motivos_rojo, cuadro)
    if motivos_amarillo:
        return Decision(Semaforo.AMARILLO, motivos_amarillo, cuadro)
    return Decision(Semaforo.VERDE, ["sin hallazgos de alarma"], cuadro)
