"""Saneo del nombre de archivo en la consola de conocimiento.

HALLAZGO DE LA AUDITORÍA PRE-ENTREGA (2026-08-10). El endpoint de carga
concatenaba el nombre que enviaba el cliente directo contra la carpeta de
subidas:

    destino = SUBIDAS / archivo.filename

`pathlib` no sanea nada. Dos formas medidas escapaban de la carpeta:

    "../../../fuera.pdf"        -> escribía dos niveles arriba del proyecto
    "C:/Windows/Temp/evil.pdf"  -> ruta ABSOLUTA: pathlib descarta la base

Escritura de archivo arbitraria, limitada solo a la extensión `.pdf`. El
contenedor corre sin privilegios, pero sobrescribir un PDF del propio corpus
alcanza para envenenar lo que el agente le cita a un paciente — que es
exactamente el ataque contra el que existe `saneamiento.py`, entrando por otra
puerta.

La revisión F2 declaraba «path traversal en consola de carga» como cubierto. No
lo estaba: se comprobó la extensión, no la ruta.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.api.app import SUBIDAS, _nombre_seguro


class TestRutasQueEscapan:
    @pytest.mark.parametrize(
        "atacante",
        [
            "../../../fuera.pdf",
            "..\\..\\..\\fuera.pdf",
            "subdir/../../fuera.pdf",
            "/etc/cron.d/evil.pdf",
            "C:/Windows/Temp/evil.pdf",
            "C:\\Windows\\Temp\\evil.pdf",
        ],
    )
    def test_el_nombre_queda_sin_ruta(self, atacante: str):
        """Sea cual sea la ruta enviada, solo sobrevive el nombre del archivo."""
        nombre = _nombre_seguro(atacante)
        assert "/" not in nombre and "\\" not in nombre
        assert ".." not in nombre

    @pytest.mark.parametrize(
        "atacante",
        [
            "../../../fuera.pdf",
            "C:/Windows/Temp/evil.pdf",
            "/etc/cron.d/evil.pdf",
        ],
    )
    def test_el_destino_cae_dentro_de_subidas(self, atacante: str):
        destino = SUBIDAS / _nombre_seguro(atacante)
        assert SUBIDAS.resolve() in destino.resolve().parents

    @pytest.mark.parametrize("atacante", ["", "   ", "..", "/", ".oculto.pdf", "notas.txt"])
    def test_lo_que_no_es_un_pdf_con_nombre_se_rechaza(self, atacante: str):
        with pytest.raises(HTTPException) as exc:
            _nombre_seguro(atacante)
        assert exc.value.status_code == 400


class TestElNombreLegitimoSobrevive:
    """Cerrar la puerta no puede romper la demostración de conocimiento vivo."""

    @pytest.mark.parametrize(
        "nombre",
        [
            "cuidados-tras-mastectomia-DEMO.pdf",
            "Guía de cuidados (2026).pdf",
            "informe final.PDF",
        ],
    )
    def test_pasa_intacto(self, nombre: str):
        assert _nombre_seguro(nombre) == nombre

    def test_el_navegador_que_manda_la_ruta_completa_no_se_rechaza(self):
        """Algunos clientes envían la ruta local entera. Se recorta, no se cae."""
        assert _nombre_seguro("C:\\Users\\ana\\Documentos\\guia.pdf") == "guia.pdf"
