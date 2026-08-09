"""Banco de evaluación conversacional: los 160 casos por el pipeline COMPLETO.

`tests/test_escalamiento.py` prueba el motor con el cuadro clínico ya armado —es
decir, asumiendo extracción perfecta—. Este banco es más duro y más honesto:
reproduce las conversaciones reales del dataset turno a turno, deja que el
sistema extraiga lo que pueda de lo que el paciente efectivamente dijo, y recién
entonces compara contra la etiqueta.

La diferencia entre ambos números es exactamente lo que se pierde en la
extracción, que es lo que el jurado va a ver en la sesión evaluada.

Se corre sobre la capa RUIDOSA por defecto: es la que se parece a una llamada de
verdad —evasivas, ambigüedad, interrupciones de terceros—.

    python scripts/banco_conversacional.py                 # capa2, 160 casos
    python scripts/banco_conversacional.py --capa capa1_limpia --casos 40
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.clinico.escalamiento import evaluar  # noqa: E402
from src.conversacion.turno import Conversacion, EstadoLlamada  # noqa: E402
from src.modelo.cliente import ClienteLocal  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
KIT = RAIZ.parent / "techsphere-2026" / "dataset"

# Módulo de Synthea -> carpeta del corpus. El del kit viene con guion bajo.
ESCENARIOS = {
    "appendicitis": "Appendicitis",
    "cholecystitis": "cholecystitis",
    "colorectal_cancer": "colorectal cancer",
    "total_joint_replacement": "total joint replacement",
    "breast_cancer": "breast_cancer",
}


class ModeloApagado:
    """Cliente que no llama al modelo: mide SOLO lo que el código detecta.

    Es el peor caso realista —el modelo local caído o saturado— y también el
    más rápido de correr. Si el recall en rojo se sostiene acá, se sostiene
    siempre: el modelo únicamente puede agregar información.
    """

    def generar(self, prompt, **kwargs):  # noqa: ANN001, ANN003
        from src.modelo.cliente import Respuesta

        return Respuesta(json.dumps({}), 0, 0, 0.0, "apagado")


def cargar_casos(capa: str, limite: int | None):
    import pandas as pd

    dialogos = pd.read_excel(KIT / "dataset_final.xlsx", sheet_name="result")
    perfiles = pd.read_excel(
        KIT / "perfiles_clinicos_pacientes_silver_contest.xlsx", sheet_name="result"
    )
    escenario_por_paciente = dict(
        zip(perfiles["paciente_id"], perfiles["modulo_synthea"], strict=True)
    )

    turnos = dialogos[dialogos["capa"] == capa].sort_values(["caso_id", "dialogo_id"])
    casos = []
    for caso_id, grupo in turnos.groupby("caso_id", sort=True):
        paciente = grupo["paciente_id"].iloc[0]
        # Se toman los turnos del paciente Y del tercero: el cuidador aporta
        # señales que el paciente calla, y el agente debe procesarlas igual.
        habla = grupo[grupo["hablante"].isin(["paciente", "tercero"])]["texto"].tolist()
        casos.append(
            {
                "caso_id": caso_id,
                "etiqueta": grupo["label_ground_truth"].iloc[0],
                "escenario": ESCENARIOS.get(
                    escenario_por_paciente.get(paciente, ""), "cholecystitis"
                ),
                "turnos": [t for t in habla if isinstance(t, str) and t.strip()],
            }
        )
    return casos[:limite] if limite else casos


def evaluar_caso(caso: dict, cliente) -> str:
    conv = Conversacion(cliente, EstadoLlamada(escenario=caso["escenario"]))
    conv.abrir()
    for texto in caso["turnos"]:
        respuesta = conv.responder(texto)
        conv.esperar_extraccion(timeout=30)
        if respuesta.cierra:
            break
    return evaluar(conv.estado.cuadro).semaforo.value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capa", default="capa2_ruidosa")
    parser.add_argument("--casos", type=int, default=None)
    parser.add_argument(
        "--con-modelo",
        action="store_true",
        help="usa el 3B local (lento: ~17 s por turno). Por defecto mide solo el código.",
    )
    args = parser.parse_args()

    if not KIT.exists():
        raise SystemExit(f"No existe el dataset en {KIT}")

    casos = cargar_casos(args.capa, args.casos)
    cliente = ClienteLocal() if args.con_modelo else ModeloApagado()
    print(f"Casos: {len(casos)} · capa: {args.capa} · "
          f"modelo: {'llama3.2:3b' if args.con_modelo else 'APAGADO (solo código)'}\n")

    inicio = time.perf_counter()
    matriz: Counter = Counter()
    fallos_rojo: list[str] = []

    for i, caso in enumerate(casos, 1):
        predicho = evaluar_caso(caso, cliente)
        real = caso["etiqueta"]
        matriz[(real, predicho)] += 1
        if real == "rojo" and predicho != "rojo":
            fallos_rojo.append(f"{caso['caso_id']} -> {predicho}")
        if i % 20 == 0 or i == len(casos):
            print(f"  {i}/{len(casos)}...")

    etiquetas = ["verde", "amarillo", "rojo"]
    print("\nMATRIZ DE CONFUSIÓN (fila = real, columna = predicho)")
    print(f"{'':>10} " + " ".join(f"{e:>9}" for e in etiquetas))
    for real in etiquetas:
        fila = " ".join(f"{matriz[(real, p)]:>9}" for p in etiquetas)
        print(f"{real:>10} {fila}")

    rojos = sum(matriz[("rojo", p)] for p in etiquetas)
    aciertos_rojo = matriz[("rojo", "rojo")]
    amarillos = sum(matriz[("amarillo", p)] for p in etiquetas)
    amarillos_a_verde = matriz[("amarillo", "verde")]
    verdes = sum(matriz[("verde", p)] for p in etiquetas)
    verdes_escalados = verdes - matriz[("verde", "verde")]

    print(f"\nRecall en ROJO:            {aciertos_rojo}/{rojos}"
          f"  {'✅' if aciertos_rojo == rojos else '❌ FALSOS NEGATIVOS'}")
    if fallos_rojo:
        for f in fallos_rojo:
            print(f"   ! {f}")
    print(f"Amarillos caídos a verde:  {amarillos_a_verde}/{amarillos}")
    print(f"Verdes sobre-escalados:    {verdes_escalados}/{verdes}")
    print(f"\nTiempo: {time.perf_counter() - inicio:.1f}s")
    return 0 if aciertos_rojo == rojos else 1


if __name__ == "__main__":
    sys.exit(main())
