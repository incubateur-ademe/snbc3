"""
tests/test_api.py
==================
Tests de l'API FastAPI (api/) avec un PivotStore de test (petit df_long
synthétique injecté via dependency override) - pas de dépendance aux
fichiers pivot réels, et le lifespan (chargement au démarrage) n'est jamais
déclenché (TestClient utilisé hors `with`).

La correctness du calcul pandas (overrides, totaux, territorialize) est déjà
couverte par tests/test_overrides.py et tests/test_territorialize.py : ces
tests-ci portent sur le câblage des routes (codes HTTP, auth, forme des
réponses), pas sur la logique de calcul elle-même.

Lancer avec : uv run python -m pytest tests/test_api.py -v
"""

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import api.routers.territorialisation as territorialisation_router
from api.config import settings
from api.dependencies import get_pivot_store
from api.main import app
from api.pivot_store import PivotStore
from pipeline.territorialize import TERRITORIALISE_COLUMNS

CODE_EPCI = "TESTEPCI1"


def _fake_store() -> PivotStore:
    df_long = pd.DataFrame([
        {"code_epci": CODE_EPCI, "nom_epci": "Territoire Test", "niveau_geo": "EPCI",
         "zone": "HEXAGONE", "code_region": "84", "code_departement": "74",
         "code_indicateur": "POP", "annee": 2021, "valeur": 100.0, "unite": "habitants",
         "source": "TEST", "date_extraction": "2026-01-01", "qualite": "ok"},
        {"code_epci": "HEXAGONE", "nom_epci": "France hexagonale", "niveau_geo": "TOTAL",
         "zone": None, "code_region": None, "code_departement": None,
         "code_indicateur": "POP", "annee": 2021, "valeur": 100.0, "unite": "habitants",
         "source": "TEST", "date_extraction": "2026-01-01", "qualite": "ok"},
    ])
    store = PivotStore(pivot_dir=Path("unused"))
    store.df_long = df_long
    store.manifest = {
        "annee_ref": 2021, "date_generation": "2026-01-01T00:00:00+00:00",
        "n_lignes": len(df_long), "n_territoires": 1,
    }
    store._default_results["HEXAGONE"] = pd.DataFrame([{
        "code_epci": CODE_EPCI, "nom_epci": "Territoire Test", "niveau_geo": "EPCI", "zone": "HEXAGONE",
        "code_region": "84", "code_departement": "74", "code_traj_supra": "TEST_SECTEUR",
        "type": "conso", "secteur": "test", "usage": "test", "code_pivot": "POP",
        "unite": "TWh", "annee": 2021, "valeur_brute": 1.0, "valeur_recalee": 1.0,
        "code_recalage": None, "recal_factor": 1.0, "ecart_conservation": 0.0,
        "qualite": "ok",
    }])[TERRITORIALISE_COLUMNS]
    return store


@pytest.fixture
def client():
    app.dependency_overrides[get_pivot_store] = _fake_store
    settings.api_keys = ""  # auth désactivée par défaut pour ces tests
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestMeta:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_version(self, client):
        r = client.get("/version")
        assert r.status_code == 200
        body = r.json()
        assert body["pivot"]["annee_ref"] == 2021


class TestPivots:
    def test_pivots_territoire_connu(self, client):
        r = client.get(f"/territoires/{CODE_EPCI}/pivots")
        assert r.status_code == 200
        body = r.json()
        assert body["code_epci"] == CODE_EPCI
        assert len(body["pivots"]) == 1
        assert body["pivots"][0]["code_indicateur"] == "POP"

    def test_pivots_territoire_inconnu(self, client):
        r = client.get("/territoires/INCONNU/pivots")
        assert r.status_code == 404


