"""Experimento: ¿un reranker cruzado mejora la compuerta de grounding?

NO forma parte del sistema. Es un experimento aislado cuyo resultado se cita en
el informe final, para que la decisión de no incluirlo esté sustentada con un
número y no con una intuición.

Un reranker cruzado (cross-encoder) lee la consulta y el fragmento JUNTOS, y por
eso ordena mucho mejor que la similitud de vectores calculados por separado. El
precio es que hay que ejecutarlo una vez por candidato, en vez de una búsqueda
vectorial única.

Qué se mide, sobre las mismas 13 consultas con las que se calibró la compuerta:

  1. Latencia del reranker sobre 40 candidatos, en el hardware de referencia.
  2. Si cambia alguna decisión de la compuerta —es decir, si arregla algo.

    python scripts/experimento_reranker.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.rag import bilingue  # noqa: E402
from src.rag.grounding import RECUPERACION_AMPLIA, verificar  # noqa: E402
from src.rag.index import IndiceClinico  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
INDICE = RAIZ / "chroma_data"

# El cross-encoder multilingüe más liviano disponible. Uno más grande sería aún
# más lento, así que este es el mejor caso para la latencia.
MODELO_RERANKER = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"

# Las mismas consultas con las que se calibró la compuerta: 7 que deben pasar y
# 6 que deben bloquearse.
CONSULTAS = [
    (True, "cholecystitis", "cuando puedo comer normal despues de la operacion de vesicula"),
    (True, "cholecystitis", "que cuidados necesita la herida"),
    (True, "cholecystitis", "cuando me puedo bañar"),
    (True, "cholecystitis", "puedo hacer fuerza o levantar peso"),
    (True, "Appendicitis", "cuando puedo volver a trabajar"),
    (True, "colorectal cancer", "que dieta debo seguir"),
    (True, "total joint replacement", "cuando puedo apoyar el peso"),
    (False, "cholecystitis", "cuidados tras una amigdalectomia"),
    (False, "cholecystitis", "que dosis de tramadol tomo"),
    (False, "cholecystitis", "cual es la capital de Francia"),
    (False, "cholecystitis", "receta de arroz con pollo"),
    (False, "cholecystitis", "cuidados despues de una cirugia de corazon abierto"),
    (False, "breast_cancer", "cuidados del drenaje despues de mastectomia"),
]


def main() -> int:
    if not INDICE.exists():
        raise SystemExit(f"No existe el índice en {INDICE}")

    print("Cargando el reranker (una sola vez)...")
    inicio = time.perf_counter()
    try:
        from sentence_transformers import CrossEncoder

        reranker = CrossEncoder(MODELO_RERANKER, max_length=512)
    except Exception as exc:  # modelo no disponible o sin red
        print(f"No se pudo cargar: {exc}")
        return 1
    print(f"  carga: {time.perf_counter() - inicio:.1f}s\n")

    indice = IndiceClinico(INDICE)
    _ = indice.modelo  # calentar embeddings

    print(f"{'consulta':52} {'compuerta':10} {'reranker ms':>12} {'coincide':>9}")
    print("-" * 88)

    latencias: list[float] = []
    cambios = 0

    for esperado, escenario, consulta in CONSULTAS:
        veredicto = verificar(indice, consulta, escenario=escenario)

        # Se recupera el mismo conjunto que ve la compuerta y se reordena.
        candidatos = indice.buscar(consulta, k=RECUPERACION_AMPLIA)
        en_ingles = bilingue.traducir_consulta(consulta)
        if en_ingles:
            vistos = {c.chunk_id for c in candidatos}
            candidatos += [
                c
                for c in indice.buscar(en_ingles, k=RECUPERACION_AMPLIA)
                if c.chunk_id not in vistos
            ]
        del_escenario = [c for c in candidatos if c.escenario == escenario]

        ms = 0.0
        mejor_puntaje = None
        if del_escenario:
            pares = [(consulta, c.texto) for c in del_escenario]
            t0 = time.perf_counter()
            puntajes = reranker.predict(pares, show_progress_bar=False)
            ms = (time.perf_counter() - t0) * 1000
            latencias.append(ms)
            mejor_puntaje = float(max(puntajes))

        # El reranker solo puede cambiar la decisión si su mejor puntaje separa
        # lo respondible de lo ausente. Se registra para poder juzgarlo.
        coincide = "—"
        if mejor_puntaje is not None:
            decidiria = mejor_puntaje > 0.5
            coincide = "sí" if decidiria == esperado else "NO"
            if decidiria != veredicto.permitido:
                cambios += 1

        marca = "pasa" if veredicto.permitido else "BLOQ"
        puntaje_txt = f"{mejor_puntaje:.3f}" if mejor_puntaje is not None else "s/cand"
        print(f"{consulta[:50]:52} {marca:10} {ms:>10.0f}   {coincide:>8}  ({puntaje_txt})")

    print("-" * 88)
    if latencias:
        media = sum(latencias) / len(latencias)
        print(f"\nLatencia del reranker: media {media:.0f} ms · "
              f"min {min(latencias):.0f} · max {max(latencias):.0f}")
        print(f"Decisiones que cambiaría respecto de la compuerta actual: {cambios}")
        print(f"\nPresupuesto de latencia del turno (P50 medido): 5.131 ms")
        print(f"El reranker agregaría un {media / 5131 * 100:.0f}% a ese presupuesto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
