"""Pruebas del ciclo de conocimiento vivo — compuerta G5 del reto.

El enunciado es literal: "Subes un documento desde tu consola de administración y
el agente lo usa; lo eliminas y el agente lo olvida. Se verifica con un documento
de prueba que no forma parte de ningún corpus entregado."

Fallar esta compuerta significa que la entrega no se puntúa, así que el ciclo
completo —alta, uso, baja, olvido— se prueba de punta a punta sobre un índice
real, no con dobles de prueba.

Se usa un índice temporal para no tocar el corpus versionado.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.rag.chunk import Chunk
from src.rag.grounding import verificar
from src.rag.index import IndiceClinico

# Documento de prueba: un procedimiento que NO existe en el corpus del kit, para
# reproducir la condición real de evaluación.
DOC_NUEVO = "doctest00001"
ESCENARIO_NUEVO = "cirugia_bariatrica"

TEXTO_NUEVO = (
    "Cuidados tras cirugía bariátrica por manga gástrica. La dieta líquida se "
    "mantiene durante las dos primeras semanas posteriores al procedimiento. "
    "El paciente debe consumir sesenta mililitros de líquido cada quince minutos "
    "para evitar la deshidratación. La progresión a dieta blanda ocurre en la "
    "tercera semana según tolerancia. Ante vómito persistente tras la ingesta de "
    "líquidos, consultar de inmediato porque puede indicar estenosis de la manga "
    "gástrica, una complicación que requiere valoración endoscópica temprana."
)


def _chunks_nuevos() -> list[Chunk]:
    return [
        Chunk(
            texto=TEXTO_NUEVO,
            doc_id=DOC_NUEVO,
            chunk_id=f"{DOC_NUEVO}:p1:0",
            escenario=ESCENARIO_NUEVO,
            pagina=1,
            fuente="cuidados-bariatrica-prueba.pdf",
        )
    ]


@pytest.fixture
def indice(tmp_path: Path) -> IndiceClinico:
    """Índice temporal. No toca el corpus versionado del repositorio."""
    return IndiceClinico(tmp_path / "chroma_prueba")


class TestCicloCompleto:
    def test_indice_nuevo_arranca_vacio(self, indice: IndiceClinico):
        assert indice.total_chunks() == 0
        assert indice.documentos() == {}

    def test_buscar_en_indice_vacio_no_revienta(self, indice: IndiceClinico):
        """Antes de la primera carga la consola puede consultarse igual."""
        assert indice.buscar("cualquier cosa") == []

    def test_alta_uso_baja_olvido(self, indice: IndiceClinico):
        """El ciclo completo de G5, en el orden en que lo prueba el jurado."""
        consulta = "cuánto líquido debo tomar tras la cirugía bariátrica"

        # 1. Antes de subirlo: el agente no sabe.
        antes = verificar(indice, consulta, escenario=ESCENARIO_NUEVO)
        assert not antes.permitido

        # 2. Se sube el documento.
        indexados = indice.indexar(_chunks_nuevos())
        assert indexados == 1
        assert indice.total_chunks() == 1

        # 3. El agente lo usa, y puede citarlo.
        durante = verificar(indice, consulta, escenario=ESCENARIO_NUEVO)
        assert durante.permitido, durante.motivo
        assert durante.fragmentos
        assert durante.fragmentos[0].doc_id == DOC_NUEVO
        assert "cuidados-bariatrica-prueba.pdf" in durante.citas[0]

        # 4. Se elimina.
        borrados = indice.olvidar(DOC_NUEVO)
        assert borrados == 1
        assert indice.total_chunks() == 0

        # 5. El agente lo olvidó: vuelve a no saber.
        despues = verificar(indice, consulta, escenario=ESCENARIO_NUEVO)
        assert not despues.permitido
        assert not despues.fragmentos

    def test_inventario_refleja_el_documento_cargado(self, indice: IndiceClinico):
        """La consola debe poder listar qué hay cargado y con cuántos chunks."""
        indice.indexar(_chunks_nuevos())
        inventario = indice.documentos()
        assert DOC_NUEVO in inventario
        assert inventario[DOC_NUEVO]["chunks"] == 1
        assert inventario[DOC_NUEVO]["fuente"] == "cuidados-bariatrica-prueba.pdf"

    def test_olvidar_documento_inexistente_no_falla(self, indice: IndiceClinico):
        """La consola no puede reventar si se pide borrar dos veces."""
        indice.indexar(_chunks_nuevos())
        assert indice.olvidar(DOC_NUEVO) == 1
        assert indice.olvidar(DOC_NUEVO) == 0
        assert indice.olvidar("nunca_existio") == 0

    def test_borrar_un_documento_no_afecta_a_los_demas(self, indice: IndiceClinico):
        otro = Chunk(
            texto="Instrucciones de cuidado tras extracción de muela del juicio.",
            doc_id="otrodoc00001",
            chunk_id="otrodoc00001:p1:0",
            escenario="odontologia",
            pagina=1,
            fuente="otro.pdf",
        )
        indice.indexar([*_chunks_nuevos(), otro])
        assert indice.total_chunks() == 2

        indice.olvidar(DOC_NUEVO)
        assert indice.total_chunks() == 1
        assert "otrodoc00001" in indice.documentos()

    def test_reindexar_el_mismo_documento_no_duplica(self, indice: IndiceClinico):
        """La consola puede recibir el mismo archivo dos veces sin corromper el índice."""
        indice.indexar(_chunks_nuevos())
        indice.indexar(_chunks_nuevos())
        assert indice.total_chunks() == 1
        assert indice.documentos()[DOC_NUEVO]["chunks"] == 1

    def test_el_indice_persiste_al_reabrirlo(self, tmp_path: Path):
        """El conocimiento sobrevive a un reinicio del servicio."""
        ruta = tmp_path / "chroma_persistencia"
        primero = IndiceClinico(ruta)
        primero.indexar(_chunks_nuevos())
        assert primero.total_chunks() == 1

        segundo = IndiceClinico(ruta)
        assert segundo.total_chunks() == 1
        assert DOC_NUEVO in segundo.documentos()
