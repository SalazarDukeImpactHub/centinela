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
# Marcadores de minimización que, sobre un cuadro con hallazgos, lo elevan a
# rojo. Calibrado contra los 160 casos: ver el comentario en evaluar().
MINIMIZACION_PARA_ESCALAR = 6

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
    # Marcadores de minimización acumulados en la llamada. No es un síntoma:
    # es una señal sobre CÓMO reporta el paciente, y por eso solo pondera
    # hallazgos ya detectados — nunca crea uno.
    marcadores_minimizacion: int = 0
    # El paciente NEGÓ la fiebre. Es un dato, no un vacío: sin esto el resumen
    # informaba «quedó sin preguntar: fiebre» a quien recibe la alerta, sobre un
    # paciente que había contestado «fiebre no he tenido, nada». Decirle al
    # equipo clínico que un tema quedó sin explorar cuando sí se exploró es peor
    # que no decir nada.
    fiebre_negada: bool = False

    @property
    def campos_faltantes(self) -> list[str]:
        """Qué falta preguntar para poder decidir con fundamento.

        Falta lo que NO SE SABE, no lo que no tiene número: una fiebre negada es
        una respuesta cerrada aunque no deje una cifra.
        """
        faltan = []
        if self.dolor_nrs is None:
            faltan.append("dolor")
        if self.fiebre_c is None and not self.fiebre_negada:
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
        #
        # SALVO que además haya un hallazgo en la herida. Fiebre referida más
        # herida inflamada después de una cirugía es sospecha de infección de
        # sitio operatorio, y que no haya termómetro no la vuelve más segura:
        # la vuelve desconocida, que es justo cuando se escala.
        #
        # CALIBRADO sobre los 160 casos por el pipeline conversacional completo:
        # recupera 2 casos rojo que se perdían en amarillo, y dispara en CERO
        # verdes y CERO amarillos. Sin costo de sobre-escalamiento.
        if cuadro.herida in (Herida.ERITEMA_LEVE, Herida.SECRECION_PURULENTA):
            motivos_rojo.append(
                "fiebre referida sin medir junto a hallazgo en la herida "
                "— sospecha de infección de sitio operatorio"
            )
        else:
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
        # Minimización sistemática sobre un cuadro que YA tiene hallazgos.
        #
        # MEDIDO sobre los 160 casos: los pacientes rojo minimizan mucho más
        # (mediana 6 marcadores por llamada) que los verde (mediana 2). El que
        # está peor es el que más resta importancia — quien tiene 9 de dolor y
        # dice "un poquito molesto, uno aguanta" no informa mal: aguanta.
        #
        # La señal SOLO pondera hallazgos existentes, nunca crea uno: sobre un
        # cuadro verde no hace nada. Calibrada en 6 marcadores, recupera los 2
        # casos rojo que ningún extractor podía sacar de lo que el paciente
        # dijo, al costo de 8 escalamientos de más sobre 148 casos no-rojo.
        #
        # La compensación es deliberada y es la que pide el reto: un falso
        # negativo en posoperatorio es riesgo clínico; un falso positivo cuesta
        # una llamada de verificación.
        if cuadro.marcadores_minimizacion >= MINIMIZACION_PARA_ESCALAR:
            return Decision(
                Semaforo.ROJO,
                [
                    *motivos_amarillo,
                    f"el paciente minimiza de forma sostenida "
                    f"({cuadro.marcadores_minimizacion} señales) sobre un cuadro "
                    f"con hallazgos — su autorreporte puede estar por debajo de "
                    f"lo real",
                ],
                cuadro,
            )
        return Decision(Semaforo.AMARILLO, motivos_amarillo, cuadro)

    return Decision(Semaforo.VERDE, ["sin hallazgos de alarma"], cuadro)
