"""Fragmentación del corpus clínico con trazabilidad hasta la página de origen.

La rúbrica exige que cada afirmación clínica pueda rastrearse hasta el documento
que la sustenta, y que esa referencia resista una verificación contra la fuente
real. Por eso el chunk no es solo texto: es texto + de dónde salió.

Estrategia: ventana deslizante sobre párrafos, con solape. El solape existe
porque una recomendación clínica partida al medio por un corte arbitrario pierde
su condición ("...administrar antibiótico" / "...si la fiebre supera 38 °C" son
peligrosas por separado).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.rag.extract import Documento

# Un chunk debe entrar cómodo en el contexto junto con otros y con el diálogo.
# Medido en caracteres, no en tokens: es determinista y no depende del tokenizador.
MAX_CHARS = 1200
SOLAPE_CHARS = 200
MIN_CHARS = 100  # por debajo de esto el fragmento no sostiene una afirmación


@dataclass
class Chunk:
    texto: str
    doc_id: str
    chunk_id: str
    escenario: str
    pagina: int
    fuente: str  # nombre del archivo, para citar de forma legible

    def como_metadata(self) -> dict[str, str | int]:
        """Metadatos que viajan al índice vectorial. ChromaDB solo acepta escalares."""
        return {
            "doc_id": self.doc_id,
            "chunk_id": self.chunk_id,
            "escenario": self.escenario,
            "pagina": self.pagina,
            "fuente": self.fuente,
        }

    def cita(self) -> str:
        """Referencia legible para auditar contra la fuente real."""
        return f"{self.fuente} (p. {self.pagina})"


def _normalizar(texto: str) -> str:
    """Colapsa espacios y quita guiones de corte de línea de los PDF."""
    texto = re.sub(r"-\n(\w)", r"\1", texto)  # palabra cortada al final de renglón
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def _partir(texto: str) -> list[str]:
    """Divide en fragmentos de hasta MAX_CHARS respetando límites de oración.

    Corta en el último punto que quepa dentro de la ventana; si no hay ninguno,
    corta en el último espacio. Nunca parte una palabra al medio.
    """
    if len(texto) <= MAX_CHARS:
        return [texto] if len(texto) >= MIN_CHARS else []

    fragmentos: list[str] = []
    inicio = 0
    while inicio < len(texto):
        fin = min(inicio + MAX_CHARS, len(texto))
        if fin < len(texto):
            ventana = texto[inicio:fin]
            corte = max(ventana.rfind(". "), ventana.rfind("? "), ventana.rfind("! "))
            if corte <= MAX_CHARS // 2:  # sin punto útil: cortar por palabra
                corte = ventana.rfind(" ")
            if corte > 0:
                fin = inicio + corte + 1

        fragmento = texto[inicio:fin].strip()
        if len(fragmento) >= MIN_CHARS:
            fragmentos.append(fragmento)

        if fin >= len(texto):
            break
        inicio = max(fin - SOLAPE_CHARS, inicio + 1)

    return fragmentos


def fragmentar(doc: Documento) -> list[Chunk]:
    """Convierte un documento en chunks citables.

    Fragmenta página por página en lugar de sobre el texto completo: así el número
    de página de cada chunk es exacto, no aproximado. Una cita que apunta a la
    página equivocada es peor que no citar — el jurado verifica contra la fuente.
    """
    chunks: list[Chunk] = []
    for pagina in doc.paginas:
        texto = _normalizar(pagina.texto)
        for i, fragmento in enumerate(_partir(texto)):
            chunks.append(
                Chunk(
                    texto=fragmento,
                    doc_id=doc.doc_id,
                    chunk_id=f"{doc.doc_id}:p{pagina.numero}:{i}",
                    escenario=doc.escenario,
                    pagina=pagina.numero,
                    fuente=doc.ruta.name,
                )
            )
    return chunks
