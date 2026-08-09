"""Pruebas del puente español↔inglés para el corpus bilingüe.

El corpus del reto mezcla idiomas: colecistitis y la carpeta `breast_cancer`
están mayormente en inglés, y los pacientes preguntan en español coloquial.
MEDIDO antes de la corrección: 5 de 8 consultas clínicas legítimas quedaban
bloqueadas, con el agente diciendo "no lo sé" sobre material que sí tenía.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.rag import bilingue

INDICE = Path(__file__).resolve().parents[1] / "chroma_data"


class TestEquivalencias:
    @pytest.mark.parametrize(
        "termino,esperado",
        [
            ("herida", "wound"),
            ("fiebre", "fever"),
            ("vesicula", "gallbladder"),
            ("bañar", "shower"),
            ("drenaje", "drain"),
            ("dolor", "pain"),
        ],
    )
    def test_traduce_el_vocabulario_clinico(self, termino: str, esperado: str):
        assert esperado in bilingue.equivalentes_de(termino)

    def test_funciona_en_ambas_direcciones(self):
        """Un fragmento en inglés debe reconocerse desde una consulta en español
        y al revés."""
        assert "herida" in bilingue.equivalentes_de("wound")
        assert "wound" in bilingue.equivalentes_de("herida")

    def test_tolera_tildes_y_enes(self):
        """Las claves se normalizan al cargar: "bañar" nunca habría encontrado
        su entrada si se comparaba sobre texto sin tildes."""
        assert bilingue.equivalentes_de("bañar") == bilingue.equivalentes_de("banar")

    def test_un_termino_sin_equivalente_se_devuelve_solo(self):
        assert bilingue.equivalentes_de("aguardiente") == {"aguardiente"}


class TestTraduccionDeConsulta:
    def test_produce_terminos_del_corpus(self):
        t = bilingue.traducir_consulta("cuando me puedo bañar")
        assert t and "shower" in t

    def test_enmarca_en_registro_clinico(self):
        """MEDIDO: el término suelto recupera con 0,816 —bajo el umbral— y la
        misma búsqueda enmarcada como bolsa clínica sube a 0,870 y pasa. Las
        guías están escritas en terminología, no en lenguaje de paciente."""
        t = bilingue.traducir_consulta("cuando me puedo bañar")
        assert t.startswith("postoperative")
        assert t.endswith("instructions")

    def test_sin_terminos_clinicos_no_traduce(self):
        assert bilingue.traducir_consulta("cual es la capital de Francia") is None


@pytest.mark.skipif(not INDICE.exists(), reason="índice no construido")
class TestSobreElIndiceReal:
    @pytest.fixture(scope="class")
    def indice(self):
        from src.rag.index import IndiceClinico

        return IndiceClinico(INDICE)

    @pytest.mark.parametrize(
        "consulta,escenario",
        [
            ("cuando me puedo bañar", "cholecystitis"),
            ("puedo hacer fuerza o levantar peso", "cholecystitis"),
            ("cuando puedo comer normal despues de la operacion de vesicula", "cholecystitis"),
            ("cuando puedo volver a trabajar", "Appendicitis"),
        ],
    )
    def test_las_consultas_en_espanol_encuentran_el_corpus_en_ingles(
        self, indice, consulta: str, escenario: str
    ):
        from src.rag.grounding import verificar

        veredicto = verificar(indice, consulta, escenario=escenario)
        assert veredicto.permitido, f"bloqueada: {veredicto.motivo}"
        assert veredicto.fragmentos

    def test_el_puente_no_abre_la_puerta_a_lo_que_debe_bloquearse(self, indice):
        """La ampliación no puede costar precisión: lo ajeno sigue afuera."""
        from src.rag.grounding import verificar

        for consulta in [
            "cuidados tras una amigdalectomia",
            "que dosis de tramadol tomo",
            "cual es la capital de Francia",
            "cuidados despues de una cirugia de corazon abierto",
        ]:
            assert not verificar(indice, consulta, escenario="cholecystitis").permitido
