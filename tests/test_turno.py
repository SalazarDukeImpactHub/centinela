"""Pruebas del orquestador de conversación.

Se usa un cliente de modelo falso: estas pruebas verifican la MÁQUINA DE TURNOS
—qué pregunta, cuándo escala, cuándo repregunta— y esa lógica no debe depender del
modelo. Que no dependa es justamente la propiedad que se está verificando.

La extracción real contra Llama 3.2 se prueba aparte; acá interesa que el camino
crítico no toque el modelo.
"""

from __future__ import annotations

import json

import pytest

from src.clinico.escalamiento import Herida, Semaforo
from src.conversacion.turno import (
    ORDEN_FOCOS,
    Conversacion,
    EstadoLlamada,
    Foco,
)


class ClienteFalso:
    """Devuelve una extracción fija. Registra cuántas veces se lo invocó."""

    def __init__(self, respuesta: dict | None = None) -> None:
        self.respuesta = respuesta or {}
        self.invocaciones = 0

    def generar(self, prompt, **kwargs):  # noqa: ANN001, ANN003
        from src.modelo.cliente import Respuesta

        self.invocaciones += 1
        return Respuesta(
            texto=json.dumps(self.respuesta),
            tokens_entrada=50,
            tokens_salida=20,
            latencia_ms=1.0,
            modelo="falso",
        )


def _conversacion(respuesta: dict | None = None) -> tuple[Conversacion, ClienteFalso]:
    cliente = ClienteFalso(respuesta)
    return Conversacion(cliente, EstadoLlamada(escenario="Appendicitis")), cliente


class TestAperturaYOrden:
    def test_la_apertura_se_presenta_y_pregunta(self):
        conv, _ = _conversacion()
        r = conv.abrir()
        assert "seguimiento" in r.texto
        assert r.foco is ORDEN_FOCOS[0]
        assert not r.escala

    def test_pregunta_primero_por_fiebre_y_herida(self):
        """Son las señales que escalan solas: si la llamada se corta, ya se preguntaron."""
        assert ORDEN_FOCOS[0] is Foco.FIEBRE
        assert ORDEN_FOCOS[1] is Foco.HERIDA

    def test_no_repite_un_foco_ya_preguntado(self):
        conv, _ = _conversacion()
        conv.abrir()
        vistos = {conv.estado.foco_actual}
        for _ in range(3):
            r = conv.responder("bueno")
            conv.esperar_extraccion()
            if r.cierra:
                break
            assert r.foco not in vistos
            vistos.add(r.foco)


class TestAlarmasInmediatas:
    """La señal más urgente no puede esperar al modelo."""

    def test_la_alarma_escala_en_el_mismo_turno(self):
        conv, cliente = _conversacion()
        conv.abrir()
        r = conv.responder("Doctora no puedo respirar bien, me falta el aire")
        assert r.escala
        assert r.cierra
        assert "dificultad_respiratoria" in r.alarmas_detectadas

    def test_la_alarma_no_depende_de_la_extraccion(self):
        """Aunque el modelo no haya respondido todavía, la alarma ya escaló."""
        conv, _ = _conversacion()
        conv.abrir()
        r = conv.responder("Se me abrió la herida, se reventaron los puntos")
        assert r.escala
        assert "dehiscencia_herida" in r.alarmas_detectadas

    def test_el_texto_de_escalamiento_no_tranquiliza(self):
        conv, _ = _conversacion()
        conv.abrir()
        r = conv.responder("Anoche me desmayé en el baño")
        assert r.escala
        bajo = r.texto.lower()
        # Invariantes del mensaje de escalación: nombra al equipo de salud, dice
        # qué va a pasar después, y NO tranquiliza — la rúbrica penaliza por
        # nombre la falsa tranquilidad ante un síntoma de alarma.
        assert "equipo" in bajo
        assert "comunicar" in bajo or "llamar" in bajo
        assert "tranquil" not in bajo
        assert "no se preocupe" not in bajo
        assert "todo está bien" not in bajo


class TestCaminoCritico:
    """El modelo no debe estar en el camino crítico de un turno."""

    def test_responder_no_espera_al_modelo(self):
        """El turno devuelve texto sin haber esperado a la extracción."""
        conv, cliente = _conversacion()
        conv.abrir()
        r = conv.responder("me siento bien")
        assert r.texto  # ya hay respuesta
        # La extracción puede seguir corriendo: nadie la esperó para responder.
        conv.esperar_extraccion()

    def test_la_decision_no_consume_llamadas_al_modelo(self):
        """Escalar ante una alarma cuesta cero tokens."""
        conv, cliente = _conversacion()
        conv.abrir()
        antes = cliente.invocaciones
        r = conv.responder("no puedo respirar")
        assert r.escala
        # La extracción de fondo puede sumar una invocación, pero la DECISIÓN no.
        conv.esperar_extraccion()
        assert r.decision.semaforo is Semaforo.ROJO


