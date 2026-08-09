"""Pruebas del detector de estado de la herida.

Vive en código y no en el modelo porque la extracción corre en segundo plano y
llega un turno tarde. Caso real: el paciente dijo "roja, hinchada y también le
sale líquido" —una SECRECIÓN, que escala sola— y el agente respondió "me dice
que la ve roja" y siguió, dejando el caso en amarillo con eritema leve.
"""

from __future__ import annotations

import json

import pytest

from src.clinico.escalamiento import Herida, Semaforo, evaluar, CuadroClinico
from src.clinico.herida import estado_referido
from src.conversacion.turno import Conversacion, EstadoLlamada, Foco


class TestPrioridadClinica:
    def test_la_secrecion_manda_sobre_el_enrojecimiento(self):
        """Quedarse con el primer adjetivo sería quedarse con lo menos grave."""
        assert estado_referido("roja, hinchada y también le sale líquido") == (
            "secrecion_purulenta"
        )

    @pytest.mark.parametrize(
        "texto",
        [
            "le sale un líquido amarillo",
            "está botando algo",
            "le sale pus",
            "está supurando",
            "mancha la gasa",
            "huele feo",
            "le sale materia",
        ],
    )
    def test_reconoce_la_secrecion_como_la_nombra_el_paciente(self, texto: str):
        assert estado_referido(texto) == "secrecion_purulenta"

    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("la veo roja", "eritema_leve"),
            ("está hinchada", "eritema_leve"),
            ("coloradita alrededor", "eritema_leve"),
            ("está seca y limpia", "normal"),
            ("no le veo nada raro", "normal"),
            ("no la he mirado, me da cosa", "desconocido"),
            ("tiene vendaje todavía", "desconocido"),
        ],
    )
    def test_clasifica_los_demas_estados(self, texto: str, esperado: str):
        assert estado_referido(texto) == esperado

    def test_no_inventa_estado_cuando_no_se_habla_de_la_herida(self):
        assert estado_referido("me duele la cabeza") is None
        assert estado_referido("¡Ojo!") is None


class ClienteFalso:
    def generar(self, prompt, **kwargs):  # noqa: ANN001, ANN003
        from src.modelo.cliente import Respuesta

        return Respuesta(json.dumps({}), 10, 5, 1.0, "falso")


class TestEnLaConversacion:
    def test_la_secrecion_escala_en_el_mismo_turno(self):
        conv = Conversacion(ClienteFalso(), EstadoLlamada(escenario="cholecystitis"))
        conv.abrir()
        conv.responder("no he tenido fiebre")
        conv.esperar_extraccion()
        r = conv.responder("roja, hinchada y también le sale líquido")
        assert conv.estado.cuadro.herida is Herida.SECRECION_PURULENTA
        assert r.decision.semaforo is Semaforo.ROJO
        assert any("purulenta" in m for m in r.decision.motivos)

    def test_el_eco_nombra_el_hallazgo_mas_grave(self):
        conv = Conversacion(ClienteFalso(), EstadoLlamada(escenario="cholecystitis"))
        conv.abrir()
        conv.responder("no he tenido fiebre")
        conv.esperar_extraccion()
        r = conv.responder("roja, hinchada y también le sale líquido")
        assert "sale de la herida" in r.texto
        assert "la ve roja" not in r.texto


class TestSaludosYAclaraciones:
    """Ni un saludo ni un "¿cómo dice?" son la respuesta clínica."""

    def test_el_saludo_recibe_un_saludo(self):
        conv = Conversacion(ClienteFalso(), EstadoLlamada(escenario="cholecystitis"))
        conv.abrir()
        r = conv.responder("¡Buenos días!")
        assert "Buenos días" in r.texto
        assert "no le entendí" not in r.texto
        assert r.foco is Foco.FIEBRE, "perdió el hilo de la pregunta"

    def test_la_aclaracion_repite_la_pregunta(self):
        conv = Conversacion(ClienteFalso(), EstadoLlamada(escenario="cholecystitis"))
        conv.abrir()
        conv.responder("sí he tenido fiebre")
        conv.esperar_extraccion()
        r = conv.responder("¿Qué es lo que se llama?")
        assert "repito" in r.texto.lower()
        assert "documentación que manejo" not in r.texto


class TestVocabulario:
    def test_no_usa_palabras_de_medico(self):
        """"Destemplado" es vocabulario clínico: el paciente no lo usa."""
        from src.conversacion import turno as T

        frases = [
            *(v for var in T.PREGUNTAS.values() for v in var),
            *(v for var in T.REPREGUNTAS.values() for v in var),
            *T.PEDIDOS_DE_DATO.values(),
        ]
        for frase in frases:
            assert "destemplado" not in frase.lower()
            assert "eritema" not in frase.lower()
            assert "purulent" not in frase.lower()
