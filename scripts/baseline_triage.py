"""Línea base del motor de escalamiento — umbrales clínicos deterministas.

Valida los umbrales del triaje contra los 160 casos etiquetados del dataset,
operando directamente sobre el cuadro clínico real (trayectorias).

IMPORTANTE — qué prueba y qué NO prueba este script:
  - SÍ prueba: que los umbrales clínicos, aplicados a un cuadro clínico correcto,
    reproducen el ground truth con recall perfecto en los casos rojo.
  - NO prueba: que el agente logre extraer ese cuadro clínico conversando con un
    paciente evasivo. Esa es la parte difícil (extracción estructurada, F1).

Este script establece el TECHO del motor de escalamiento. La distancia entre este
resultado y el del pipeline conversacional completo es exactamente el error de
extracción.

Uso:
    python scripts/baseline_triage.py [--dataset RUTA]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "techsphere-2026" / "dataset"

# Umbrales clínicos. Calibrados a favor del recall en rojo: en postoperatorio un
# falso negativo es riesgo clínico; un falso positivo cuesta una llamada.
FIEBRE_ROJO = 38.0
FIEBRE_AMARILLO = 37.5
DOLOR_ROJO = 8
DOLOR_AMARILLO = 4


def triage(dolor_nrs: float, fiebre_c: float, herida: str, movilidad: str) -> str:
    """Clasifica criticidad. Determinista, en código: no se negocia con un prompt."""
    if fiebre_c >= FIEBRE_ROJO or herida == "secrecion_purulenta" or dolor_nrs >= DOLOR_ROJO:
        return "rojo"
    if (
        fiebre_c >= FIEBRE_AMARILLO
        or herida == "eritema_leve"
        or dolor_nrs >= DOLOR_AMARILLO
        or movilidad == "incapacitante_nueva"
    ):
        return "amarillo"
    return "verde"


def load_cases(dataset: Path) -> pd.DataFrame:
    """Une trayectorias (cuadro clínico) con el label de referencia de las conversaciones."""
    trayectorias = pd.read_excel(dataset / "trayectorias_postop_silver.xlsx", sheet_name="result")
    dialogos = pd.read_excel(dataset / "dataset_final.xlsx", sheet_name="result")

    # El join no es directo: caso_id = "caso_" + trayectoria_id
    labels = dialogos.groupby("caso_id")["label_ground_truth"].first().rename("label")
    trayectorias["caso_id"] = "caso_" + trayectorias["trayectoria_id"].astype(str)
    casos = trayectorias.merge(labels, on="caso_id", how="left")

    if casos["label"].isna().any():
        raise SystemExit(f"join incompleto: {casos['label'].isna().sum()} casos sin label")
    return casos


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()

    if not args.dataset.exists():
        raise SystemExit(f"dataset no encontrado en {args.dataset}")

    casos = load_cases(args.dataset)
    casos["pred"] = casos.apply(
        lambda r: triage(r.dolor_nrs, r.fiebre_c, r.herida, r.movilidad), axis=1
    )

    print(f"Casos evaluados: {len(casos)}")
    print("\n=== Matriz de confusión ===")
    print(pd.crosstab(casos["label"], casos["pred"], rownames=["real"], colnames=["pred"]))

    rojos = casos[casos.label == "rojo"]
    aciertos = int((rojos.pred == "rojo").sum())
    falsos_negativos = casos[(casos.label == "rojo") & (casos.pred != "rojo")]
    sobre_escalados = int(((casos.label == "verde") & (casos.pred != "verde")).sum())

    print(f"\nRecall en rojo:          {aciertos}/{len(rojos)}")
    print(f"Falsos negativos rojo:   {len(falsos_negativos)}  (objetivo: 0)")
    print(f"Verdes sobre-escalados:  {sobre_escalados}  (costo aceptado: una llamada)")
    print(f"Exactitud global:        {(casos.label == casos.pred).mean():.3f}")

    if not falsos_negativos.empty:
        print("\nFALLA — falsos negativos en rojo:")
        print(falsos_negativos[["caso_id", "dolor_nrs", "fiebre_c", "herida", "movilidad"]])
        return 1

    print("\nOK — cero falsos negativos en casos rojo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
