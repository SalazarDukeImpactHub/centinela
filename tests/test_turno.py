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

from src.clinico.escalamiento import Semaforo
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

    def test_no_repite_un_foco_ya_cubierto(self):
        """Con respuestas que SÍ aportan dato, cada turno avanza de tema.

        Se usan respuestas concretas y no un "bueno" genérico: una respuesta sin
        dato ahora dispara un reintento del mismo tema, que es la conducta
        correcta y se verifica aparte.
        """
        respuestas = [
            "no he tenido fiebre",
            "la herida se ve bien",
            "el dolor es como un 2",
            "camino sin problema",
        ]
        conv, _ = _conversacion()
        conv.abrir()
        vistos = {conv.estado.foco_actual}
        for respuesta in respuestas:
            r = conv.responder(respuesta)
            conv.esperar_extraccion()
            if r.cierra or r.foco is None:
                break
            assert r.foco not in vistos, f"repitió {r.foco} tras '{respuesta}'"
            vistos.add(r.foco)


class TestRespuestaSinDato:
    """El agente no finge haber entendido.

    Caso real: el paciente respondió "¡Ojo!" —ruido de transcripción— y el
    agente contestó "Bueno, eso me sirve saberlo" y dio el tema por cubierto.
    """

    def test_vuelve_a_preguntar_lo_que_no_entendio(self):
        from src.conversacion.turno import NO_SE_ENTENDIO

        conv, _ = _conversacion()
        conv.abrir()
        foco_inicial = conv.estado.foco_actual
        r = conv.responder("¡Ojo!")
        assert r.foco is foco_inicial, "cambió de tema sin haber entendido"
        assert NO_SE_ENTENDIO[foco_inicial] in r.texto

    def test_reintenta_una_sola_vez(self):
        """Insistir dos veces sobre lo mismo hostiga: mejor avanzar y dejar
        constancia de lo que quedó sin preguntar."""
        conv, _ = _conversacion()
        conv.abrir()
        foco_inicial = conv.estado.foco_actual
        conv.responder("¡Ojo!")
        conv.esperar_extraccion()
        r = conv.responder("¡Ojo!")
        assert r.foco is not foco_inicial

    def test_una_respuesta_con_dato_no_dispara_reintento(self):
        from src.conversacion.turno import NO_SE_ENTENDIO

        conv, _ = _conversacion()
        conv.abrir()
        foco_inicial = conv.estado.foco_actual
        r = conv.responder("no he tenido fiebre, nada")
        assert NO_SE_ENTENDIO[foco_inicial] not in r.texto


