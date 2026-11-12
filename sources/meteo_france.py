"""
sources/meteo_france.py
=======================
Téléchargement et chargement des données brutes Météo-France open data.
Gère le cache dans data/interim/ pour éviter les re-téléchargements.

Source : https://meteo.data.gouv.fr / data.gouv.fr
Licence : Licence Ouverte Etalab 2.0
"""

import gzip
import logging
import time
from pathlib import Path

import pandas as pd
import requests

from config.settings import DEPARTEMENTS, INTERIM_DIR

# ---------------------------------------------------------------------------
# URLs Météo-France open data
# ---------------------------------------------------------------------------
URL_POSTES_MF = "https://www.data.gouv.fr/fr/datasets/r/1fe544d8-4615-4642-a307-5956a7d90922"
MF_BASE_URL   = "https://object.files.data.gouv.fr/meteofrance/data/synchro_ftp/BASE/QUOT/"

SOURCE_META = {
    "id": "METEO_FRANCE",
    "nom": "Meteo-France - Donnees climatologiques de base",
    "description": (
        "Temperatures journalieres (Tmin, Tmax) par station meteo. "
        "Calcul des Degres-Jours Unifies (DJU) chaud et froid selon "
        "methodes Meteo et COSTIC. "
        "Interpolation spatiale IDW des stations vers les centroides EPCI."
    ),
    "urls": (
        f"{URL_POSTES_MF} (catalogue stations)\n"
        f"{MF_BASE_URL} (donnees quotidiennes)"
    ),
    "niveau_geo": "Station meteo - EPCI (interpolation IDW)",
    "annees": "1996 - 2024 (historique complet)",
    "format": "CSV.GZ par departement/periode",
    "acces": "Automatique (telechargement)",
    "frequence_maj": "Annuelle",
    "licence": "Etalab 2.0",
    "theme": "Climat",
    "notes": (
        "Methode Meteo : DJU = max(0, T_ref - T_moy) avec T_moy = (Tmin+Tmax)/2. "
        "Methode COSTIC (energie) : 3 cas selon position Tmin/Tmax par rapport au seuil. "
        "IDW puissance 2, 5 stations les plus proches dans un rayon de 150 km."
    ),
    "indicateurs": [
        {"code": "DJU_CHAUD_METEO",  "label": "DJU chauffage - methode Meteo",       "unite": "deg.j", "methode_agregation": "Moy. ponderee IDW", "annees": "1996-2024"},
        {"code": "DJU_FROID_METEO",  "label": "DJU climatisation - methode Meteo",   "unite": "deg.j", "methode_agregation": "Moy. ponderee IDW", "annees": "1996-2024"},
        {"code": "DJU_CHAUD_COSTIC", "label": "DJU chauffage - methode COSTIC",      "unite": "deg.j", "methode_agregation": "Moy. ponderee IDW", "annees": "1996-2024"},
        {"code": "DJU_FROID_COSTIC", "label": "DJU climatisation - methode COSTIC",  "unite": "deg.j", "methode_agregation": "Moy. ponderee IDW", "annees": "1996-2024"},
    ],
}

# Colonnes des fichiers quotidiens Météo-France
COL_ID      = "NUM_POSTE"
COL_DATE    = "AAAAMMJJ"
COL_TMIN    = "TN"
COL_TMAX    = "TX"
COL_QUAL_TN = "QTN"
COL_QUAL_TX = "QTX"

QUALITY_OK = {0, 1}

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers réseau
# ---------------------------------------------------------------------------

def download_with_retry(url: str, max_retries: int = 3, timeout: int = 60) -> bytes:
    """Télécharge une URL avec retry exponentiel."""
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            wait = 2 ** attempt
            log.warning(f"Échec téléchargement (tentative {attempt+1}/{max_retries}): {e} → attente {wait}s")
            if attempt < max_retries - 1:
                time.sleep(wait)
            else:
                raise


