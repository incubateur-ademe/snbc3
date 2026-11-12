"""
tests/test_territoire_resolver.py
==================================
Tests unitaires de la résolution de territoire (pipeline/territoire_resolver.py)
sur des DataFrames synthétiques, avec les correspondances commune->EPT et
commune->EPCI mockées (monkeypatch) pour ne pas dépendre des sources réelles.

Lancer avec : uv run python -m pytest tests/test_territoire_resolver.py -v
"""

import pandas as pd
import pytest

from pipeline.territoire_resolver import (
    METHODE_COMMUNE_VERS_EPCI, METHODE_COMMUNE_VERS_EPT, METHODE_DIRECT,
    METHODE_NON_RESOLU, resolve_territoire,
)


def _df_long() -> pd.DataFrame:
    rows = [
        {"code_epci": "200000172", "zone": "HEXAGONE", "niveau_geo": "EPCI"},
        {"code_epci": "T8", "zone": "HEXAGONE", "niveau_geo": "EPT"},
        {"code_epci": "75056", "zone": "HEXAGONE", "niveau_geo": "COMMUNE"},
        {"code_epci": "HEXAGONE", "zone": None, "niveau_geo": "TOTAL"},
        {"code_epci": "FRANCE", "zone": None, "niveau_geo": "TOTAL"},
    ]
    return pd.DataFrame(rows)


class TestResolutionDirecte:
    def test_code_epci_direct(self):
        r = resolve_territoire("200000172", _df_long())
        assert r["resolu"] is True
        assert r["code_epci"] == "200000172"
        assert r["niveau_geo"] == "EPCI"
        assert r["methode_resolution"] == METHODE_DIRECT

    def test_code_ept_direct(self):
        """Un EPT est déjà une valeur de code_epci : résolution directe, pas
        de désagrégation particulière (cf. contexte : EPT traité comme EPCI)."""
        r = resolve_territoire("T8", _df_long())
        assert r["resolu"] is True
        assert r["code_epci"] == "T8"
        assert r["niveau_geo"] == "EPT"
        assert r["methode_resolution"] == METHODE_DIRECT

    def test_paris_direct(self):
        r = resolve_territoire("75056", _df_long())
        assert r["resolu"] is True
        assert r["code_epci"] == "75056"
        assert r["niveau_geo"] == "COMMUNE"

    def test_lignes_total_non_eligibles_a_la_resolution_directe(self):
        """HEXAGONE/FRANCE ne sont pas des territoires demandables."""
        r = resolve_territoire("HEXAGONE", _df_long())
        assert r["methode_resolution"] != METHODE_DIRECT


class TestResolutionParCommune:
    def test_commune_mgp_resolue_vers_ept(self, monkeypatch):
        import sources.geo_epci as geo_epci

        monkeypatch.setattr(
            geo_epci, "load_commune_ept_cog",
            lambda: pd.DataFrame([{"codgeo": "93001", "code_ept": "T8"}]),
        )
        monkeypatch.setattr(
            geo_epci, "load_commune_epci_cog",
            lambda: pd.DataFrame([{"codgeo": "93001", "code_epci": "200054781"}]),
        )
        r = resolve_territoire("93001", _df_long())
        assert r["resolu"] is True
        assert r["code_epci"] == "T8"
        assert r["methode_resolution"] == METHODE_COMMUNE_VERS_EPT

    def test_commune_hors_mgp_resolue_vers_epci_parent(self, monkeypatch):
        import sources.geo_epci as geo_epci

        monkeypatch.setattr(
            geo_epci, "load_commune_ept_cog",
            lambda: pd.DataFrame(columns=["codgeo", "code_ept"]),
        )
        monkeypatch.setattr(
            geo_epci, "load_commune_epci_cog",
            lambda: pd.DataFrame([{"codgeo": "74001", "code_epci": "200000172"}]),
        )
        r = resolve_territoire("74001", _df_long())
        assert r["resolu"] is True
        assert r["code_epci"] == "200000172"
        assert r["methode_resolution"] == METHODE_COMMUNE_VERS_EPCI

    def test_commune_inconnue_non_resolue(self, monkeypatch):
        import sources.geo_epci as geo_epci

        monkeypatch.setattr(
            geo_epci, "load_commune_ept_cog",
            lambda: pd.DataFrame(columns=["codgeo", "code_ept"]),
        )
        monkeypatch.setattr(
            geo_epci, "load_commune_epci_cog",
            lambda: pd.DataFrame(columns=["codgeo", "code_epci"]),
        )
        r = resolve_territoire("00000", _df_long())
        assert r["resolu"] is False
        assert r["code_epci"] is None
        assert r["methode_resolution"] == METHODE_NON_RESOLU
