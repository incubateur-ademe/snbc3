"""
sources/dju.py
==============
Fonctions de calcul DJU (Degrés-Jours Unifiés) - calcul pur, sans I/O.

Méthodes :
  - Météo (simplifiée)  : DJU = max(0, T_ref - Tmoy)
  - COSTIC (pro énergie): 3 cas selon position de T_ref vs Tmin/Tmax

Interpolation spatiale :
  - IDW (Inverse Distance Weighting) avec exposant p=2
  - Appliquée au centroïde géographique de chaque EPCI
  - N stations les plus proches dans un rayon max configurable
"""

import logging

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

DJU_SEUIL_CHAUD_DEFAULT: float = 17.0   # seuil chauffage (SDES mars 2025)
DJU_SEUIL_FROID_DEFAULT: float = 21.0   # seuil climatisation (SDES mars 2025)

IDW_POWER:      int   = 2
IDW_N_STATIONS: int   = 5
IDW_RADIUS_KM:  float = 150.0

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Méthodes de calcul DJU journalier
# ---------------------------------------------------------------------------

def dju_meteo(tmin: np.ndarray, tmax: np.ndarray, t_ref: float) -> tuple[np.ndarray, np.ndarray]:
    """
    Méthode Météo (simplifiée) :
      DJU_chaud = max(0, T_ref - Tmoy)   avec Tmoy = (Tmin+Tmax)/2
      DJU_froid = max(0, Tmoy - T_ref)

    Retourne (dju_chaud, dju_froid) par jour.
    """
    tmoy = (tmin + tmax) / 2.0
    chaud = np.maximum(0.0, t_ref - tmoy)
    froid = np.maximum(0.0, tmoy - t_ref)
    return chaud, froid


def dju_costic(tmin: np.ndarray, tmax: np.ndarray, t_ref: float) -> tuple[np.ndarray, np.ndarray]:
    """
    Méthode COSTIC (Professionnels de l'énergie) - 3 cas :
      Cas 1 (plein hiver)  : Tmax <= T_ref  → DJU_chaud = T_ref - Tmoy
      Cas 2 (plein été)    : Tmin >= T_ref  → DJU_chaud = 0
      Cas 3 (mi-saison)    : Tmin < T_ref < Tmax
                             b = (T_ref - Tmin) / (Tmax - Tmin)
                             DJU_chaud = (Tmax-Tmin) * b * (0.08 + 0.42*b)

    Symétrique pour DJU_froid.
    Retourne (dju_chaud, dju_froid) par jour.
    """
    chaud = np.zeros(len(tmin))
    froid = np.zeros(len(tmin))
    delta = tmax - tmin
    delta_safe = np.where(delta == 0, 1e-9, delta)

    # DJU chaud (chauffage)
    mask1 = tmax <= t_ref
    chaud[mask1] = t_ref - (tmin[mask1] + tmax[mask1]) / 2.0

    mask3 = (tmin < t_ref) & (tmax > t_ref)
    b = (t_ref - tmin[mask3]) / delta_safe[mask3]
    chaud[mask3] = delta[mask3] * b * (0.08 + 0.42 * b)

    # DJU froid (climatisation) - symétrique
    maskA = tmin >= t_ref
    froid[maskA] = (tmin[maskA] + tmax[maskA]) / 2.0 - t_ref

    maskC = (tmin < t_ref) & (tmax > t_ref)
    b2 = (tmax[maskC] - t_ref) / delta_safe[maskC]
    froid[maskC] = delta[maskC] * b2 * (0.08 + 0.42 * b2)

    return np.maximum(0.0, chaud), np.maximum(0.0, froid)


def compute_station_dju(
    df_temps: pd.DataFrame,
    t_ref_chaud: float = DJU_SEUIL_CHAUD_DEFAULT,
    t_ref_froid: float = DJU_SEUIL_FROID_DEFAULT,
    methode: str = "both",
) -> pd.DataFrame:
    """
    Calcule les DJU journaliers pour chaque station.

    Paramètres :
      df_temps    : DataFrame avec colonnes num_poste, date, tmin, tmax
      t_ref_chaud : seuil chauffage (°C)
      t_ref_froid : seuil climatisation (°C)
      methode     : 'meteo', 'costic', ou 'both'

    Retourne un DataFrame avec les DJU journaliers par station.
    """
    tmin = df_temps["tmin"].values
    tmax = df_temps["tmax"].values

    result = df_temps[["num_poste", "date"]].copy()

    if methode in ("meteo", "both"):
        c_m, f_m = dju_meteo(tmin, tmax, t_ref_chaud)
        result["dju_chaud_meteo"] = c_m
        result["dju_froid_meteo"] = dju_meteo(tmin, tmax, t_ref_froid)[1] if t_ref_froid != t_ref_chaud else f_m

    if methode in ("costic", "both"):
        c_c, f_c = dju_costic(tmin, tmax, t_ref_chaud)
        result["dju_chaud_costic"] = c_c
        result["dju_froid_costic"] = dju_costic(tmin, tmax, t_ref_froid)[1] if t_ref_froid != t_ref_chaud else f_c

    result["annee"] = df_temps["date"].dt.year
    result["mois"]  = df_temps["date"].dt.month

    return result