def cached_download(url: str, cache_path: Path, force: bool = False) -> Path:
    """Télécharge vers cache_path si absent (ou force=True)."""
    if cache_path.exists() and not force:
        log.debug(f"Cache hit: {cache_path.name}")
        return cache_path
    log.info(f"Téléchargement: {url}")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    data = download_with_retry(url)
    cache_path.write_bytes(data)
    return cache_path


# ---------------------------------------------------------------------------
# Chargement des postes météo
# ---------------------------------------------------------------------------

def load_postes(cache_dir: Path = INTERIM_DIR, force: bool = False) -> pd.DataFrame:
    """
    Charge la liste des postes Météo-France avec leurs coordonnées.
    Retourne un DataFrame : num_poste, nom, lat, lon[, alt][, dep]
    """
    import numpy as np

    cache_path = cache_dir / "postes_meteo.json"
    cached_download(URL_POSTES_MF, cache_path, force=force)

    df = pd.read_json(cache_path)

    # Normalisation des colonnes
    col_map = {}
    for col in df.columns:
        cl = col.lower()
        if ("num" in cl and "poste" in cl) or cl in ("id", "identifiant", "num_poste"):
            col_map[col] = "num_poste"
        elif cl in ("lat", "latitude"):
            col_map[col] = "lat"
        elif cl in ("lon", "lng", "longitude"):
            col_map[col] = "lon"
        elif cl in ("alt", "altitude"):
            col_map[col] = "alt"
        elif cl in ("nom", "nom_usuel", "libelle"):
            col_map[col] = "nom"
        elif cl in ("dep", "departement"):
            col_map[col] = "dep"
    df = df.rename(columns=col_map)

    # Extraction lat/lon depuis colonne 'positions' si nécessaire
    if ("lat" not in df.columns or "lon" not in df.columns) and "positions" in df.columns:
        log.info("Extraction lat/lon depuis la colonne 'positions'...")

        def _extract(positions, key):
            if positions is None:
                return np.nan
            if isinstance(positions, dict):
                return positions.get(key, np.nan)
            if isinstance(positions, list) and positions:
                entry = positions[0]
                return entry.get(key, np.nan) if isinstance(entry, dict) else np.nan
            return np.nan

        sample = df["positions"].dropna().iloc[0] if not df["positions"].dropna().empty else None
        lat_key, lon_key = "latitude", "longitude"
        if isinstance(sample, (dict, list)):
            src = sample if isinstance(sample, dict) else (sample[0] if sample else {})
            keys = {k.lower(): k for k in src.keys()} if isinstance(src, dict) else {}
            lat_key = keys.get("latitude", keys.get("lat", "latitude"))
            lon_key = keys.get("longitude", keys.get("lon", keys.get("lng", "longitude")))

        df["lat"] = df["positions"].apply(lambda p: _extract(p, lat_key))
        df["lon"] = df["positions"].apply(lambda p: _extract(p, lon_key))

    required = {"num_poste", "lat", "lon"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Colonnes manquantes dans le fichier des postes: {missing}. "
            f"Colonnes disponibles: {list(df.columns)}"
        )

    df["num_poste"] = df["num_poste"].astype(str).str.strip()
    df = df.dropna(subset=["lat", "lon"])
    df = df[df["lat"].between(-90, 90) & df["lon"].between(-180, 180)]

    log.info(f"{len(df)} postes météo chargés")
    extra = [c for c in ("alt", "dep", "nom") if c in df.columns]
    return df[["num_poste"] + extra + ["lat", "lon"]]


# ---------------------------------------------------------------------------
# Construction des URLs fichiers quotidiens MF
# ---------------------------------------------------------------------------

def _mf_periode(annee: int) -> str:
    """Retourne la période Météo-France correspondant à une année donnée."""
    annee_courante = pd.Timestamp.now().year
    if annee >= annee_courante - 1:
        return f"latest-{annee_courante - 1}-{annee_courante}"
    else:
        return f"previous-1950-{annee_courante - 2}"


