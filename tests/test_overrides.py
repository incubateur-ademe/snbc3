"""
tests/test_overrides.py
========================
Tests unitaires de l'application d'overrides utilisateur (pipeline/overrides.py)
sur des DataFrames synthétiques - pas de dépendance aux données réelles.

Utilise des codes indicateur réels (POP, RP_MAISON) car `apply_overrides`
valide contre le catalogue réel (config/indicateurs.yaml).

Lancer avec : uv run python -m pytest tests/test_overrides.py -v
"""

import pandas as pd
import pytest

from pipeline.overrides import OverrideError, apply_overrides


def _df_long() -> pd.DataFrame:
    rows = [
        {"code_epci": "200000172", "nom_epci": "CC Faucigny - Glières", "zone": "HEXAGONE",
         "code_region": "84", "code_departement": "74", "code_indicateur": "POP",
         "annee": 2021, "valeur": 100.0, "unite": "habitants", "source": "INSEE_RP",
         "date_extraction": "2026-01-01", "qualite": "ok"},
        {"code_epci": "200000438", "nom_epci": "CC du Pays de Pontchâteau", "zone": "HEXAGONE",
         "code_region": "52", "code_departement": "44", "code_indicateur": "POP",
         "annee": 2021, "valeur": 50.0, "unite": "habitants", "source": "INSEE_RP",
         "date_extraction": "2026-01-01", "qualite": "ok"},
        {"code_epci": "200000172", "nom_epci": "CC Faucigny - Glières", "zone": "HEXAGONE",
         "code_region": "84", "code_departement": "74", "code_indicateur": "RP_MAISON",
         "annee": 2021, "valeur": 40.0, "unite": "logements", "source": "INSEE_RP",
         "date_extraction": "2026-01-01", "qualite": "ok"},
    ]
    return pd.DataFrame(rows)


class TestApplyOverridesNoOp:
    def test_overrides_vides_retourne_meme_df(self):
        df = _df_long()
        result = apply_overrides(df, "200000172", [], ref_year=2021)
        assert result is df


class TestApplyOverridesValidation:
    def test_indicateur_inconnu_leve(self):
        df = _df_long()
        with pytest.raises(OverrideError, match="inconnu"):
            apply_overrides(
                df, "200000172",
                [{"code_indicateur": "CODE_INEXISTANT", "annee": 2021, "valeur": 1.0}],
                ref_year=2021,
            )

    def test_territoire_absent_leve(self):
        df = _df_long()
        with pytest.raises(OverrideError, match="absent"):
            apply_overrides(
                df, "999999999",
                [{"code_indicateur": "POP", "annee": 2021, "valeur": 1.0}],
                ref_year=2021,
            )


class TestApplyOverridesLigneExistante:
    def test_met_a_jour_la_valeur(self):
        df = _df_long()
        result = apply_overrides(
            df, "200000172",
            [{"code_indicateur": "POP", "annee": 2021, "valeur": 999.0}],
            ref_year=2021,
        )
        row = result[
            (result["code_epci"] == "200000172") & (result["code_indicateur"] == "POP")
        ]
        assert row["valeur"].iloc[0] == pytest.approx(999.0)
        assert row["source"].iloc[0] == "OVERRIDE_TET"
        assert row["qualite"].iloc[0] == "override_tet"

    def test_ne_modifie_pas_le_df_original(self):
        df = _df_long()
        apply_overrides(
            df, "200000172",
            [{"code_indicateur": "POP", "annee": 2021, "valeur": 999.0}],
            ref_year=2021,
        )
        original = df[(df["code_epci"] == "200000172") & (df["code_indicateur"] == "POP")]
        assert original["valeur"].iloc[0] == pytest.approx(100.0)


class TestApplyOverridesNouvelleLigne:
    def test_ajoute_une_ligne_pour_indicateur_absent(self):
        df = _df_long()
        result = apply_overrides(
            df, "200000438",
            [{"code_indicateur": "RP_MAISON", "annee": 2021, "valeur": 15.0}],
            ref_year=2021,
        )
        row = result[
            (result["code_epci"] == "200000438") & (result["code_indicateur"] == "RP_MAISON")
        ]
        assert len(row) == 1
        assert row["valeur"].iloc[0] == pytest.approx(15.0)
        assert row["nom_epci"].iloc[0] == "CC du Pays de Pontchâteau"
        assert row["zone"].iloc[0] == "HEXAGONE"


class TestApplyOverridesRafraichitTotaux:
    def test_total_hexagone_reflete_override(self):
        df = _df_long()
        result = apply_overrides(
            df, "200000172",
            [{"code_indicateur": "POP", "annee": 2021, "valeur": 300.0}],
            ref_year=2021,
        )
        total = result[
            (result["code_epci"] == "HEXAGONE") & (result["code_indicateur"] == "POP")
        ]
        assert len(total) == 1
        # 300 (override) + 50 (200000438, inchangé) = 350
        assert total["valeur"].iloc[0] == pytest.approx(350.0)

    def test_total_indicateur_non_touche_est_conserve_tel_quel(self):
        """
        Optimisation : seuls les indicateurs affectés par l'override (et les
        indicateurs à moyenne pondérée par population si POP est overridé)
        voient leur total recalculé - RP_MAISON, non affecté, doit garder
        exactement son ancien total (y compris ses éventuelles métadonnées),
        pas un total recalculé à partir de zéro.
        """
        df = _df_long()
        ancien_total = pd.DataFrame([{
            "code_epci": "HEXAGONE", "nom_epci": "France hexagonale", "zone": None,
            "code_region": None, "code_departement": None, "code_indicateur": "RP_MAISON",
            "annee": 2021, "valeur": 999999.0, "unite": "logements", "source": "PRECALCULE",
            "date_extraction": "2020-01-01", "qualite": "ok",
        }])
        df = pd.concat([df, ancien_total], ignore_index=True)

        result = apply_overrides(
            df, "200000172",
            [{"code_indicateur": "POP", "annee": 2021, "valeur": 300.0}],
            ref_year=2021,
        )
        total_rp = result[
            (result["code_epci"] == "HEXAGONE") & (result["code_indicateur"] == "RP_MAISON")
        ]
        assert len(total_rp) == 1
        assert total_rp["valeur"].iloc[0] == pytest.approx(999999.0)
        assert total_rp["source"].iloc[0] == "PRECALCULE"
