"""Banco de pruebas del motor de escalamiento.

Dos niveles:
  1. Casos unitarios sobre los umbrales y sus bordes.
  2. Banco completo contra los 160 casos etiquetados del kit oficial — la prueba
     que la rúbrica pide de verdad. Se salta si el kit no está clonado.

El criterio bloqueante es recall en rojo, no exactitud global.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.clinico.escalamiento import (
    CuadroClinico,
    Herida,
    Movilidad,
    Semaforo,
    evaluar,
)

KIT = Path(__file__).resolve().parents[2] / "techsphere-2026" / "dataset"


class TestUmbrales:
    def test_fiebre_38_escala_a_rojo(self):
        d = evaluar(CuadroClinico(fiebre_c=38.0, dolor_nrs=2, herida=Herida.NORMAL))
        assert d.semaforo is Semaforo.ROJO
        assert d.escala

    def test_fiebre_justo_debajo_de_38_no_es_rojo(self):
        d = evaluar(CuadroClinico(fiebre_c=37.9, dolor_nrs=2, herida=Herida.NORMAL))
        assert d.semaforo is Semaforo.AMARILLO

    def test_secrecion_purulenta_escala_sola(self):
        """Señal perfecta en el corpus: los 3 casos con secreción son rojo."""
        d = evaluar(
            CuadroClinico(fiebre_c=36.5, dolor_nrs=0, herida=Herida.SECRECION_PURULENTA)
        )
        assert d.semaforo is Semaforo.ROJO

    def test_dolor_8_escala_a_rojo(self):
        d = evaluar(CuadroClinico(dolor_nrs=8, fiebre_c=36.5, herida=Herida.NORMAL))
        assert d.semaforo is Semaforo.ROJO

    def test_sintoma_de_alarma_escala_sin_mas_datos(self):
        d = evaluar(CuadroClinico(sintomas_alarma=["dificultad_respiratoria"]))
        assert d.semaforo is Semaforo.ROJO

    def test_cuadro_limpio_y_completo_es_verde(self):
        d = evaluar(
            CuadroClinico(
                dolor_nrs=1,
                fiebre_c=36.6,
                herida=Herida.NORMAL,
                movilidad=Movilidad.NORMAL,
            )
        )
        assert d.semaforo is Semaforo.VERDE
        assert not d.requiere_indagar

    def test_los_motivos_explican_la_decision(self):
        d = evaluar(CuadroClinico(fiebre_c=39.0, dolor_nrs=9, herida=Herida.NORMAL))
        assert any("fiebre" in m for m in d.motivos)
        assert any("dolor" in m for m in d.motivos)


class TestIndagacion:
    """Verde sin datos no es verde: es ignorancia, y es el falso negativo que penaliza."""

    def test_verde_sin_preguntar_fiebre_exige_indagar(self):
        d = evaluar(CuadroClinico(dolor_nrs=1, herida=Herida.NORMAL))
        assert d.semaforo is Semaforo.VERDE
        assert d.requiere_indagar
        assert "fiebre" in d.cuadro.campos_faltantes

    def test_verde_sin_revisar_herida_exige_indagar(self):
        d = evaluar(CuadroClinico(dolor_nrs=1, fiebre_c=36.5))
        assert d.requiere_indagar
        assert "herida" in d.cuadro.campos_faltantes

    def test_rojo_no_espera_a_tener_todo(self):
        """Ante una señal roja se escala ya, aunque falten datos."""
        d = evaluar(CuadroClinico(fiebre_c=38.5))
        assert d.semaforo is Semaforo.ROJO
        assert not d.requiere_indagar


@pytest.mark.skipif(not KIT.exists(), reason="kit oficial no clonado")
class TestBanco160Casos:
    """La prueba que importa: los 160 casos reales del reto."""

    @pytest.fixture(scope="class")
    def casos(self):
        import pandas as pd

        trayectorias = pd.read_excel(
            KIT / "trayectorias_postop_silver.xlsx", sheet_name="result"
        )
        dialogos = pd.read_excel(KIT / "dataset_final.xlsx", sheet_name="result")
        labels = dialogos.groupby("caso_id")["label_ground_truth"].first().rename("label")
        trayectorias["caso_id"] = "caso_" + trayectorias["trayectoria_id"].astype(str)
        return trayectorias.merge(labels, on="caso_id", how="left")

    @staticmethod
    def _predecir(fila) -> Semaforo:
        return evaluar(
            CuadroClinico(
                dolor_nrs=int(fila.dolor_nrs),
                fiebre_c=float(fila.fiebre_c),
                herida=Herida(fila.herida),
                movilidad=Movilidad(fila.movilidad),
            )
        ).semaforo

    def test_el_banco_tiene_los_160_casos(self, casos):
        assert len(casos) == 160
        assert casos["label"].notna().all()

    def test_recall_perfecto_en_rojo(self, casos):
        """BLOQUEANTE. Un falso negativo en postoperatorio es riesgo clínico."""
        rojos = casos[casos.label == "rojo"]
        fallados = [
            f.caso_id for f in rojos.itertuples() if self._predecir(f) is not Semaforo.ROJO
        ]
        assert not fallados, f"falsos negativos en rojo: {fallados}"

    def test_ningun_rojo_se_clasifica_como_verde(self, casos):
        """El error más grave posible: tranquilizar a quien hay que escalar."""
        rojos = casos[casos.label == "rojo"]
        assert all(self._predecir(f) is not Semaforo.VERDE for f in rojos.itertuples())

    def test_ningun_amarillo_cae_a_verde(self, casos):
        amarillos = casos[casos.label == "amarillo"]
        caidos = [
            f.caso_id
            for f in amarillos.itertuples()
            if self._predecir(f) is Semaforo.VERDE
        ]
        assert not caidos, f"amarillos degradados a verde: {caidos}"

    def test_el_sobre_escalamiento_se_mantiene_acotado(self, casos):
        """Sobre-escalar es aceptable, pero no a cualquier precio: cada uno cuesta
        una llamada de verificación. Si esto se dispara, el agente pierde utilidad."""
        verdes = casos[casos.label == "verde"]
        escalados = sum(
            1 for f in verdes.itertuples() if self._predecir(f) is not Semaforo.VERDE
        )
        assert escalados <= 50, f"sobre-escalamiento excesivo: {escalados}/{len(verdes)}"
