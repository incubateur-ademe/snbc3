"""Parc de véhicules SDES : agrégation par commune et catégorie (sources/parc_vehicules.py)."""

import pandas as pd
import pytest

from sources.parc_vehicules import _agreger


def _csv(lignes: list[tuple[str, str, int]], col_commune="COMMUNE_CODE", col_classe="GROUPE"):
    return pd.DataFrame(
        [{col_commune: c, col_classe: g, "PARC_2021": str(v)} for c, g, v in lignes]
    )


def test_edition_2026_corse_et_codes_fictifs():
    df = _csv([
        ("01001", "VP", 10), ("01001", "VP", 5), ("01001", "VUL", 2),
        ("2A004", "PL", 3), ("2B033", "TCP", 1),
        ("00000", "VP", 100), ("01000", "VP", 100), ("2A000", "VP", 100),
        ("01001", "AUTRE", 50),
    ])
    res = _agreger(df, 2021)
    assert sorted(res.index) == ["01001", "2A004", "2B033"]
    assert res.loc["01001"].to_dict() == {"PARC_VP": 15.0, "PARC_VUL": 2.0, "PARC_PL": 0.0, "PARC_BUS": 0.0}
    assert res.loc["2A004", "PARC_PL"] == 3.0
    assert res.loc["2B033", "PARC_BUS"] == 1.0


def test_edition_precedente():
    df = _csv([("01001", "vp", 4), ("XXXXX", "vp", 9), ("01001", "autcar", 1)],
              col_classe="CLASSE_VEHICULE")
    res = _agreger(df, 2021)
    assert list(res.index) == ["01001"]
    assert res.loc["01001", "PARC_VP"] == 4.0
    assert res.loc["01001", "PARC_BUS"] == 1.0


def test_annee_absente():
    with pytest.raises(ValueError, match="PARC_2030"):
        _agreger(_csv([("01001", "VP", 1)]), 2030)


@pytest.mark.donnees
def test_source_en_echec_arrete_la_bdd(monkeypatch):
    # Cas réel (novembre 2026) : jeu data.gouv retiré, 404. Avant, l'échec était
    # seulement journalisé et la BDD produite sans parc ni pivots routiers.
    import sources.parc_vehicules as pv
    from pipeline.aggregate import SourceEnEchec, build_bdd

    def _404(annee):
        raise RuntimeError("404 Client Error: NOT FOUND")

    monkeypatch.setattr(pv, "extract_indicators", _404)
    with pytest.raises(SourceEnEchec, match="Parc véhicules 2021"):
        build_bdd(annee=2021, sources=["parc_vehicules"])
