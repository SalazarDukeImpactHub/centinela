"""Pruebas del manejo de fiebre referida sin cifra.

El umbral de escalamiento está en 38.0 °C y "tuve fiebre" abarca desde 37.2
—verde— hasta 39 —rojo—. Asumir alta es alarmismo; asumir normal es el falso
negativo que la rúbrica castiga con más dureza. La única conducta correcta es
pedir el número, y pedirlo en el mismo turno.
"""

from __future__ import annotations

import json

import pytest

from src.clinico.escalamiento import CuadroClinico, Semaforo, evaluar
from src.clinico.fiebre import menciona_fiebre, refiere_fiebre_sin_cifra, tiene_cifra
from src.conversacion.turno import (
    PEDIDOS_DE_DATO,
    Conversacion,
    EstadoLlamada,
    Foco,
)


class TestDeteccionEnCodigo:
    @pytest.mark.parametrize(
        "texto",
        [
            "Sí, tuve fiebre anoche",
            "me sentí afiebrada",
            "tenía el cuerpo caliente",
            "me dio como calentura",
            "anoche tuve escalofríos",
            "me sentí destemplado",
            "sentí un frío raro anoche",
            "estaba tiritando",
        ],
    )
    def test_reconoce_como_habla_el_paciente(self, texto: str):
        assert menciona_fiebre(texto)
        assert refiere_fiebre_sin_cifra(texto), "sin número, hay que pedirlo"

    @pytest.mark.parametrize(
        "texto",
        [
            "tuve 38 de fiebre",
            "me dio 38.5",
            "tenía treinta y ocho",
            "llegué a treinta y nueve anoche",
            "me marcó 37,8 el termómetro",
        ],
    )
    def test_no_pide_cifra_si_el_paciente_ya_la_dio(self, texto: str):
        assert menciona_fiebre(texto) or tiene_cifra(texto)
        assert not refiere_fiebre_sin_cifra(texto)

    @pytest.mark.parametrize(
        "texto",
        [
            "me duele la herida",
            "no he podido dormir bien",
            "camino sin problema",
            "todo bien, doctora",
        ],
    )
    def test_no_confunde_otros_sintomas_con_fiebre(self, texto: str):
        assert not menciona_fiebre(texto)


class TestEscalamiento:
    def test_fiebre_referida_sin_medir_no_es_verde(self):
        """Descartarla sería el falso negativo; asumirla alta, alarmismo."""
        d = evaluar(CuadroClinico(fiebre_referida_sin_medir=True))
        assert d.semaforo is Semaforo.AMARILLO
        assert any("sin medir" in m for m in d.motivos)

    def test_la_cifra_real_manda_sobre_la_sospecha(self):
        d = evaluar(CuadroClinico(fiebre_c=36.8, fiebre_referida_sin_medir=True))
        assert d.semaforo is Semaforo.VERDE

    def test_la_cifra_alta_escala_a_rojo(self):
        d = evaluar(CuadroClinico(fiebre_c=38.5, fiebre_referida_sin_medir=True))
        assert d.semaforo is Semaforo.ROJO

    def test_falta_el_numero_solo_mientras_no_haya_cifra(self):
        assert CuadroClinico(fiebre_referida_sin_medir=True).falta_el_numero_de_fiebre
        assert not CuadroClinico(
            fiebre_c=38.0, fiebre_referida_sin_medir=True
        ).falta_el_numero_de_fiebre


class ClienteFalso:
    def __init__(self, respuesta: dict | None = None) -> None:
        self.respuesta = respuesta or {}

    def generar(self, prompt, **kwargs):  # noqa: ANN001, ANN003
        from src.modelo.cliente import Respuesta

        return Respuesta(json.dumps(self.respuesta), 10, 5, 1.0, "falso")


class TestConversacion:
    def test_pide_el_numero_en_el_mismo_turno(self):
        """El caso reportado: decir 'tuve fiebre' y que el agente siguiera de largo."""
        conv = Conversacion(ClienteFalso(), EstadoLlamada(escenario="cholecystitis"))
        conv.abrir()
        r = conv.responder("Sí, tuve fiebre anoche, me sentí muy afiebrada")

        assert r.foco is Foco.FIEBRE, "no cambió de tema sin tener el número"
        assert PEDIDOS_DE_DATO[Foco.FIEBRE] in r.texto
        assert r.decision.semaforo is Semaforo.AMARILLO

    def test_el_numero_no_se_pide_dos_veces(self):
        """Insistir por una cifra que el paciente no tiene es hostigarlo."""
        conv = Conversacion(ClienteFalso(), EstadoLlamada(escenario="cholecystitis"))
        conv.abrir()
        conv.responder("tuve fiebre anoche")
        conv.esperar_extraccion()
        r = conv.responder("no, no me la tomé, no tengo termómetro")
        conv.esperar_extraccion()
        assert PEDIDOS_DE_DATO[Foco.FIEBRE] not in r.texto

    def test_no_pide_numero_si_el_paciente_ya_lo_dio(self):
        conv = Conversacion(ClienteFalso(), EstadoLlamada(escenario="cholecystitis"))
        conv.abrir()
        r = conv.responder("sí, tuve 37 y medio")
        assert PEDIDOS_DE_DATO[Foco.FIEBRE] not in r.texto

    def test_una_alarma_tiene_prioridad_sobre_pedir_la_cifra(self):
        """Ante un síntoma de alarma se escala ya, sin completar el cuestionario."""
        conv = Conversacion(ClienteFalso(), EstadoLlamada(escenario="cholecystitis"))
        conv.abrir()
        r = conv.responder("tuve fiebre y además no puedo respirar bien")
        assert r.escala
        assert "dificultad_respiratoria" in r.alarmas_detectadas


class TestTono:
    """La rúbrica evalúa el registro del agente en contexto de salud."""

    def test_el_agente_nunca_tutea(self):
        from src.conversacion import turno as T

        frases = [
            T.APERTURA,
            T.ESCALAMIENTO,
            T.CIERRE_VERDE,
            T.CIERRE_AMARILLO,
            T.ACUSE_PREOCUPACION,
            *T.PREGUNTAS.values(),
            *T.REPREGUNTAS.values(),
            *T.PEDIDOS_DE_DATO.values(),
            *T.ACUSES.values(),
        ]
        prohibidas = ("tienes", "puedes", "sientes", "estás", "tuyo", "contigo")
        for frase in frases:
            bajo = frase.lower()
            assert not any(p in bajo for p in prohibidas), f"tuteo en: {frase}"

    def test_el_escalamiento_dice_que_va_a_pasar_despues(self):
        """Anunciar que algo anda mal sin explicar el siguiente paso deja al
        paciente solo con el susto."""
        from src.conversacion.turno import ESCALAMIENTO

        assert "equipo de salud" in ESCALAMIENTO
        assert "comunicar" in ESCALAMIENTO

    def test_el_cierre_verde_advierte_cuando_volver_a_llamar(self):
        from src.conversacion.turno import CIERRE_VERDE

        assert "fiebre" in CIERRE_VERDE
        assert "avise" in CIERRE_VERDE or "llame" in CIERRE_VERDE
