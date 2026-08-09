"""Pruebas de la detección de minimización sistemática.

MEDIDO sobre los 160 casos: los pacientes rojo minimizan MÁS (mediana 6
marcadores por llamada) que los verde (mediana 2). Contraintuitivo hasta que uno
lo piensa: quien tiene 9 de dolor y dice "un poquito molesto, uno aguanta" no
está informando mal — está aguantando, que es otra cosa.
"""

from __future__ import annotations

import json

import pytest

from src.clinico import minimizacion
from src.clinico.escalamiento import (
    MINIMIZACION_PARA_ESCALAR,
    CuadroClinico,
    Herida,
    Semaforo,
    evaluar,
)
from src.conversacion.turno import Conversacion, EstadoLlamada


class TestDeteccion:
    @pytest.mark.parametrize(
        "texto,grupo",
        [
            ("un poquito molesto no más", "resta_intensidad"),
            ("eso es normal después de la operación", "normaliza"),
            ("uno aguanta, doctora", "aguanta"),
            ("no se preocupe, no es nada", "tranquiliza_al_agente"),
        ],
    )
    def test_reconoce_las_formas_de_minimizar(self, texto: str, grupo: str):
        assert grupo in minimizacion.marcadores_en(texto)

    def test_una_respuesta_directa_no_minimiza(self):
        assert minimizacion.contar("tuve 38.5 de fiebre") == 0
        assert minimizacion.contar("me duele un 8") == 0

    def test_cuenta_varios_marcadores_en_un_turno(self):
        texto = "Ay, no, tranquila doctora, un poquito molesto no más, nada del otro mundo"
        assert minimizacion.contar(texto) >= 2


class TestReglaDeEscalamiento:
    """La señal SOLO pondera hallazgos existentes: nunca crea uno."""

    def test_sobre_un_cuadro_verde_no_hace_nada(self):
        cuadro = CuadroClinico(
            dolor_nrs=1,
            fiebre_c=36.5,
            herida=Herida.NORMAL,
            marcadores_minimizacion=20,
        )
        assert evaluar(cuadro).semaforo is Semaforo.VERDE

    def test_sobre_un_cuadro_amarillo_escala(self):
        cuadro = CuadroClinico(
            herida=Herida.ERITEMA_LEVE,
            marcadores_minimizacion=MINIMIZACION_PARA_ESCALAR,
        )
        decision = evaluar(cuadro)
        assert decision.semaforo is Semaforo.ROJO
        assert any("minimiza" in m for m in decision.motivos)

    def test_debajo_del_umbral_sigue_amarillo(self):
        cuadro = CuadroClinico(
            herida=Herida.ERITEMA_LEVE,
            marcadores_minimizacion=MINIMIZACION_PARA_ESCALAR - 1,
        )
        assert evaluar(cuadro).semaforo is Semaforo.AMARILLO

    def test_el_motivo_explica_la_decision(self):
        """El registro tiene que decir por qué se escaló, no solo que se escaló."""
        cuadro = CuadroClinico(herida=Herida.ERITEMA_LEVE, marcadores_minimizacion=8)
        motivos = " ".join(evaluar(cuadro).motivos)
        assert "8 señales" in motivos
        assert "por debajo de lo real" in motivos


class ClienteFalso:
    def generar(self, prompt, **kwargs):  # noqa: ANN001, ANN003
        from src.modelo.cliente import Respuesta

        return Respuesta(json.dumps({}), 10, 5, 1.0, "falso")


class TestAcumulacionEnLaLlamada:
    def test_los_marcadores_se_acumulan_entre_turnos(self):
        conv = Conversacion(ClienteFalso(), EstadoLlamada(escenario="cholecystitis"))
        conv.abrir()
        conv.responder("Ay no, tranquila doctora, un poquito no más")
        conv.esperar_extraccion()
        primero = conv.estado.cuadro.marcadores_minimizacion
        conv.responder("eso es normal después de la operación, uno aguanta")
        conv.esperar_extraccion()
        assert conv.estado.cuadro.marcadores_minimizacion > primero

    def test_la_extraccion_no_borra_el_contador(self):
        """La fusión construye un cuadro NUEVO: todo campo que no se copie se
        pierde. Los marcadores quedaban en cero y la regla no tenía efecto."""
        conv = Conversacion(ClienteFalso(), EstadoLlamada(escenario="cholecystitis"))
        conv.abrir()
        conv.responder("un poquito no más, no se preocupe")
        antes = conv.estado.cuadro.marcadores_minimizacion
        conv.esperar_extraccion()  # la extracción reemplaza el cuadro
        assert conv.estado.cuadro.marcadores_minimizacion == antes
        assert antes > 0
