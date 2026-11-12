"""
tests/test_dju.py
=================
Tests unitaires des fonctions de calcul DJU (sources/dju.py).
Fonctions pures sans I/O - pas de dépendance réseau.

Lancer avec : uv run python -m pytest tests/test_dju.py -v
"""

import numpy as np
import pandas as pd
import pytest

from sources.dju import (
    dju_meteo,
    dju_costic,
    compute_station_dju,
    haversine_km,
    idw_weights,
)


# ---------------------------------------------------------------------------
# dju_meteo
# ---------------------------------------------------------------------------

class TestDjuMeteo:
    def test_plein_hiver(self):
        """Tmax < T_ref → DJU_chaud = T_ref - Tmoy, DJU_froid = 0."""
        chaud, froid = dju_meteo(np.array([0.0]), np.array([5.0]), 18.0)
        assert chaud[0] == pytest.approx(18.0 - 2.5)
        assert froid[0] == 0.0

    def test_plein_ete(self):
        """Tmin > T_ref → DJU_froid = Tmoy - T_ref, DJU_chaud = 0."""
        chaud, froid = dju_meteo(np.array([22.0]), np.array([30.0]), 18.0)
        assert chaud[0] == 0.0
        assert froid[0] == pytest.approx(26.0 - 18.0)

    def test_isotherme(self):
        """Tmoy = T_ref → DJU = 0 dans les deux cas."""
        chaud, froid = dju_meteo(np.array([16.0]), np.array([20.0]), 18.0)
        assert chaud[0] == 0.0
        assert froid[0] == 0.0

    def test_vectoriel(self):
        """Calcul vectoriel sur plusieurs jours."""
        tmin = np.array([0.0, 10.0, 22.0])
        tmax = np.array([5.0, 16.0, 30.0])
        chaud, froid = dju_meteo(tmin, tmax, 18.0)
        assert len(chaud) == 3
        assert all(chaud >= 0)
        assert all(froid >= 0)


# ---------------------------------------------------------------------------
# dju_costic
# ---------------------------------------------------------------------------

class TestDjuCostic:
    def test_plein_hiver(self):
        """Tmax <= T_ref → DJU_chaud = T_ref - Tmoy."""
        chaud, froid = dju_costic(np.array([0.0]), np.array([10.0]), 18.0)
        assert chaud[0] == pytest.approx(18.0 - 5.0, abs=1e-6)
        assert froid[0] == 0.0

    def test_plein_ete(self):
        """Tmin >= T_ref → DJU_chaud = 0, DJU_froid > 0."""
        chaud, froid = dju_costic(np.array([20.0]), np.array([30.0]), 18.0)
        assert chaud[0] == 0.0
        assert froid[0] > 0.0

    def test_mi_saison_symetrie(self):
        """
        Cas mi-saison symétrique autour de T_ref :
        Tmin = T_ref - d, Tmax = T_ref + d → DJU_chaud ≈ DJU_froid.
        """
        d    = 5.0
        tref = 18.0
        tmin = np.array([tref - d])
        tmax = np.array([tref + d])
        chaud, froid = dju_costic(tmin, tmax, tref)
        assert chaud[0] == pytest.approx(froid[0], rel=1e-6)

    def test_tmin_egal_tmax(self):
        """Tmin = Tmax → pas de division par zéro."""
        chaud, froid = dju_costic(np.array([15.0]), np.array([15.0]), 18.0)
        assert np.isfinite(chaud[0])
        assert np.isfinite(froid[0])

    def test_non_negatif(self):
        """DJU toujours >= 0."""
        tmin = np.random.uniform(-10, 25, 100)
        tmax = tmin + np.random.uniform(0, 15, 100)
        chaud, froid = dju_costic(tmin, tmax, 18.0)
        assert all(chaud >= 0)
        assert all(froid >= 0)


# ---------------------------------------------------------------------------
# dju_costic vs dju_meteo - cohérence
# ---------------------------------------------------------------------------