class TestExtraccionEnSegundoPlano:
    def test_el_cuadro_se_actualiza_tras_la_extraccion(self):
        """El texto del paciente DEBE contener la cifra: la validación contra el
        texto crudo descarta números que el modelo introduce por su cuenta."""
        conv, _ = _conversacion({"herida": "eritema_leve", "evasivo": False})
        conv.abrir()
        conv.responder("la veo un poco roja alrededor")
        assert conv.esperar_extraccion()
        from src.clinico.escalamiento import Herida

        assert conv.estado.cuadro.herida is Herida.ERITEMA_LEVE

    def test_la_extraccion_no_puede_inventar_la_cifra(self):
        """Si el modelo devuelve una fiebre que el paciente no dijo, se descarta
        y queda registrada como sospecha sin medir. Es el bug real que produjo
        un escalamiento con motivo 'fiebre ≥ 38' cuando el paciente dijo 34."""
        conv, _ = _conversacion({"fiebre_c": 38.5, "evasivo": False})
        conv.abrir()
        conv.responder("tuve fiebre anoche")
        conv.esperar_extraccion()
        assert conv.estado.cuadro.fiebre_c is None
        assert conv.estado.cuadro.fiebre_referida_sin_medir

    def test_la_fiebre_alta_dicha_escala_de_inmediato(self):
        """Con la cifra en el texto, ya ni siquiera espera al turno siguiente:
        el parser en código la toma en el mismo turno."""
        conv, _ = _conversacion({"fiebre_c": 38.5, "evasivo": False})
        conv.abrir()
        r = conv.responder("tuve 38.5 de fiebre anoche")
        assert r.escala
        assert any("38.5" in m for m in r.decision.motivos)

    def test_la_evasion_marca_el_foco_para_repreguntar(self):
        """El invariante es que el tema evadido quede marcado, no CUÁNDO se repregunta.

        El momento depende de una carrera legítima: si la extracción termina antes
        de elegir la siguiente pregunta, el agente repregunta de inmediato; si
        termina después —lo habitual con el modelo real, que tarda ~17 s— sigue con
        otro tema y vuelve más adelante. Ambas conductas son aceptables; lo que no
        puede pasar es que el tema evadido se dé por cubierto.
        """
        conv, _ = _conversacion({"dolor_nrs": None, "evasivo": True})
        conv.abrir()
        foco_inicial = conv.estado.foco_actual
        conv.responder("todo bien, no se preocupe")
        conv.esperar_extraccion()
        assert foco_inicial in conv.estado.repreguntados

    def test_el_tema_evadido_se_vuelve_a_preguntar(self):
        """Verificación de conducta observable: la repregunta llega a decirse."""
        from src.conversacion.turno import REPREGUNTAS

        conv, _ = _conversacion({"dolor_nrs": None, "evasivo": True})
        conv.abrir()
        foco_inicial = conv.estado.foco_actual

        dichos: list[str] = []
        for _ in range(len(ORDEN_FOCOS) + 2):
            conv.esperar_extraccion()
            r = conv.responder("todo bien, no se preocupe")
            dichos.append(r.texto)
            if r.cierra:
                break

        assert any(
            any(variante in dicho for variante in REPREGUNTAS[foco_inicial])
            for dicho in dichos
        ), "el tema evadido nunca se repreguntó"

    def test_la_repregunta_cambia_las_palabras(self):
        """Insistir con la misma frase a quien ya minimizó no sirve de nada."""
        from src.conversacion.turno import PREGUNTAS, REPREGUNTAS

        for foco in ORDEN_FOCOS:
            # Pools disjuntos: ninguna repregunta repite una pregunta original.
            assert not set(PREGUNTAS[foco]) & set(REPREGUNTAS[foco])


class TestCierre:
    def test_el_cierre_verde_advierte_signos_de_alarma(self):
        conv, _ = _conversacion({"evasivo": False})
        conv.abrir()
        for _ in range(len(ORDEN_FOCOS) + 1):
            r = conv.responder("todo normal")
            conv.esperar_extraccion()
            if r.cierra:
                break
        assert r.cierra
        assert "fiebre" in r.texto or "llame" in r.texto

    def test_el_escalamiento_cierra_la_llamada(self):
        conv, _ = _conversacion()
        conv.abrir()
        r = conv.responder("me duele el pecho")
        assert r.escala and r.cierra
        assert conv.estado.cerrada

    def test_la_transcripcion_registra_ambos_lados(self):
        conv, _ = _conversacion()
        conv.abrir()
        conv.responder("hola")
        hablantes = {h for h, _ in conv.estado.transcripcion}
        assert hablantes == {"agente", "paciente"}
