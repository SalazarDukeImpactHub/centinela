"""Ingesta del corpus clínico al índice vectorial.

Se ejecuta UNA vez, en desarrollo. El índice resultante viaja versionado en el
repositorio: regenerarlo durante el levantamiento del jurado consumiría casi todo
el presupuesto de 15 minutos de la compuerta G2.

Uso:
    python scripts/ingest.py                 # corpus del kit oficial
    python scripts/ingest.py --textos RUTA   # otro corpus
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.rag.chunk import fragmentar  # noqa: E402
from src.rag.extract import recorrer_corpus  # noqa: E402
from src.rag.index import IndiceClinico  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
TEXTOS_KIT = RAIZ.parent / "techsphere-2026" / "dataset" / "textos"
INDICE = RAIZ / "chroma_data"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--textos", type=Path, default=TEXTOS_KIT)
    parser.add_argument("--indice", type=Path, default=INDICE)
    args = parser.parse_args()

    if not args.textos.exists():
        raise SystemExit(
            f"No existe {args.textos}.\n"
            "Cloná el kit oficial:\n"
            "  git clone https://github.com/TechSphere2026/ParticipantArtifacts.git"
        )

    print(f"Corpus:  {args.textos}")
    print(f"Índice:  {args.indice}\n")

    inicio = time.perf_counter()
    corpus = recorrer_corpus(args.textos)
    print(corpus.resumen())

    if corpus.duplicados:
        print("\nDuplicados descartados:")
        for duplicado, original in corpus.duplicados:
            print(f"  {duplicado.ruta.name[:60]}")
            print(f"    = {original.ruta.name[:60]}")
    if corpus.sin_texto:
        print("\nSin capa de texto (requieren OCR, excluidos del índice):")
        for doc in corpus.sin_texto:
            print(f"  {doc.ruta.name[:70]}")

    chunks = [c for doc in corpus.documentos for c in fragmentar(doc)]
    print(f"\nChunks a indexar: {len(chunks):,}")

    indice = IndiceClinico(args.indice)
    print(f"Cargando modelo de embeddings ({indice._modelo_nombre})...")
    _ = indice.modelo

    ultimo = time.perf_counter()

    def progreso(hechos: int, total: int) -> None:
        nonlocal ultimo
        ahora = time.perf_counter()
        if ahora - ultimo < 15 and hechos < total:
            return
        ultimo = ahora
        transcurrido = ahora - inicio
        ritmo = hechos / transcurrido if transcurrido else 0
        faltan = (total - hechos) / ritmo / 60 if ritmo else 0
        print(f"  {hechos:>5}/{total}  {ritmo:5.1f} ch/s  restan ~{faltan:.1f} min")

    indexados = indice.indexar(chunks, progreso=progreso)
    total_s = time.perf_counter() - inicio

    print(f"\nIndexados {indexados:,} chunks en {total_s / 60:.1f} min")
    print(f"Total en el índice: {indice.total_chunks():,} chunks")
    print(f"Documentos: {len(indice.documentos())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