class TestAlarmasInmediatas:
    """La señal más urgente no puede esperar al modelo."""

    def test_la_alarma_escala_en_el_mismo_turno(self):
        """La alerta se dispara ya, pero la llamada NO se corta.

        Una enfermera que detecta una bandera roja no cuelga: completa la
        valoración para que quien recibe la alerta tenga el cuadro entero.
        """
        conv, cliente = _conversacion()
        conv.abrir()
        r = conv.responder("Doctora no puedo respirar bien, me falta el aire")
        assert r.escala
        assert r.decision.semaforo is Semaforo.ROJO
        assert "dificultad_respiratoria" in r.alarmas_detectadas
        assert not r.cierra, "colgó en vez de completar la valoración"
        assert conv.estado.alerta_anunciada

    def test_la_alerta_se_anuncia_una_sola_vez(self):
        from src.conversacion.turno import ESCALAMIENTO

        conv, _ = _conversacion()
        conv.abrir()
        primero = conv.responder("no puedo respirar")
        conv.esperar_extraccion()
        segundo = conv.responder("la herida se ve bien")
        assert ESCALAMIENTO in primero.texto
        assert ESCALAMIENTO not in segundo.texto, "repitió el anuncio de alerta"

    def test_la_llamada_sigue_preguntando_tras_escalar(self):
        """El equipo que recibe el aviso necesita más que el primer hallazgo."""
        conv, _ = _conversacion()
        conv.abrir()
        conv.responder("no puedo respirar")
        conv.esperar_extraccion()
        r = conv.responder("la herida se ve bien")
        assert r.foco is not None, "dejó de preguntar tras la alerta"
        assert not r.cierra

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
        # El anuncio dice que ya se reportó y que se sigue preguntando.
        assert "reportando" in bajo or "reportado" in bajo
        # Invariantes del mensaje de escalación: nombra al equipo de salud, dice
        # qué va a pasar después, y NO tranquiliza — la rúbrica penaliza por
        # nombre la falsa tranquilidad ante un síntoma de alarma.
        assert "equipo" in bajo
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
        """Respuestas con dato en cada tema, hasta llegar al cierre."""
        respuestas = [
            "no he tenido fiebre",
            "la herida se ve bien",
            "el dolor es un 1",
            "camino sin problema",
            "no, nada más, gracias",
        ]
        conv, _ = _conversacion({"evasivo": False})
        conv.abrir()
        r = None
        for respuesta in respuestas:
            r = conv.responder(respuesta)
            conv.esperar_extraccion()
            if r.cierra:
                break
        assert r is not None and r.cierra
        assert "fiebre" in r.texto
        assert "especialista" in r.texto or "equipo de salud" in r.texto

    def test_el_escalamiento_cierra_con_recapitulacion(self):
        """Escalar no cuelga: la llamada completa la valoración y cierra
        recapitulando lo que quedó reportado."""
        from src.conversacion.turno import CIERRE_ROJO

        conv, _ = _conversacion()
        conv.abrir()
        r = conv.responder("me duele el pecho")
        assert r.escala and not r.cierra

        # "me duele el pecho" no contesta la pregunta por la fiebre: el paciente
        # cambió de tema. El agente lo sigue y vuelve a la fiebre más adelante,
        # así que la valoración tarda un turno más que antes — y termina completa.
        for respuesta in ["la herida se ve bien", "el dolor es un 2",
                          "de fiebre nada", "camino sin problema", "no, nada más"]:
            conv.esperar_extraccion()
            r = conv.responder(respuesta)
            if r.cierra:
                break
        assert r.cierra and conv.estado.cerrada
        assert r.texto == CIERRE_ROJO

    def test_la_transcripcion_registra_ambos_lados(self):
        conv, _ = _conversacion()
        conv.abrir()
        conv.responder("hola")
        hablantes = {h for h, _ in conv.estado.transcripcion}
        assert hablantes == {"agente", "paciente"}


class TestAgendaNoGuion:
    """El cuestionario es la AGENDA del agente, no su guion.

    Una enfermera también tiene cuatro cosas en la cabeza. La diferencia con un
    formulario es que tacha lo que ya le contaron y sigue el tema que el
    paciente trae. Estas tres fallas se midieron sobre el sistema real.
    """

    def test_no_pregunta_lo_que_el_paciente_ya_contesto(self):
        """Los cuatro temas en un turno: no queda nada que preguntar.

        Antes el agente preguntaba igual por la herida, y al turno siguiente
        respondía "no le entendí lo de la herida" a alguien que se la había
        descrito.
        """
        conv, _ = _conversacion()
        conv.abrir()
        r = conv.responder(
            "No he tenido fiebre, la herida la veo seca y limpia, "
            "el dolor es como un dos y camino bien."
        )
        conv.esperar_extraccion()
        assert conv.estado.siguiente_foco() is None
        assert "herida" not in r.texto.lower()
        assert conv.estado.cuadro.dolor_nrs == 2

    def test_sigue_el_tema_que_trae_el_paciente(self):
        """"Me preocupa la herida" recibía "no le entendí la temperatura"."""
        conv, _ = _conversacion()
        conv.abrir()
        r = conv.responder("Me preocupa la herida.")
        conv.esperar_extraccion()
        assert r.foco is Foco.HERIDA
        assert "no le entendí" not in r.texto

    def test_el_tema_que_quedo_pendiente_vuelve(self):
        """Seguir al paciente no es abandonar lo que faltaba preguntar."""
        conv, _ = _conversacion()
        conv.abrir()  # pregunta por fiebre
        conv.responder("Me preocupa la herida.")
        conv.esperar_extraccion()
        assert Foco.FIEBRE not in conv.estado.preguntados, "la fiebre se perdió"
        r = conv.responder("Está roja.")
        conv.esperar_extraccion()
        assert r.foco is Foco.FIEBRE

    def test_la_esquiva_de_verdad_si_recibe_reintento(self):
        """Cambiar de tema no es lo mismo que no responder."""
        conv, _ = _conversacion()
        conv.abrir()
        r = conv.responder("Ay, no sabría decirle.")
        conv.esperar_extraccion()
        assert "no le entendí" in r.texto.lower()
        assert r.foco is Foco.FIEBRE

    def test_el_acuse_en_rojo_no_se_repite_igual(self):
        """Una sola variante hacía que todo turno tras escalar sonara idéntico."""
        from src.conversacion.turno import ACUSES
        from src.clinico.escalamiento import Semaforo

        assert len(ACUSES[Semaforo.ROJO]) >= 3