class TestCoherenceMethods:
    def test_costic_vs_meteo_mi_saison(self):
        """
        En mi-saison (Tmin < T_ref < Tmax), les deux méthodes divergent.
        Météo moyenne la journée (Tmoy=15 < 18 -> froid=0, chaud=3).
        COSTIC distingue la part au-dessus et en-dessous de T_ref
        (chaud=3.8, froid=0.8). Pas de relation d'ordre universelle.
        """
        tmin = np.array([8.0])
        tmax = np.array([22.0])
        chaud_m, froid_m = dju_meteo(tmin, tmax, 18.0)
        chaud_c, froid_c = dju_costic(tmin, tmax, 18.0)
        # Les deux détectent un besoin de chauffage
        assert chaud_m[0] > 0 and chaud_c[0] > 0
        # COSTIC détecte aussi un besoin de froid (Tmax > T_ref)
        # alors que Météo ne le voit pas (Tmoy < T_ref)
        assert froid_m[0] == 0.0
        assert froid_c[0] > 0

    def test_plein_hiver_identiques(self):
        """
        En plein hiver (Tmax <= T_ref), les deux méthodes donnent le même résultat.
        """
        tmin = np.array([0.0])
        tmax = np.array([10.0])
        chaud_m, _ = dju_meteo(tmin, tmax, 18.0)
        chaud_c, _ = dju_costic(tmin, tmax, 18.0)
        assert chaud_m[0] == pytest.approx(chaud_c[0], abs=1e-6)


# ---------------------------------------------------------------------------
# compute_station_dju
# ---------------------------------------------------------------------------

class TestComputeStationDju:
    def _make_df(self):
        dates = pd.date_range("2020-01-01", periods=5, freq="D")
        return pd.DataFrame({
            "num_poste": ["P1"] * 5,
            "date":  dates,
            "tmin":  [0.0, 5.0, 15.0, 20.0, 10.0],
            "tmax":  [8.0, 12.0, 22.0, 28.0, 18.0],
        })

    def test_both(self):
        df = compute_station_dju(self._make_df(), methode="both")
        assert "dju_chaud_meteo"  in df.columns
        assert "dju_froid_meteo"  in df.columns
        assert "dju_chaud_costic" in df.columns
        assert "dju_froid_costic" in df.columns

    def test_methode_meteo_only(self):
        df = compute_station_dju(self._make_df(), methode="meteo")
        assert "dju_chaud_meteo" in df.columns
        assert "dju_chaud_costic" not in df.columns

    def test_annee_mois(self):
        df = compute_station_dju(self._make_df())
        assert "annee" in df.columns
        assert "mois"  in df.columns
        assert df["annee"].iloc[0] == 2020
        assert df["mois"].iloc[0]  == 1


# ---------------------------------------------------------------------------
# haversine_km
# ---------------------------------------------------------------------------

class TestHaversine:
    def test_meme_point(self):
        assert haversine_km(48.8, 2.3, 48.8, 2.3) == pytest.approx(0.0, abs=1e-6)

    def test_paris_lyon(self):
        """Paris–Lyon ≈ 392 km."""
        d = haversine_km(48.85, 2.35, 45.75, 4.83)
        assert 380 < d < 410

    def test_symetrie(self):
        assert haversine_km(48.0, 2.0, 45.0, 5.0) == pytest.approx(
            haversine_km(45.0, 5.0, 48.0, 2.0), rel=1e-9
        )


# ---------------------------------------------------------------------------
# idw_weights
# ---------------------------------------------------------------------------

class TestIdwWeights:
    def test_somme_un(self):
        w = idw_weights(np.array([10.0, 20.0, 30.0]))
        assert w.sum() == pytest.approx(1.0)

    def test_distance_zero(self):
        """Distance 0 → poids = 1 sur ce point, 0 sur les autres."""
        w = idw_weights(np.array([0.0, 10.0, 20.0]))
        assert w[0] == pytest.approx(1.0)
        assert w[1] == pytest.approx(0.0)
        assert w[2] == pytest.approx(0.0)

    def test_plus_proche_plus_pondere(self):
        w = idw_weights(np.array([5.0, 10.0, 50.0]))
        assert w[0] > w[1] > w[2]
