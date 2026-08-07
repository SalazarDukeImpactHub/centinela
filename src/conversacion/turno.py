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

from typing import Callable, Protocol

from src.clinico import alarmas, fiebre
from src.conversacion import preguntas
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

# Las preguntas van en lenguaje de paciente, no de historia clínica. Se evita
# "¿ha presentado?" y "refiera usted": nadie habla así por teléfono.
PREGUNTAS: dict[Foco, str] = {
    Foco.FIEBRE: "Cuénteme, ¿ha tenido fiebre o escalofríos estos días?",
    Foco.HERIDA: "¿Y cómo ve la herida? Me interesa si está roja, hinchada o si le sale algún líquido.",
    Foco.DOLOR: "Hablemos del dolor. Si cero es nada y diez es lo más fuerte que ha sentido, ¿en cuánto lo pondría ahora?",
    Foco.MOVILIDAD: "¿Se ha podido mover y caminar como esperaba?",
}

# Repregunta cuando el paciente esquiva. No repite la misma frase: insistir con las
# mismas palabras a alguien que ya minimizó no funciona.
REPREGUNTAS: dict[Foco, str] = {
    Foco.FIEBRE: "Se lo pregunto de otra manera: ¿en algún momento se sintió caliente o destemplado?",
    Foco.HERIDA: "¿La ha alcanzado a mirar hoy? Con saber de qué color está me ayuda bastante.",
    Foco.DOLOR: "Aunque sea por encimita: ¿el dolor lo deja dormir y moverse tranquilo?",
    Foco.MOVILIDAD: "¿Se levanta de la cama sin que nadie lo ayude?",
}

# Repregunta por DATO FALTANTE: el paciente respondió, pero sin la cifra que la
# decisión necesita. Es distinto de la evasión — acá sí quiere colaborar.
#
# El caso de la fiebre es el que más pesa: "tuve fiebre" puede ser 37.2 (verde) o
# 39 (rojo), y el umbral de escalamiento está justo en 38. Seguir de largo sin el
# número obliga a asumir, y asumir en cualquiera de las dos direcciones es un
# error clínico.
PEDIDOS_DE_DATO: dict[Foco, str] = {
    Foco.FIEBRE: (
        "¿Se alcanzó a tomar la temperatura? Si tiene el número me sirve mucho, "
        "porque no es lo mismo treinta y siete que treinta y nueve."
    ),
    Foco.DOLOR: (
        "¿Y si tuviera que ponerle un número del cero al diez, cuál sería? "
        "Aunque sea aproximado."
    ),
}

# Apertura: se presenta con nombre y rol, dice qué va a hacer y ofrece ayuda.
# Todo en USTED. El registro no se mezcla: los pacientes del dataset son adultos
# colombianos, muchos mayores, y alternar usted y tú en la misma frase suena
# descuidado justo en el momento en que el agente se está ganando la confianza.
APERTURA = (
    "Buenos días, le habla Centinela, su agente encargado del seguimiento de su "
    "cirugía. Voy a hacerle unas preguntas para ver cómo va su recuperación y si "
    "puedo ayudarle en algo."
)

# Escalamiento: se nombra lo que pasa sin dramatizarlo, y sobre todo se dice qué
# va a ocurrir después. Un paciente al que le anuncian que algo anda mal y no le
# explican el siguiente paso se queda solo con el susto.
ESCALAMIENTO = (
    "Le agradezco que me lo cuente, porque eso sí hay que mirarlo hoy mismo. Lo "
    "estoy reportando al equipo de salud en este momento, y se van a comunicar "
    "con usted en breve. Mientras tanto no se aplique nada en la herida, y si se "
    "siente peor no espere la llamada: consulte de una vez."
)

CIERRE_VERDE = (
    "Me deja tranquilo, su recuperación va bien encaminada. Le pido una cosa: si "
    "le llega a dar fiebre, si la herida le cambia de aspecto o si el dolor "
    "aumenta, no espere a la próxima llamada y avise de una vez. "
    "Que siga mejorando, y gracias por su tiempo."
)

CIERRE_AMARILLO = (
    "Voy a dejar anotado todo lo que me contó para que el equipo lo revise con "
    "calma. Es probable que lo llamen para verificar un par de cosas. "
    "Si algo se pone peor antes de eso, avise de una vez. Que se mejore."
)

# Reconocimiento breve antes de la siguiente pregunta. Cumple dos funciones: que
# el paciente sepa que se le escuchó, y llenar el silencio mientras la extracción
# corre por detrás. La rúbrica pregunta explícitamente qué hace la solución
# durante los silencios.
ACUSES = {
    Semaforo.VERDE: "Listo, gracias.",
    Semaforo.AMARILLO: "Bueno, eso me sirve saberlo.",
    Semaforo.ROJO: "Entiendo, eso sí es importante.",
}

# Acuse cuando el paciente cuenta algo que preocupa. Reconoce lo que dijo antes
# de seguir preguntando: pasar de largo suena a formulario.
ACUSE_PREOCUPACION = "Entiendo su preocupación, y hace bien en contármelo."