# ---------------------------------------------------------------------------
# Interpolation IDW station → EPCI
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance haversine en km entre deux points (degrés décimaux)."""
    R = 6371.0
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = (np.sin(dlat / 2) ** 2
         + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon / 2) ** 2)
    return 2 * R * np.arcsin(np.sqrt(a))


def idw_weights(distances_km: np.ndarray, power: float = 2.0) -> np.ndarray:
    """
    Calcule les poids IDW normalisés.
    Si un point est à distance 0, il reçoit un poids de 1 (valeur exacte).
    """
    if np.any(distances_km == 0):
        w = np.where(distances_km == 0, 1.0, 0.0)
    else:
        w = 1.0 / (distances_km ** power)
    return w / w.sum()


def build_station_tree(postes: pd.DataFrame) -> tuple[cKDTree, pd.DataFrame]:
    """
    Construit un kd-tree sur les coordonnées des stations (en radians).
    Retourne (tree, postes_valides).
    """
    postes_ok = postes.dropna(subset=["lat", "lon"]).copy()
    coords_rad = np.radians(postes_ok[["lat", "lon"]].values)
    tree = cKDTree(coords_rad)
    return tree, postes_ok


def interpolate_dju_to_epci(
    df_dju_station: pd.DataFrame,
    epci: pd.DataFrame,
    postes: pd.DataFrame,
    methode: str = "both",
    n_stations: int = IDW_N_STATIONS,
    radius_km: float = IDW_RADIUS_KM,
    power: float = IDW_POWER,
) -> pd.DataFrame:
    """
    Interpole les DJU annuels de chaque station vers chaque centroïde EPCI par IDW.

    Paramètres :
      df_dju_station : DJU journaliers par station (num_poste, annee, mois, dju_*)
      epci           : DataFrame des EPCI (code_epci, nom_epci, lat, lon)
      postes         : DataFrame des postes (num_poste, lat, lon)

    Retourne un DataFrame avec les DJU annuels par EPCI.
    Colonnes : code_epci, nom_epci, annee, dju_chaud_*, dju_froid_*,
               n_stations, distance_max_km
    """
    dju_cols = [c for c in df_dju_station.columns if c.startswith("dju_")]

    # Agréger par station/annee (somme des DJU journaliers → DJU annuel)
    agg_an = df_dju_station.groupby(["num_poste", "annee"])[dju_cols].sum().reset_index()

    tree, postes_ok = build_station_tree(postes)
    poste_ids = postes_ok["num_poste"].values
    results = []

    for _, epci_row in epci.iterrows():
        code_epci = epci_row["code_epci"]
        nom_epci  = epci_row["nom_epci"]
        lat_e     = epci_row["lat"]
        lon_e     = epci_row["lon"]

        if pd.isna(lat_e) or pd.isna(lon_e):
            log.warning(f"EPCI {code_epci} sans coordonnées, ignoré")
            continue

        query_rad    = np.radians([lat_e, lon_e])
        max_dist_rad = radius_km / 6371.0

        dists_rad, idxs = tree.query(
            query_rad,
            k=min(n_stations * 3, len(poste_ids)),
            distance_upper_bound=max_dist_rad * 1.5,
        )

        valid_mask = dists_rad < np.inf
        if not np.any(valid_mask):
            log.warning(f"EPCI {code_epci}: aucune station dans le rayon {radius_km} km")
            continue

        candidate_ids  = poste_ids[idxs[valid_mask]]
        cand_lats      = postes_ok.iloc[idxs[valid_mask]]["lat"].values
        cand_lons      = postes_ok.iloc[idxs[valid_mask]]["lon"].values

        dist_km = np.array([
            haversine_km(lat_e, lon_e, la, lo)
            for la, lo in zip(cand_lats, cand_lons)
        ])

        in_radius = dist_km <= radius_km
        candidate_ids = candidate_ids[in_radius]
        dist_km       = dist_km[in_radius]

        if len(candidate_ids) == 0:
            log.warning(f"EPCI {code_epci}: aucune station dans le rayon {radius_km} km (filtre exact)")
            continue

        order      = np.argsort(dist_km)[:n_stations]
        sel_postes = candidate_ids[order]
        sel_dists  = dist_km[order]
        dist_max   = sel_dists.max()

        for annee, grp in agg_an[agg_an["num_poste"].isin(sel_postes)].groupby("annee"):
            present_postes = grp["num_poste"].values
            present_mask   = np.isin(sel_postes, present_postes)
            if not np.any(present_mask):
                continue

            w = idw_weights(sel_dists[present_mask], power=power)

            row_result = {
                "code_epci":       code_epci,
                "nom_epci":        nom_epci,
                "annee":           int(annee),
                "n_stations":      int(present_mask.sum()),
                "distance_max_km": round(float(dist_max), 1),
            }

            for col in dju_cols:
                if col not in grp.columns:
                    continue
                vals = []
                for p in sel_postes[present_mask]:
                    v = grp.loc[grp["num_poste"] == p, col]
                    vals.append(float(v.values[0]) if len(v) else np.nan)
                vals  = np.array(vals)
                valid = ~np.isnan(vals)
                if not np.any(valid):
                    row_result[col] = np.nan
                else:
                    w_v = idw_weights(sel_dists[present_mask][valid], power=power)
                    row_result[col] = round(float(np.dot(w_v, vals[valid])), 2)

            results.append(row_result)

    return pd.DataFrame(results)