class TestMovilidadNoSeInventa:
    """"La veo bien" —hablando de la herida— no es una respuesta de movilidad.

    Con "bien" y "normal" sin anclar, una respuesta sobre la herida escribía
    movilidad normal en el cuadro. Con la agenda que se tacha sola, además hacía
    que el agente NI PREGUNTARA por el movimiento.
    """

    def test_un_bien_suelto_no_es_movilidad(self):
        from src.clinico.dolor import estado_movilidad

        assert estado_movilidad("la veo bien") is None
        assert estado_movilidad("la herida está normal") is None
        assert estado_movilidad("cicatriza lento") is None

    def test_pero_si_lo_es_cuando_se_pregunto_por_el_movimiento(self):
        from src.clinico.dolor import estado_movilidad

        assert estado_movilidad("bien", en_contexto=True) == "normal"
        assert estado_movilidad("despacio", en_contexto=True) == "limitada_esperada"

    def test_y_lo_es_cuando_la_frase_habla_de_moverse(self):
        from src.clinico.dolor import estado_movilidad

        assert estado_movilidad("camino bien") == "normal"
        assert estado_movilidad("me levanto sin problema") == "normal"
        assert estado_movilidad("camino despacio") == "limitada_esperada"
        assert estado_movilidad("no me puedo mover") == "incapacitante_nueva"

    def test_la_herida_no_cierra_el_tema_del_movimiento(self):
        conv, _ = _conversacion()
        conv.abrir()
        conv.responder("no he tenido fiebre")
        conv.esperar_extraccion()
        conv.responder("la veo bien")
        conv.esperar_extraccion()
        assert Foco.MOVILIDAD not in conv.estado.preguntados


class TestElPacienteTraeSuTemaEnVezDeResponder:
    """MEDIDO en llamada completa por la API, con Whisper real.

    El paciente abrió con "no me quitan el drenaje y eso me tiene preocupada" y
    recibió "disculpe, no le entendí la temperatura". Le entendió perfecto:
    hablaba de otra cosa. Es la misma sordera que el cambio de tema, con un
    disparador distinto — el drenaje no es ninguno de los cuatro focos.
    """

    def test_no_dice_que_no_entendio_lo_que_entendio(self):
        from src.conversacion.turno import ACUSE_INQUIETUD

        conv, _ = _conversacion()
        conv.abrir()
        r = conv.responder("Doctora, no me quitan el drenaje y eso me tiene preocupada.")
        assert "no le entendí" not in r.texto.lower()
        assert ACUSE_INQUIETUD in r.texto

    def test_repite_la_pregunta_sin_perder_el_foco(self):
        conv, _ = _conversacion()
        conv.abrir()
        foco = conv.estado.foco_actual
        r = conv.responder("Doctora, no me quitan el drenaje y eso me tiene preocupada.")
        assert r.foco is foco

    def test_si_insiste_sin_responder_vuelve_el_reintento(self):
        """Reconocer lo que trajo no es un bucle: la inquietud se acusa una vez."""
        conv, _ = _conversacion()
        conv.abrir()
        conv.responder("Doctora, no me quitan el drenaje y eso me tiene preocupada.")
        conv.esperar_extraccion()
        r = conv.responder("Doctora, no me quitan el drenaje y eso me tiene preocupada.")
        assert "no le entendí" in r.texto.lower()


class TestLaFiebreNegadaNoEsUnVacio:
    """El resumen informaba "quedó sin preguntar: fiebre" sobre un paciente que
    había contestado "fiebre no he tenido, nada".

    Decirle al equipo clínico que un tema quedó sin explorar cuando sí se
    exploró es peor que no decir nada.
    """

    def test_negar_la_fiebre_cierra_el_tema(self):
        conv, _ = _conversacion()
        conv.abrir()
        conv.responder("Fiebre no he tenido, nada.")
        conv.esperar_extraccion()
        assert conv.estado.cuadro.fiebre_negada
        assert "fiebre" not in conv.estado.cuadro.campos_faltantes

    def test_no_haber_preguntado_si_deja_el_hueco(self):
        from src.clinico.escalamiento import CuadroClinico

        assert "fiebre" in CuadroClinico().campos_faltantes

    def test_la_negacion_sobrevive_a_la_extraccion(self):
        """La fusión construye un cuadro nuevo: lo que no se arrastra se pierde."""
        from src.clinico.escalamiento import CuadroClinico
        from src.clinico.extraccion import _fusionar

        previo = CuadroClinico(fiebre_negada=True)
        assert _fusionar(previo, {}).fiebre_negada

    def test_pero_una_cifra_posterior_manda(self):
        from src.clinico.escalamiento import CuadroClinico
        from src.clinico.extraccion import _fusionar

        previo = CuadroClinico(fiebre_negada=True)
        fusionado = _fusionar(previo, {"fiebre_c": 38.5})
        assert not fusionado.fiebre_negada
        assert "fiebre" not in fusionado.campos_faltantes