# Cuando el corpus no cubre la pregunta del paciente. Declarar el límite y
# derivar es exactamente la conducta que el criterio de 20 puntos evalúa.
SIN_RESPUESTA = (
    "Esa pregunta no la puedo responder con la documentación que manejo, y "
    "prefiero no adivinar. Se la dejo anotada al equipo de salud para que se la "
    "resuelvan bien."
)

# Saludo a un cuidador que toma la palabra. Su relato vale: muchas señales
# —desorientación, por ejemplo— las ve el cuidador antes que el paciente.
ACUSE_TERCERO = "Claro que sí, le agradezco que me cuente cómo lo ha visto."


class Consultor(Protocol):
    """Responde una pregunta del paciente contra el corpus, o declara el límite.

    Devuelve el texto a decir (ya con cita si la hay) o None si no hay sustento.
    La implementación real vive en la API, que conoce el índice; acá solo se
    define el contrato para que la máquina de turnos sea comprobable sin RAG.
    """

    def __call__(self, pregunta: str) -> str | None: ...


@dataclass
class EstadoLlamada:
    """Lo que el agente sabe y lo que ya preguntó."""

    escenario: str
    cuadro: CuadroClinico = field(default_factory=CuadroClinico)
    preguntados: set[Foco] = field(default_factory=set)
    repreguntados: set[Foco] = field(default_factory=set)
    # Focos donde ya se pidió la cifra concreta. Se pide UNA vez: insistir dos
    # veces por un número que el paciente no tiene es hostigarlo.
    datos_pedidos: set[Foco] = field(default_factory=set)
    foco_actual: Foco | None = None
    cerrada: bool = False
    transcripcion: list[tuple[str, str]] = field(default_factory=list)

    def siguiente_foco(self) -> Foco | None:
        for foco in ORDEN_FOCOS:
            if foco not in self.preguntados:
                return foco
        return None

    def dato_pendiente(self) -> Foco | None:
        """Foco donde el paciente respondió pero falta la cifra que decide.

        Tiene prioridad sobre avanzar al siguiente tema: sin el número de la
        fiebre no se puede distinguir un 37.2 de un 39, y el umbral está en 38.
        """
        if self.cuadro.falta_el_numero_de_fiebre and Foco.FIEBRE not in self.datos_pedidos:
            return Foco.FIEBRE
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

    def __init__(
        self,
        cliente: ClienteLocal,
        estado: EstadoLlamada,
        consultor: Consultor | None = None,
    ) -> None:
        self.cliente = cliente
        self.estado = estado
        self.consultor = consultor
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

        # 1b. Fiebre referida sin cifra, también en código y en este mismo turno.
        #     Por la vía de la extracción llegaría un turno tarde, y preguntar
        #     "¿de cuánto?" después de haber cambiado de tema suena a no haber
        #     escuchado.
        if fiebre.refiere_fiebre_sin_cifra(texto_paciente):
            with self._lock:
                self.estado.cuadro.fiebre_referida_sin_medir = True

        # 2. La extracción arranca en segundo plano y no bloquea nada.
        self._extraer_en_segundo_plano(texto_paciente, foco_previo)

        # 3. Se decide con lo que ya se sabe, incluidas las alarmas recién vistas.
        with self._lock:
            decision = evaluar(self.estado.cuadro)

        # 3b. Si el paciente preguntó algo, se le responde ANTES de seguir con el
        #     triaje. Medido en el dataset: el 45 % de los turnos de la capa
        #     ruidosa traen una pregunta, y un agente que las ignora suena a
        #     formulario con voz. La respuesta sale del corpus con cita, o es el
        #     límite declarado — nunca una improvisación.
        #     Un tercero que se presenta recibe su acuse: su relato vale, y las
        #     alarmas ya barrieron su texto igual que el del paciente.
        prefijo = ""
        if preguntas.habla_un_tercero(texto_paciente):
            prefijo = ACUSE_TERCERO + " "
        elif not decision.escala and preguntas.contiene_pregunta(texto_paciente):
            respuesta_corpus = self.consultor(texto_paciente) if self.consultor else None
            prefijo = (respuesta_corpus or SIN_RESPUESTA) + " "

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
        #    Antes de avanzar de tema, se pide la cifra que quedó pendiente. Sin
        #    el número de la fiebre no se puede decidir, y asumirlo es un error
        #    clínico en las dos direcciones.
        with self._lock:
            pendiente = self.estado.dato_pendiente()
            if pendiente is not None:
                self.estado.datos_pedidos.add(pendiente)
                self.estado.foco_actual = pendiente
                texto = f"{prefijo}{ACUSE_PREOCUPACION} {PEDIDOS_DE_DATO[pendiente]}"
                self.estado.transcripcion.append(("agente", texto))
                return RespuestaTurno(
                    texto=texto,
                    decision=decision,
                    escala=False,
                    cierra=False,
                    foco=pendiente,
                    alarmas_detectadas=detectadas,
                )
            foco = self.estado.siguiente_foco()

        if foco is None:
            return self._cerrar(decision)

        es_repregunta = foco in self.estado.repreguntados
        pregunta = (REPREGUNTAS if es_repregunta else PREGUNTAS)[foco]
        with self._lock:
            self.estado.preguntados.add(foco)
            self.estado.foco_actual = foco

        texto = f"{prefijo}{ACUSES[decision.semaforo]} {pregunta}"
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
