"""Pruebas de la preparación de texto para voz y de la cadencia.

Las cifras clínicas son el contenido más importante de la llamada y son justo lo
que un sintetizador arruina: "38.5 °C" leído como dígitos sueltos y una letra
pierde al paciente en el peor momento.
"""

from __future__ import annotations

import pytest

from src.voz.tts import PAUSA_ORACION_MS, PAUSA_PREGUNTA_MS, VELOCIDAD, preparar_para_voz


class TestCifrasHabladas:
    @pytest.mark.parametrize(
        "escrito,dicho",
        [
            ("38.5 °C", "treinta y ocho punto cinco grados"),
            ("37,2 °C", "treinta y siete punto dos grados"),
            ("39.0 °C", "treinta y nueve grados"),
            ("40.1 °C", "cuarenta punto uno grados"),
            ("36,8 °C", "treinta y seis punto ocho grados"),
        ],
    )
    def test_la_temperatura_se_dice_en_palabras(self, escrito: str, dicho: str):
        assert preparar_para_voz(escrito) == dicho

    def test_la_escala_de_dolor_se_dice_completa(self):
        assert "de diez" in preparar_para_voz("dolor 8/10")

    def test_el_texto_sin_cifras_no_se_toca(self):
        original = "¿Cómo ve la herida? Me interesa si está roja o hinchada."
        assert preparar_para_voz(original) == original


class TestCadencia:
    def test_la_velocidad_no_arrastra_la_voz(self):
        """Probado en llamada real: 1.12 se percibió arrastrado.

        La naturalidad viene de las pausas entre oraciones, no de estirar cada
        palabra — estirarlas suena a grabación en cámara lenta.
        """
        assert 0.95 <= VELOCIDAD <= 1.05

    def test_la_pregunta_deja_mas_aire_que_la_afirmacion(self):
        """El silencio tras una pregunta es parte de la invitación a responder."""
        assert PAUSA_PREGUNTA_MS > PAUSA_ORACION_MS