def build_mf_file_urls(annee: int) -> list[tuple[str, str, str]]:
    """
    Construit la liste des URLs des fichiers quotidiens Météo-France
    pour une année donnée (tous les départements).

    Retourne une liste de (dep, url, periode).
    """
    periode = _mf_periode(annee)
    return [
        (dep, MF_BASE_URL + f"Q_{dep}_{periode}_RR-T-Vent.csv.gz", periode)
        for dep in DEPARTEMENTS
    ]


# ---------------------------------------------------------------------------
# Chargement des températures quotidiennes
# ---------------------------------------------------------------------------

def load_daily_temps_for_year(
    annee: int,
    raw_dir: Path | None = None,
    interim_dir: Path = INTERIM_DIR,
    force: bool = False,
) -> pd.DataFrame:
    """
    Charge les données quotidiennes Tmin/Tmax pour toutes les stations,
    pour l'année `annee`. Cache en Parquet dans interim_dir.

    Retourne un DataFrame : num_poste, date, tmin, tmax
    """
    from config.settings import RAW_DIR
    if raw_dir is None:
        raw_dir = RAW_DIR / "meteo"

    year_cache = interim_dir / f"daily_temps_{annee}.parquet"
    if year_cache.exists() and not force:
        log.info(f"Cache quotidien {annee}: chargement depuis {year_cache.name}")
        return pd.read_parquet(year_cache)

    raw_dir.mkdir(parents=True, exist_ok=True)
    interim_dir.mkdir(parents=True, exist_ok=True)

    url_list   = build_mf_file_urls(annee)
    all_chunks = []
    errors     = []

    for dep, url, periode in url_list:
        cache_gz = raw_dir / f"Q_{dep}_{periode}_raw.csv.gz"
        try:
            cached_download(url, cache_gz, force=force)
            with gzip.open(cache_gz, "rt", encoding="utf-8", errors="replace") as f:
                df_dep = pd.read_csv(f, sep=";", dtype={COL_ID: str}, low_memory=False)

            needed    = [COL_ID, COL_DATE, COL_TMIN, COL_TMAX]
            qual_cols = []
            for qcol in (COL_QUAL_TN, COL_QUAL_TX):
                if qcol in df_dep.columns:
                    needed.append(qcol)
                    qual_cols.append(qcol)

            df_dep = df_dep[[c for c in needed if c in df_dep.columns]].copy()

            df_dep[COL_DATE] = pd.to_datetime(
                df_dep[COL_DATE].astype(str), format="%Y%m%d", errors="coerce"
            )
            df_dep = df_dep[df_dep[COL_DATE].dt.year == annee]
            if df_dep.empty:
                continue

            for qcol in qual_cols:
                df_dep = df_dep[df_dep[qcol].isin(QUALITY_OK) | df_dep[qcol].isna()]

            for tcol in (COL_TMIN, COL_TMAX):
                if tcol in df_dep.columns:
                    df_dep[tcol] = pd.to_numeric(df_dep[tcol], errors="coerce")

            df_dep = df_dep.rename(columns={
                COL_ID: "num_poste", COL_DATE: "date",
                COL_TMIN: "tmin",   COL_TMAX: "tmax",
            })
            df_dep = df_dep[["num_poste", "date", "tmin", "tmax"]].dropna(subset=["tmin", "tmax"])
            all_chunks.append(df_dep)
            log.debug(f"Dep {dep}: {len(df_dep)} jours-stations")

        except Exception as e:
            log.warning(f"Dep {dep}: erreur → {e}")
            errors.append(dep)

    if errors:
        log.warning(f"{len(errors)} départements en erreur: {errors}")
    if not all_chunks:
        raise RuntimeError(f"Aucune donnée chargée pour l'année {annee}")

    df_all = pd.concat(all_chunks, ignore_index=True)
    df_all.to_parquet(year_cache, index=False)
    log.info(f"Année {annee}: {len(df_all)} enregistrements, {df_all['num_poste'].nunique()} stations")
    return df_all
