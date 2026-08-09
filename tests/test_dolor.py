"""Pruebas de la detección de dolor y movilidad en código.

Dos fallas medidas en llamada real las obligaron:
  - "Siete." recibió "no le capté el número del dolor" — el detector solo
    miraba dígitos, y por teléfono la gente dice las cifras en palabras.
  - "No me puedo mover" se interpretó como PREGUNTA (por el "puedo"), se buscó
    en el corpus, y la movilidad quedó "desconocido".
"""

from __future__ import annotations

import json

import pytest

from src.clinico.dolor import estado_movilidad, nivel_dolor
from src.clinico.escalamiento import Movilidad
from src.conversacion.preguntas import contiene_pregunta
from src.conversacion.turno import Conversacion, EstadoLlamada


class TestNivelDeDolor:
    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("Siete.", 7),
            ("siete", 7),
            ("Número 7", 7),
            ("un 3", 3),
            ("como diez", 10),
            ("cero", 0),
        ],
    )
    def test_numeros_en_digitos_y_en_palabras(self, texto: str, esperado: int):
        assert nivel_dolor(texto) == esperado

    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("me duele harto", 8),
            ("insoportable", 10),
            ("aguantable", 3),
            ("más o menos", 5),
            ("no me duele nada", 0),
        ],
    )
    def test_escala_verbal(self, texto: str, esperado: int):
        assert nivel_dolor(texto) == esperado

    def test_no_inventa_nivel(self):
        assert nivel_dolor("¡Ojo!") is None
        assert nivel_dolor("la herida está roja") is None


class TestMovilidad:
    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("No me puedo mover.", "incapacitante_nueva"),
            ("casi no puedo caminar", "incapacitante_nueva"),
            ("camino con dificultad", "limitada_esperada"),
            ("me cuesta levantarme", "limitada_esperada"),
            ("camino bien", "normal"),
            ("me muevo sin problema", "normal"),
        ],
    )
    def test_clasifica_el_estado(self, texto: str, esperado: str):
        assert estado_movilidad(texto) == esperado

    def test_la_incapacidad_manda(self):
        """"No me puedo mover" y "puedo caminar" no se promedian."""
        assert estado_movilidad("no me puedo mover casi nada") == "incapacitante_nueva"


class TestNoEsPregunta:
    """Un verbo modal negado es una queja, no una consulta al corpus."""

    @pytest.mark.parametrize(
        "texto",
        ["No me puedo mover.", "ya no puedo caminar", "casi no puedo levantarme"],
    )
    def test_la_negacion_modal_no_es_pregunta(self, texto: str):
        assert not contiene_pregunta(texto)

    def test_la_pregunta_real_sigue_siendo_pregunta(self):
        assert contiene_pregunta("¿Puedo tomar agua?")
        assert contiene_pregunta("puedo caminar ya")


class ClienteFalso:
    def generar(self, prompt, **kwargs):  # noqa: ANN001, ANN003
        from src.modelo.cliente import Respuesta

        return Respuesta(json.dumps({}), 10, 5, 1.0, "falso")


class TestElEcoNoMiente:
    """El agente decía "Un 7 de dolor, anotado" y el resumen cerraba con
    "sin dato": el eco afirmaba haber guardado algo que no guardó."""

    def test_el_dolor_dicho_queda_registrado(self):
        conv = Conversacion(ClienteFalso(), EstadoLlamada(escenario="cholecystitis"))
        conv.abrir()
        for respuesta in ["no he tenido fiebre", "la veo bien", "Siete."]:
            conv.responder(respuesta)
            conv.esperar_extraccion()
        assert conv.estado.cuadro.dolor_nrs == 7

    def test_lo_que_el_eco_dice_es_lo_que_se_guardo(self):
        conv = Conversacion(ClienteFalso(), EstadoLlamada(escenario="cholecystitis"))
        conv.abrir()
        conv.responder("no he tenido fiebre")
        conv.esperar_extraccion()
        conv.responder("la veo bien")
        conv.esperar_extraccion()
        r = conv.responder("Siete.")
        assert "Un 7 de dolor" in r.texto
        assert conv.estado.cuadro.dolor_nrs == 7

    def test_la_movilidad_dicha_queda_registrada(self):
        conv = Conversacion(ClienteFalso(), EstadoLlamada(escenario="cholecystitis"))
        conv.abrir()
        for respuesta in ["no he tenido fiebre", "la veo bien", "un 2"]:
            conv.responder(respuesta)
            conv.esperar_extraccion()
        conv.responder("No me puedo mover.")
        conv.esperar_extraccion()
        assert conv.estado.cuadro.movilidad is Movilidad.INCAPACITANTE_NUEVA


class TestNumerosQueNoSonDolor:
    """MEDIDO sobre los 160 casos: "hace 7 días que me operaron" se registraba
    como dolor 7/10, a un paso del umbral de escalamiento."""

    @pytest.mark.parametrize(
        "texto",
        [
            "hace 7 días que me operaron",
            "me tomo 3 pastillas al día",
            "camino 5 cuadras",
            "como a las 8 de la mañana",
            "llevo 2 semanas así",
        ],
    )
    def test_los_numeros_con_unidad_ajena_se_ignoran(self, texto: str):
        assert nivel_dolor(texto, admitir_numero=True) is None

    @pytest.mark.parametrize("texto", ["un 7 de dolor", "en un 8", "como 4"])
    def test_el_numero_del_dolor_si_se_toma(self, texto: str):
        assert nivel_dolor(texto, admitir_numero=True) is not None

    def test_fuera_del_contexto_de_dolor_no_se_toman_numeros(self):
        """Un "37" suelto es una temperatura, no un dolor de 37."""
        assert nivel_dolor("me marcó 37", admitir_numero=False) is None
