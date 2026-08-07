"""Verificación del dataset contra lo que declara el README del kit.

No es paranoia: el kit ya demostró tener defectos que su documentación no menciona
(la carpeta breast_cancer contiene literatura de cuello uterino, ver
docs/corpus-hallazgos.md). Cada supuesto sobre el que se construye el harness se
verifica contra el archivo real.

Estas pruebas también sirven de contrato: si el jurado corre el proyecto contra una
versión distinta del dataset, fallan acá y no en producción.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[2] / "techsphere-2026" / "dataset"

pytestmark = pytest.mark.skipif(not KIT.exists(), reason="kit oficial no clonado")


@pytest.fixture(scope="module")
def dialogos():
    import pandas as pd

    return pd.read_excel(KIT / "dataset_final.xlsx", sheet_name="result")


@pytest.fixture(scope="module")
def trayectorias():
    import pandas as pd

    return pd.read_excel(KIT / "trayectorias_postop_silver.xlsx", sheet_name="result")


@pytest.fixture(scope="module")
def perfiles():
    import pandas as pd

    return pd.read_excel(
        KIT / "perfiles_clinicos_pacientes_silver_contest.xlsx", sheet_name="result"
    )


@pytest.fixture(scope="module")
def demografia():
    import pandas as pd

    return pd.read_excel(KIT / "perfiles_pacientes_co.xlsx", sheet_name="result")


class TestFormaDeLosArchivos:
    def test_conversaciones_3991_turnos_13_columnas(self, dialogos):
        assert dialogos.shape == (3991, 13)

    def test_160_casos_40_pacientes(self, dialogos):
        assert dialogos["caso_id"].nunique() == 160
        assert dialogos["paciente_id"].nunique() == 40

    def test_dias_postoperatorios_1_3_7_14(self, dialogos):
        assert sorted(dialogos["dia_postop"].unique()) == [1, 3, 7, 14]

    def test_trayectorias_una_por_caso(self, trayectorias):
        assert len(trayectorias) == 160

    def test_perfiles_uno_por_paciente(self, perfiles, demografia):
        assert len(perfiles) == 40
        assert len(demografia) == 40


class TestEtiquetasDeCriticidad:
    def test_distribucion_123_25_12(self, dialogos):
        por_caso = dialogos.groupby("caso_id")["label_ground_truth"].first()
        conteo = por_caso.value_counts()
        assert conteo["verde"] == 123
        assert conteo["amarillo"] == 25
        assert conteo["rojo"] == 12

    def test_la_etiqueta_es_constante_dentro_del_caso(self, dialogos):
        """El README lo afirma; el harness de evaluación depende de ello."""
        por_caso = dialogos.groupby("caso_id")["label_ground_truth"].nunique()
        assert (por_caso == 1).all()


class TestCapasDeDificultad:
    def test_existen_las_dos_capas(self, dialogos):
        assert set(dialogos["capa"].unique()) == {"capa1_limpia", "capa2_ruidosa"}

    def test_cada_caso_tiene_ambas_capas(self, dialogos):
        por_caso = dialogos.groupby("caso_id")["capa"].nunique()
        assert (por_caso == 2).all()

    def test_los_turnos_de_capa2_derivan_con_sufijo_c2(self, dialogos):
        """Contrato de nombres declarado por el README: los turnos derivados llevan
        el mismo dialogo_id con sufijo _c2, y los insertados por un tercero _c2_tercero.
        """
        capa2 = dialogos[dialogos["capa"] == "capa2_ruidosa"]["dialogo_id"]
        assert capa2.str.endswith(("_c2", "_c2_tercero")).all()

    def test_solo_la_capa_ruidosa_tiene_terceros(self, dialogos):
        terceros = dialogos[dialogos["hablante"] == "tercero"]
        assert (terceros["capa"] == "capa2_ruidosa").all()
        assert terceros["dialogo_id"].str.endswith("_c2_tercero").all()


class TestRelacionEntreArchivos:
    def test_el_join_de_casos_usa_prefijo_caso(self, dialogos, trayectorias):
        """caso_id = "caso_" + trayectoria_id. El join NO es directo."""
        esperados = set("caso_" + trayectorias["trayectoria_id"].astype(str))
        assert set(dialogos["caso_id"].unique()) == esperados

    def test_paciente_id_une_los_cuatro_archivos(
        self, dialogos, trayectorias, perfiles, demografia
    ):
        pacientes = set(perfiles["paciente_id"])
        assert set(demografia["paciente_id"]) == pacientes
        assert set(trayectorias["paciente_id"]) == pacientes
        assert set(dialogos["paciente_id"]) == pacientes

    def test_cada_paciente_tiene_cuatro_trayectorias(self, trayectorias):
        assert (trayectorias.groupby("paciente_id").size() == 4).all()


class TestCamposConTrampa:
    def test_comorbilidades_es_json_dentro_de_texto(self, perfiles):
        """El README avisa: es una lista JSON en una celda de texto, no una lista."""
        for valor in perfiles["comorbilidades"].dropna():
            assert isinstance(json.loads(valor), list)

    def test_adaptation_fields_es_json_dentro_de_texto(self, demografia):
        for valor in demografia["adaptation_fields"].dropna():
            assert isinstance(json.loads(valor), list)


class TestProcedimientos:
    def test_cinco_procedimientos_ocho_pacientes_cada_uno(self, perfiles):
        conteo = perfiles["procedimiento"].value_counts()
        assert len(conteo) == 5
        assert (conteo == 8).all()

    def test_la_mastectomia_esta_entre_los_procedimientos(self, perfiles):
        """Relevante: el corpus NO tiene documentación de mama.
        Ver docs/corpus-hallazgos.md — son 8 pacientes sin sustento posible.
        """
        assert "Mastectomía" in set(perfiles["procedimiento"])
        assert (perfiles["procedimiento"] == "Mastectomía").sum() == 8


class TestCorpusDeTextos:
    def test_hay_107_pdfs_en_cinco_escenarios(self):
        raiz = KIT / "textos"
        pdfs = list(raiz.rglob("*.pdf"))
        assert len(pdfs) == 107
        carpetas = {p.relative_to(raiz).parts[0] for p in pdfs}
        assert len(carpetas) == 5

    def test_dos_carpetas_tienen_espacios_en_el_nombre(self):
        """Declarado por el README. Rompe scripts que no citen las rutas."""
        carpetas = [d.name for d in (KIT / "textos").iterdir() if d.is_dir()]
        assert sum(" " in c for c in carpetas) == 2
