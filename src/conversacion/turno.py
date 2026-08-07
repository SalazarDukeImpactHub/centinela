"""Orquestación de un turno de conversación.

EL PROBLEMA DE LATENCIA, MEDIDO
    transcripción (Whisper en Groq)      ~1.500 ms
    extracción estructurada (3B local)  ~17.000 ms
    primer audio (Piper, ya caliente)      ~400 ms

Diecinueve segundos de silencio entre que el paciente termina de hablar y escucha
una respuesta. Para una conversación de voz eso no es lento: es un sistema roto.
La compuerta G4 exige conversación en tiempo real y la calidad de la voz vale 15
puntos.

LA SOLUCIÓN: SACAR EL MODELO DEL CAMINO CRÍTICO
El agente conduce la conversación, así que sabe qué le falta preguntar sin
consultar a nadie. La secuencia de preguntas la decide el motor de escalamiento
—que es código— a partir de los campos que aún no tiene.

Entonces el turno queda así:
    1. Transcribir.                                        ~1.500 ms
    2. Detectar síntomas de alarma sobre el texto crudo.        ~0 ms  (regex)
    3. Elegir la siguiente frase de una plantilla.              ~0 ms
    4. Empezar a hablar.                                     ~400 ms
    5. Extraer con el modelo, EN SEGUNDO PLANO, mientras el paciente escucha
       y responde.

Latencia percibida: ~2 segundos. El modelo sigue trabajando, pero fuera del
silencio que el paciente percibe.

Esto es posible porque las dos señales que no pueden esperar —los síntomas de
alarma y la decisión de escalar— ya viven en código y no en el modelo. La misma
decisión arquitectónica que protegía contra la alucinación resuelve ahora la
latencia.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum

from src.clinico import alarmas
from src.clinico.escalamiento import CuadroClinico, Decision, Semaforo, evaluar
from src.clinico.extraccion import extraer
from src.modelo.cliente import ClienteLocal


class Foco(str, Enum):
    """Temas que el agente debe cubrir, en orden de prioridad clínica."""

    DOLOR = "dolor"
    FIEBRE = "fiebre"
    HERIDA = "herida"
    MOVILIDAD = "movilidad"


# Orden deliberado: fiebre y herida son las señales que más pesan en el
# escalamiento (fiebre ≥38 y secreción purulenta escalan solas), así que se
# preguntan temprano. Si la llamada se corta, lo crítico ya está preguntado.
ORDEN_FOCOS = [Foco.FIEBRE, Foco.HERIDA, Foco.DOLOR, Foco.MOVILIDAD]

PREGUNTAS: dict[Foco, str] = {
    Foco.FIEBRE: "¿Ha tenido fiebre o escalofríos desde la cirugía?",
    Foco.HERIDA: "¿Cómo ve la herida? ¿Está roja, hinchada, o le sale algún líquido?",
    Foco.DOLOR: "En una escala del cero al diez, ¿qué tanto le duele en este momento?",
    Foco.MOVILIDAD: "¿Puede moverse y caminar como esperaba después de la operación?",
}

# Repregunta cuando el paciente esquiva. No repite la misma frase: insistir con las
# mismas palabras a alguien que ya minimizó no funciona.
REPREGUNTAS: dict[Foco, str] = {
    Foco.FIEBRE: "Le pregunto de otra manera: ¿se ha sentido caliente o destemplado?",
    Foco.HERIDA: "¿La ha podido mirar hoy? Me sirve saber de qué color está.",
    Foco.DOLOR: "Aunque sea aproximado: ¿el dolor le deja dormir y moverse?",
    Foco.MOVILIDAD: "¿Se levanta de la cama sin ayuda?",
}

APERTURA = (
    "Buenos días, le habla el sistema de seguimiento de su cirugía. "
    "Voy a hacerle unas preguntas para ver cómo va su recuperación."
)

ESCALAMIENTO = (
    "Por lo que me cuenta, esto necesita que lo revise el equipo médico. "
    "Voy a reportarlo ahora mismo y alguien se va a comunicar con usted."
)

CIERRE_VERDE = (
    "Su recuperación va como esperamos. Si aparece fiebre, si la herida cambia "
    "de aspecto o si el dolor aumenta, llame de inmediato. Que se mejore."
)

CIERRE_AMARILLO = (
    "Voy a dejar registrado lo que me contó para que el equipo lo revise. "
    "Si algo empeora antes de que la contacten, llame de inmediato."
)

# Reconocimiento breve antes de la siguiente pregunta. Evita que el paciente
# sienta que habló al vacío mientras la extracción corre por detrás.
ACUSES = {
    Semaforo.VERDE: "Entiendo.",
    Semaforo.AMARILLO: "Gracias por contarme, lo anoto.",
    Semaforo.ROJO: "Entiendo, eso es importante.",
}


@dataclass
class EstadoLlamada:
    """Lo que el agente sabe y lo que ya preguntó."""

    escenario: str
    cuadro: CuadroClinico = field(default_factory=CuadroClinico)
    preguntados: set[Foco] = field(default_factory=set)
    repreguntados: set[Foco] = field(default_factory=set)
    foco_actual: Foco | None = None
    cerrada: bool = False
    transcripcion: list[tuple[str, str]] = field(default_factory=list)

    def siguiente_foco(self) -> Foco | None:
        for foco in ORDEN_FOCOS:
            if foco not in self.preguntados:
                return foco
        return None


@dataclass
class RespuestaTurno:
    """Lo que el agente dice y por qué. `texto` va directo al sintetizador."""

    texto: str
    decision: Decision
    escala: bool
    cierra: bool
    foco: Foco | None = None
    alarmas_detectadas: list[str] = field(default_factory=list)


class Conversacion:
    """Máquina de turnos. El modelo nunca está en el camino crítico."""

    def __init__(self, cliente: ClienteLocal, estado: EstadoLlamada) -> None:
        self.cliente = cliente
        self.estado = estado
        self._extraccion: threading.Thread | None = None
        self._lock = threading.Lock()

    def abrir(self) -> RespuestaTurno:
        """Primer turno: saludo más la primera pregunta, sin tocar el modelo."""
        foco = self.estado.siguiente_foco()
        self.estado.foco_actual = foco
        if foco:
            self.estado.preguntados.add(foco)
        texto = f"{APERTURA} {PREGUNTAS[foco]}" if foco else APERTURA
        self.estado.transcripcion.append(("agente", texto))
        return RespuestaTurno(
            texto=texto,
            decision=evaluar(self.estado.cuadro),
            escala=False,
            cierra=False,
            foco=foco,
        )

    def _extraer_en_segundo_plano(self, texto: str, foco: Foco | None) -> None:
        """Actualiza el cuadro clínico mientras el paciente escucha la respuesta."""

        def tarea() -> None:
            resultado = extraer(
                self.cliente,
                texto,
                previo=self.estado.cuadro,
                pregunta_agente=PREGUNTAS.get(foco) if foco else None,
                foco=foco.value if foco else None,
            )
            with self._lock:
                self.estado.cuadro = resultado.cuadro
                if resultado.evasivo and foco and foco not in self.estado.repreguntados:
                    # Evasión detectada: el foco vuelve a la cola para repreguntar.
                    self.estado.preguntados.discard(foco)
                    self.estado.repreguntados.add(foco)

        hilo = threading.Thread(target=tarea, daemon=True)
        hilo.start()
        self._extraccion = hilo

    def esperar_extraccion(self, timeout: float = 60.0) -> bool:
        """Bloquea hasta que termine la extracción pendiente.

        Se usa antes de cerrar la llamada y en las pruebas deterministas, nunca
        dentro del camino crítico de un turno.
        """
        if self._extraccion is None:
            return True
        self._extraccion.join(timeout)
        return not self._extraccion.is_alive()

    def responder(self, texto_paciente: str) -> RespuestaTurno:
        """Procesa un turno del paciente y devuelve qué decir. Sin esperar al modelo."""
        self.estado.transcripcion.append(("paciente", texto_paciente))
        foco_previo = self.estado.foco_actual

        # 1. Alarmas sobre el texto crudo. Determinista, microsegundos, no espera
        #    a la extracción: es la señal que no puede llegar tarde.
        detectadas = alarmas.detectar(texto_paciente)
        if detectadas:
            with self._lock:
                nuevas = [
                    a for a in detectadas if a not in self.estado.cuadro.sintomas_alarma
                ]
                self.estado.cuadro.sintomas_alarma.extend(nuevas)

        # 2. La extracción arranca en segundo plano y no bloquea nada.
        self._extraer_en_segundo_plano(texto_paciente, foco_previo)

        # 3. Se decide con lo que ya se sabe, incluidas las alarmas recién vistas.
        with self._lock:
            decision = evaluar(self.estado.cuadro)

        if decision.escala:
            self.estado.cerrada = True
            self.estado.transcripcion.append(("agente", ESCALAMIENTO))
            return RespuestaTurno(
                texto=ESCALAMIENTO,
                decision=decision,
                escala=True,
                cierra=True,
                alarmas_detectadas=detectadas,
            )

        # 4. Siguiente pregunta desde plantilla: cero costo de modelo.
        with self._lock:
            foco = self.estado.siguiente_foco()

        if foco is None:
            return self._cerrar(decision)

        es_repregunta = foco in self.estado.repreguntados
        pregunta = (REPREGUNTAS if es_repregunta else PREGUNTAS)[foco]
        with self._lock:
            self.estado.preguntados.add(foco)
            self.estado.foco_actual = foco

        texto = f"{ACUSES[decision.semaforo]} {pregunta}"
        self.estado.transcripcion.append(("agente", texto))
        return RespuestaTurno(
            texto=texto,
            decision=decision,
            escala=False,
            cierra=False,
            foco=foco,
            alarmas_detectadas=detectadas,
        )

    def _cerrar(self, decision: Decision) -> RespuestaTurno:
        """Cierra la llamada. Antes espera la extracción: el resumen debe ser fiel."""
        self.esperar_extraccion()
        with self._lock:
            decision = evaluar(self.estado.cuadro)
            self.estado.cerrada = True

        if decision.escala:
            texto = ESCALAMIENTO
        elif decision.semaforo is Semaforo.AMARILLO:
            texto = CIERRE_AMARILLO
        else:
            texto = CIERRE_VERDE

        self.estado.transcripcion.append(("agente", texto))
        return RespuestaTurno(
            texto=texto, decision=decision, escala=decision.escala, cierra=True
        )
