"""Pruebas del detector determinista de síntomas de alarma.

Existe en código y no en el modelo por evidencia medida: llama3.2:3b copiaba la
lista de síntomas del prompt e inventaba `dolor_toracico` en turnos que hablaban
de fiebre o de la herida. Ver src/clinico/alarmas.py.

Las frases de prueba son las que un paciente colombiano usa de verdad, no
terminología médica.
"""

from __future__ import annotations

import pytest

from src.clinico.alarmas import detectar


class TestDeteccionPositiva:
    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("Doctora no puedo respirar bien, me falta el aire", "dificultad_respiratoria"),
            ("Me ahogo cuando camino un poquito", "dificultad_respiratoria"),
            ("Me duele el pecho desde anoche", "dolor_toracico"),
            ("Siento una opresión en el pecho", "dolor_toracico"),
            ("La herida no para de sangrar", "sangrado_activo"),
            ("Se me abrió la herida", "dehiscencia_herida"),
            ("Se reventaron los puntos", "dehiscencia_herida"),
            ("Anoche me desmayé en el baño", "sincope"),
            ("Perdí el conocimiento un momento", "sincope"),
            ("Devuelvo todo lo que como", "vomito_persistente"),
            ("No sé dónde estoy", "desorientacion"),
        ],
    )
    def test_detecta_sintoma(self, texto: str, esperado: str):
        assert esperado in detectar(texto)

    def test_detecta_varios_a_la_vez(self):
        r = detectar("Me duele el pecho y me ahogo")
        assert set(r) == {"dolor_toracico", "dificultad_respiratoria"}

    def test_funciona_sin_tildes(self):
        """El habla transcrita llega con y sin acentos."""
        assert "sincope" in detectar("anoche me desmaye en el bano")


class TestSinFalsosPositivos:
    """Lo que hacía mal el modelo: inventar alarmas donde no las hay."""

    @pytest.mark.parametrize(
        "texto",
        [
            "Sí señora, ayer me sentí afiebrada, creo que como 38",
            "Me duele harto la herida, casi no aguanto",
            "La he visto con un líquido amarillo saliendo",
            "No, doctora, todo bien, normal, no se preocupe",
            "Camino normal, no me cuesta nada",
            "Casi no me da hambre, como poquito",
            "Duermo mal, me despierto varias veces",
        ],
    )
    def test_no_inventa_alarmas(self, texto: str):
        assert detectar(texto) == []

    def test_dolor_abdominal_no_es_dolor_toracico(self):
        """El fallo exacto que cometía el modelo."""
        assert "dolor_toracico" not in detectar("Me duele mucho la barriga y la herida")