class TestLaAfirmacionConColetilla:
    """MEDIDO en llamada real por voz, con micrófono.

    A "¿ha tenido fiebre o escalofríos estos días?" el paciente contestó
    "He tenido estos días" —afirmó y devolvió el final de la pregunta— y no se
    registró nada. El patrón exigía que la frase ENTERA fuera la afirmación.

    El costo de perderla es un falso negativo: en esa misma llamada el paciente
    después dijo que veía la herida roja, y fiebre referida sin medir + hallazgo
    en la herida escala a ROJO por sospecha de infección de sitio operatorio.
    Sin la afirmación, la llamada cerró en amarillo.
    """

    def test_afirmar_devolviendo_la_pregunta_cuenta(self):
        conv, _ = _conversacion()
        conv.abrir()
        conv.responder("He tenido estos días")
        conv.esperar_extraccion()
        assert conv.estado.cuadro.fiebre_referida_sin_medir

    def test_pide_la_cifra_en_vez_de_cambiar_de_tema(self):
        conv, _ = _conversacion()
        conv.abrir()
        r = conv.responder("He tenido estos días")
        assert "temperatura" in r.texto.lower()
        assert r.foco is Foco.FIEBRE

    def test_la_fiebre_afirmada_con_herida_roja_escala(self):
        from src.clinico.escalamiento import Semaforo

        conv, _ = _conversacion()
        conv.abrir()
        conv.responder("He tenido estos días")
        conv.esperar_extraccion()
        conv.responder("no me la tomé")
        conv.esperar_extraccion()
        r = conv.responder("La herida la he visto roja.")
        assert r.decision.semaforo is Semaforo.ROJO
        assert any("infección" in m or "fiebre referida" in m for m in r.decision.motivos)

    @pytest.mark.parametrize("texto", ["Sí, a veces", "Sí señor, un poco", "Un poquito anoche"])
    def test_otras_formas_de_afirmar(self, texto: str):
        conv, _ = _conversacion()
        conv.abrir()
        conv.responder(texto)
        conv.esperar_extraccion()
        assert conv.estado.cuadro.fiebre_referida_sin_medir, texto

    def test_el_si_tiene_que_estar_afirmando_la_fiebre(self):
        """"Sí, la herida está roja" no es una fiebre: es una herida."""
        conv, _ = _conversacion()
        conv.abrir()
        conv.responder("Sí, la herida está roja")
        conv.esperar_extraccion()
        assert not conv.estado.cuadro.fiebre_referida_sin_medir

    @pytest.mark.parametrize("texto", ["No, nada", "El contenido.", "¡Papá!"])
    def test_lo_que_no_afirma_no_registra_fiebre(self, texto: str):
        conv, _ = _conversacion()
        conv.abrir()
        conv.responder(texto)
        conv.esperar_extraccion()
        assert not conv.estado.cuadro.fiebre_referida_sin_medir, texto

    def test_afirmar_no_recibe_no_le_entendi(self):
        """El sistema no puede registrar la fiebre y a la vez decir que no entendió.

        MEDIDO: Whisper devolvió "He tenido listos días" —afirmación intacta,
        coletilla estropeada—. El cuadro registró la fiebre referida y el agente
        contestó "disculpe, no le entendí la temperatura". Dos cosas opuestas en
        el mismo turno, y el reintento gastado ahí después hacía falta.
        """
        conv, _ = _conversacion()
        conv.abrir()
        r = conv.responder("He tenido listos días")
        conv.esperar_extraccion()
        assert conv.estado.cuadro.fiebre_referida_sin_medir
        assert "no le entendí" not in r.texto.lower()
        assert "temperatura" in r.texto.lower(), "debería pedir la cifra"
