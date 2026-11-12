"""
Référentiel des EPCI (sources/geo_epci.py) : détection des fusions d'EPCI au
1er janvier par comparaison des compositions communales Insee de deux
millésimes, et libellés des EPCI absents de geo.api.gouv.fr.

Sans réseau : les compositions Insee sont remplacées par de petites tables.
"""

import pandas as pd
import pytest

from sources import geo_epci as geo


def _compo(rattachement: dict[str, str]) -> pd.DataFrame:
    """{codgeo: code_epci} -> composition au format de load_composition_epci."""
    return pd.DataFrame({
        "codgeo": list(rattachement), "code_epci": list(rattachement.values()),
        "libelle_insee": "", "code_dep": "12", "code_reg": "76",
    })


@pytest.fixture
def millesimes(monkeypatch):
    """Remplace les compositions Insee : {millésime: {codgeo: code_epci}}, le plus récent en vigueur."""
    def _installer(compos: dict[int, dict[str, str]]):
        recent = max(compos)

        def _load(annee=None, **_):
            annee = annee or recent
            return _compo(compos[annee]), annee

        monkeypatch.setattr(geo, "load_composition_epci", _load)
    return _installer


class TestFusions:
    def test_fusion_dans_un_nouveau_code(self, millesimes):
        millesimes({
            2025: {"c1": "A", "c2": "A", "c3": "B", "c4": "C"},
            2026: {"c1": "N", "c2": "N", "c3": "N", "c4": "C"},
        })
        assert geo.load_fusions_epci() == {"N": ["A", "B"]}

    def test_absorption_le_code_conserve_figure_dans_la_liste(self, millesimes):
        millesimes({
            2025: {"c1": "A", "c2": "B"},
            2026: {"c1": "A", "c2": "A"},
        })
        assert geo.load_fusions_epci() == {"A": ["A", "B"]}

    def test_changement_de_rattachement_sans_fusion_ignore(self, millesimes):
        millesimes({
            2025: {"c1": "A", "c2": "A", "c3": "B"},
            2026: {"c1": "A", "c2": "B", "c3": "B"},
        })
        assert geo.load_fusions_epci() == {}

    def test_scission_non_recomposable(self, millesimes, caplog):
        millesimes({
            2025: {"c1": "A", "c2": "A", "c3": "B", "c4": "C"},
            2026: {"c1": "B", "c2": "C", "c3": "B", "c4": "C"},
        })
        assert geo.load_fusions_epci() == {}
        assert "non recomposable" in caplog.text


class TestLibelles:
    @pytest.mark.parametrize("insee, attendu", [
        ("Communauté de communes du Lévézou", "CC du Lévézou"),
        ("Communauté d'agglomération du Grand Annecy", "CA du Grand Annecy"),
        ("Communauté urbaine de Dunkerque", "CU de Dunkerque"),
        ("Thionville Fensch Agglomération", "Thionville Fensch Agglomération"),
    ])
    def test_abreviation(self, insee, attendu):
        assert geo._abreger_libelle_epci(insee) == attendu

    def test_nom_geo_api_prioritaire(self):
        compo = _compo({"c1": "A", "c2": "N"}).assign(
            libelle_insee=["Communauté de communes A", "Communauté de communes N"]
        )
        noms = geo._noms_epci(compo, {"A": {"nom": "CC A (geo)", "lat": 0, "lon": 0}})
        assert noms == {"A": "CC A (geo)", "N": "CC N"}