class TestTerritorialisationSansOverride:
    def test_retourne_le_resultat_par_defaut(self, client):
        r = client.post(f"/territorialisation/{CODE_EPCI}", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["code_epci"] == CODE_EPCI
        assert len(body["rows"]) == 1
        assert body["rows"][0]["valeur_recalee"] == pytest.approx(1.0)

    def test_territoire_inconnu_404(self, client):
        r = client.post("/territorialisation/INCONNU", json={})
        assert r.status_code == 404


class TestTerritorialisationAvecOverride:
    def test_override_declenche_un_recalcul(self, client, monkeypatch):
        """
        Ne teste pas la justesse du calcul pandas (couvert par
        test_overrides.py) : vérifie que le routeur (a) applique bien
        l'override à df_long avant d'appeler territorialize(), et (b)
        renvoie le résultat produit par territorialize() plutôt que le
        résultat par défaut en cache.
        """
        captured = {}

        def fake_territorialize(df_long, zone, ref_year, **kwargs):
            captured["valeur_pop"] = df_long.loc[
                (df_long["code_epci"] == CODE_EPCI) & (df_long["code_indicateur"] == "POP"),
                "valeur",
            ].iloc[0]
            return pd.DataFrame([{
                "code_epci": CODE_EPCI, "nom_epci": "Territoire Test", "niveau_geo": "EPCI", "zone": zone,
                "code_region": "84", "code_departement": "74", "code_traj_supra": "TEST_SECTEUR",
                "type": "conso", "secteur": "test", "usage": "test", "code_pivot": "POP",
                "unite": "TWh", "annee": ref_year, "valeur_brute": 42.0, "valeur_recalee": 42.0,
                "code_recalage": None, "recal_factor": 1.0, "ecart_conservation": 0.0,
                "qualite": "ok",
            }])[TERRITORIALISE_COLUMNS]

        monkeypatch.setattr(territorialisation_router, "territorialize", fake_territorialize)

        r = client.post(
            f"/territorialisation/{CODE_EPCI}",
            json={"overrides": [{"code_indicateur": "POP", "annee": 2021, "valeur": 999.0}]},
        )
        assert r.status_code == 200
        assert captured["valeur_pop"] == pytest.approx(999.0)
        assert r.json()["rows"][0]["valeur_recalee"] == pytest.approx(42.0)

    def test_override_indicateur_inconnu_422(self, client):
        r = client.post(
            f"/territorialisation/{CODE_EPCI}",
            json={"overrides": [{"code_indicateur": "CODE_INEXISTANT", "annee": 2021, "valeur": 1.0}]},
        )
        assert r.status_code == 422

    def test_dju_variant_inconnue_422(self, client):
        """Une variante inconnue désactivait la correction DJU sans erreur (facteur 1)."""
        r = client.post(f"/territorialisation/{CODE_EPCI}", json={"dju_variant": "costic\nFAUX"})
        assert r.status_code == 422


class TestAuth:
    def test_cle_api_manquante_401(self, client):
        settings.api_keys = "secret123"
        try:
            r = client.get(f"/territoires/{CODE_EPCI}/pivots")
            assert r.status_code == 401
        finally:
            settings.api_keys = ""

    def test_cle_api_valide_200(self, client):
        settings.api_keys = "secret123"
        try:
            r = client.get(f"/territoires/{CODE_EPCI}/pivots", headers={"X-API-Key": "secret123"})
            assert r.status_code == 200
        finally:
            settings.api_keys = ""


class TestClasseurMaitreOpenpyxl:
    """
    L'endpoint `/excel` réécrit le classeur maître avec openpyxl, qui perd au
    chargement les formes, zones de texte et commentaires à thread.
    `save_master` recopie les formes ; les commentaires à thread ne sont pas
    pris en charge (notes classiques seulement).
    """

    @pytest.fixture(scope="class")
    @classmethod
    def parts(cls):
        import zipfile

        from config.settings import TRAJ_GES_TEMPLATE
        with zipfile.ZipFile(TRAJ_GES_TEMPLATE) as z:
            return {n: z.read(n) for n in z.namelist()}

    def test_template_sans_commentaire_a_thread(self, parts):
        assert not [n for n in parts if n.startswith("xl/threadedComments/")]

    def test_reecriture_conserve_formes_images_et_texte_enrichi(self):
        import io
        import re
        import zipfile

        from openpyxl.cell.rich_text import CellRichText

        from api.excel_export import load_master, save_master
        from config.settings import TRAJ_GES_TEMPLATE

        def compte(wb):
            return sum(isinstance(c.value, CellRichText)
                       for ws in wb for row in ws.iter_rows() for c in row)

        def formes(z):
            # Images : <xdr:pic> ; formes, connecteurs, groupes : perdus par openpyxl
            return sorted(len(re.findall(rb"<xdr:(?:sp|cxnSp|grpSp)[ >]", z.read(n)))
                          for n in z.namelist() if re.fullmatch(r"xl/drawings/drawing\d+\.xml", n))

        wb = load_master(TRAJ_GES_TEMPLATE)
        n_riches = compte(wb)
        dst = io.BytesIO(save_master(wb, TRAJ_GES_TEMPLATE))
        with zipfile.ZipFile(TRAJ_GES_TEMPLATE) as a, zipfile.ZipFile(dst) as b:
            images = [n for n in a.namelist() if n.startswith("xl/media/")]
            assert images and any(n.startswith("xl/media/") for n in b.namelist())
            assert [f for f in formes(a) if f] and formes(a) == formes(b)
        assert n_riches and compte(load_master(dst)) == n_riches
