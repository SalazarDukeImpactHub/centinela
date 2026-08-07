"""Índice vectorial del corpus clínico sobre ChromaDB.

Elección de embeddings — medida, no heredada:

    modelo                              vel. en i3-1005G1   corpus completo
    BAAI/bge-m3 (sugerido por el reto)        1.5 ch/s          72 min
    paraphrase-multilingual-MiniLM-L12        4.8 ch/s        22.7 min
    intfloat/multilingual-e5-small           12.2 ch/s         8.9 min

Se usa `multilingual-e5-small`. Las bases del reto declaran que los embeddings son
decisión del participante, y BGE-M3 hacía inviable la compuerta G2 (levantamiento
en ≤15 minutos) en hardware modesto.

GOTCHA de la familia e5: fue entrenada con prefijos asimétricos. Un pasaje se
indexa como "passage: ..." y una consulta se emite como "query: ...". Omitirlos
degrada la recuperación de forma silenciosa — no falla, simplemente recupera peor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

from src.rag.chunk import Chunk

MODELO_EMBEDDINGS = "intfloat/multilingual-e5-small"
COLECCION = "corpus_clinico"
LOTE = 64

# La similitud coseno en ChromaDB llega como distancia: 0 = idéntico, 2 = opuesto.
# Por debajo de este umbral de similitud no se permite afirmación clínica alguna.
# Calibrable en F2 contra consultas con respuesta conocida.
UMBRAL_GROUNDING = 0.80


@dataclass
class Recuperado:
    texto: str
    similitud: float
    doc_id: str
    chunk_id: str
    pagina: int
    fuente: str
    escenario: str

    def cita(self) -> str:
        return f"{self.fuente} (p. {self.pagina})"


class IndiceClinico:
    """Índice persistente. Soporta alta y baja de documentos en caliente (G5)."""

    def __init__(self, ruta: Path, modelo: str = MODELO_EMBEDDINGS) -> None:
        self.ruta = ruta
        ruta.mkdir(parents=True, exist_ok=True)
        self._modelo_nombre = modelo
        self._modelo: SentenceTransformer | None = None
        self.cliente = chromadb.PersistentClient(
            path=str(ruta), settings=Settings(anonymized_telemetry=False)
        )
        self.coleccion = self.cliente.get_or_create_collection(
            name=COLECCION, metadata={"hnsw:space": "cosine"}
        )

    @property
    def modelo(self) -> SentenceTransformer:
        """Carga perezosa: el import de torch cuesta ~45s y no siempre hace falta."""
        if self._modelo is None:
            self._modelo = SentenceTransformer(self._modelo_nombre)
        return self._modelo

    def _embeber(self, textos: list[str], *, es_consulta: bool) -> list[list[float]]:
        prefijo = "query: " if es_consulta else "passage: "
        vectores = self.modelo.encode(
            [prefijo + t for t in textos],
            batch_size=LOTE,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectores]

    def indexar(self, chunks: list[Chunk], *, progreso=None) -> int:
        """Agrega chunks al índice. Idempotente: mismo chunk_id sobrescribe."""
        total = 0
        for i in range(0, len(chunks), LOTE):
            lote = chunks[i : i + LOTE]
            self.coleccion.upsert(
                ids=[c.chunk_id for c in lote],
                documents=[c.texto for c in lote],
                embeddings=self._embeber([c.texto for c in lote], es_consulta=False),
                metadatas=[c.como_metadata() for c in lote],
            )
            total += len(lote)
            if progreso:
                progreso(total, len(chunks))
        return total

    def olvidar(self, doc_id: str) -> int:
        """Elimina todos los chunks de un documento. Es la mitad 'olvidar' de G5.

        Devuelve cuántos chunks se borraron, para que la consola pueda mostrar
        evidencia real de la baja en lugar de un mensaje optimista.
        """
        existentes = self.coleccion.get(where={"doc_id": doc_id}, include=[])
        ids = existentes.get("ids", [])
        if ids:
            self.coleccion.delete(ids=ids)
        return len(ids)

    def buscar(self, consulta: str, k: int = 5) -> list[Recuperado]:
        """Recupera los k chunks más cercanos, ordenados por similitud descendente."""
        if self.coleccion.count() == 0:
            return []
        r = self.coleccion.query(
            query_embeddings=self._embeber([consulta], es_consulta=True),
            n_results=min(k, self.coleccion.count()),
            include=["documents", "metadatas", "distances"],
        )
        salida: list[Recuperado] = []
        for texto, meta, distancia in zip(
            r["documents"][0], r["metadatas"][0], r["distances"][0], strict=True
        ):
            salida.append(
                Recuperado(
                    texto=texto,
                    similitud=1.0 - distancia,  # distancia coseno -> similitud
                    doc_id=str(meta["doc_id"]),
                    chunk_id=str(meta["chunk_id"]),
                    pagina=int(meta["pagina"]),
                    fuente=str(meta["fuente"]),
                    escenario=str(meta["escenario"]),
                )
            )
        return salida

    def documentos(self) -> dict[str, dict[str, object]]:
        """Inventario por documento para la consola de administración."""
        todos = self.coleccion.get(include=["metadatas"])
        inventario: dict[str, dict[str, object]] = {}
        for meta in todos.get("metadatas", []):
            doc_id = str(meta["doc_id"])
            entrada = inventario.setdefault(
                doc_id,
                {"fuente": meta["fuente"], "escenario": meta["escenario"], "chunks": 0},
            )
            entrada["chunks"] = int(entrada["chunks"]) + 1
        return inventario

    def total_chunks(self) -> int:
        return self.coleccion.count()
