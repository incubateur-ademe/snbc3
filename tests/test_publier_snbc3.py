"""
Classeur SNBC3 publié (data/input/SNBC3 retraitée.xlsx, scripts/publier_snbc3.py) :
valeurs seules, onglets attendus, et à jour du classeur de travail quand
celui-ci est présent (poste local ; absent d'un checkout propre, donc en CI).
"""

import pytest


def _valeurs(chemin):
    import openpyxl

    wb = openpyxl.load_workbook(chemin, read_only=True, data_only=True)
    out = {
        ws.title: [list(r) for r in ws.iter_rows(values_only=True)]
        for ws in wb.worksheets if ws.title != "Lisez-moi"
    }
    wb.close()
    return out


def test_classeur_publie_sans_donnees_sources():
    """Ni formule, ni onglet brut DGEC (GES, bilans, détail du retraitement)."""
    import openpyxl

    from config.settings import SNBC3_RETRAITEE_PATH
    from pipeline.territorialize import load_zone_config
    from scripts.publier_snbc3 import ONGLETS_RETRAITES

    wb = openpyxl.load_workbook(SNBC3_RETRAITEE_PATH)
    onglet_parts = load_zone_config("DROM")["trajectoire_derivee"]["onglet_parts"]
    assert wb.sheetnames == ["Lisez-moi", *ONGLETS_RETRAITES, onglet_parts]
    formules = [f"{ws.title}!{c.coordinate}" for ws in wb for row in ws.iter_rows()
                for c in row if c.data_type == "f"]
    assert not formules
    assert not wb.defined_names


@pytest.mark.donnees
def test_classeur_publie_a_jour(tmp_path):
    """
    Le classeur versionné reproduit le classeur de travail : un échec signale
    une modification du classeur de travail non republiée
    (`uv run python scripts/publier_snbc3.py`).
    """
    from config.settings import SNBC3_RETRAITEE_PATH, SNBC3_TRAVAIL_PATH
    from scripts.publier_snbc3 import publier

    if not SNBC3_TRAVAIL_PATH.exists():
        pytest.skip(f"Classeur de travail absent : {SNBC3_TRAVAIL_PATH}")
    regenere = tmp_path / "publie.xlsx"
    publier(SNBC3_TRAVAIL_PATH, regenere)
    attendu, versionne = _valeurs(regenere), _valeurs(SNBC3_RETRAITEE_PATH)
    assert versionne.keys() == attendu.keys()
    for onglet, lignes in attendu.items():
        assert len(versionne[onglet]) == len(lignes), onglet
        for a, v in zip(lignes, versionne[onglet]):
            assert v == [x if not isinstance(x, float) else pytest.approx(x, rel=1e-12)
                         for x in a], onglet
