"""
sources/insee_rp.py
===================
Extraction des indicateurs démographiques et de logement depuis le
Recensement de la Population INSEE (dossier complet par commune).

Source : https://www.insee.fr/fr/statistiques/8202349 (RP 2021, etc.)
Licence : Licence Ouverte Etalab 2.0

Logique reproduite depuis Legacy/INSEE.ipynb.

Note secret statistique :
  Les cellules masquées par le secret statistique ont la valeur "s"
  dans les fichiers INSEE. Elles sont chargées comme NaN et tracées
  séparément dans un DataFrame "secret" booléen (même forme que le
  DataFrame de valeurs), propagé jusqu'à qualite="secret_stat" dans la
  BDD finale (pipeline/aggregate.py).
"""

import logging
import time
import zipfile
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests

from config.settings import RAW_DIR

log = logging.getLogger(__name__)

SOURCE_META = {
    "id": "INSEE_RP",
    "nom": "INSEE - Recensement de la Population (RP)",
    "description": (
        "Donnees de population, menages, logements (residences principales "
        "maisons/appartements) et emplois (commerce-transport-services, "
        "administration-education-sante-social) au niveau communal."
    ),
    "urls": (
        "https://www.insee.fr/fr/statistiques/8202349 (logement 2021)\n"
        "https://www.insee.fr/fr/statistiques/8201904 (population 2021)\n"
        "https://www.insee.fr/fr/statistiques/8202916 (emploi 2021)"
    ),
    "niveau_geo": "Commune - agregation EPCI",
    "annees": "2014, 2015, 2019, 2020, 2021",
    "format": "ZIP + CSV (separateur ;)",
    "acces": "Automatique (telechargement)",
    "frequence_maj": "~5 ans (cycle RP)",
    "licence": "Etalab 2.0",
    "theme": "Demographie / Logement / Economie",
    "notes": (
        "Valeurs masquees par le secret statistique INSEE codees 's' - NaN "
        "(qualite='secret_stat'). RP 2021 : colonne ACTOCC1564 (15-64 ans) "
        "remplace ACTOCC15P (15+)."
    ),
    "indicateurs": [
        {"code": "POP",        "label": "Population municipale",               "unite": "hab.",     "methode_agregation": "Somme", "annees": "2014, 2015, 2019-2021"},
        {"code": "MEN",        "label": "Nombre de menages",                   "unite": "menages",  "methode_agregation": "Somme", "annees": "2014, 2015, 2019-2021"},
        {"code": "RP_MAISON",  "label": "Res. princ. - maisons",              "unite": "logements","methode_agregation": "Somme", "annees": "2019-2021"},
        {"code": "RP_APPART",  "label": "Res. princ. - appartements",         "unite": "logements","methode_agregation": "Somme", "annees": "2019-2021"},
        {"code": "EMP_CTS",    "label": "Emplois commerce-transport-services", "unite": "emplois",  "methode_agregation": "Somme", "annees": "2019-2021"},
        {"code": "EMP_APESAS", "label": "Emplois admin.-educ.-sante-social",   "unite": "emplois",  "methode_agregation": "Somme", "annees": "2019-2021"},
    ],
}

# URL du fichier logement INSEE RP principal (à mettre à jour selon le millésime)
_RP_URLS: dict[int, str] = {
    2021: "https://www.insee.fr/fr/statistiques/fichier/8202349/base-cc-logement-2021_csv.zip",
    2020: "https://www.insee.fr/fr/statistiques/fichier/7631186/base-cc-logement-2020_csv.zip",
    2019: "https://www.insee.fr/fr/statistiques/fichier/6543200/base-cc-logement-2019.zip",
}

# Fichiers supplémentaires par année, quand le fichier logement ne contient pas
# toutes les variables nécessaires (P{aa}_POP, C{aa}_ACTOCC15P_CS*...).
# Format : liste de (url, nom_local_zip)
_RP_EXTRA_URLS: dict[int, list[tuple[str, str]]] = {
    2021: [
        (
            "https://www.insee.fr/fr/statistiques/fichier/8201904/base-cc-evol-struct-pop-2021_csv.zip",
            "rp_2021_pop_csv.zip",
        ),
        (
            "https://www.insee.fr/fr/statistiques/fichier/8202916/base-cc-emploi-pop-active-2021_csv.zip",
            "rp_2021_emploi_csv.zip",
        ),
    ],
    2020: [
        (
            "https://www.insee.fr/fr/statistiques/fichier/7632446/base-cc-evol-struct-pop-2020_csv.zip",
            "rp_2020_pop_csv.zip",
        ),
        (
            "https://www.insee.fr/fr/statistiques/fichier/7632867/base-cc-emploi-pop-active-2020_csv.zip",
            "rp_2020_emploi_csv.zip",
        ),
    ],
}

# Codes INSEE → codes indicateurs du catalogue
# {aa} sera remplacé par les 2 derniers chiffres de l'année (ex: "20" pour 2020)
_CODE_MAP: dict[str, str] = {
    "P{aa}_POP":            "POP",
    "C{aa}_MEN":            "MEN",
    "P{aa}_RPMAISON":       "RP_MAISON",
    "P{aa}_RPAPPART":       "RP_APPART",
    "C{aa}_ACTOCC15P_CS3":  "EMP_CTS",
    "C{aa}_ACTOCC15P_CS5":  "EMP_APESAS",
}

# Overrides par année : remplace les clés de _CODE_MAP qui n'existent pas
# dans le fichier INSEE de ce millésime (nommage différent selon la publication).
# Exemple : le fichier logement 2020 utilise P20_MEN au lieu de C20_MEN.
_CODE_MAP_OVERRIDES: dict[int, dict[str, str]] = {
    2021: {
        "C21_MEN": None,              # absent du fichier logement 2021
        "P21_MEN": "MEN",             # équivalent : "Nombre de ménages en 2021"
        # 2021 utilise ACTOCC1564 (15-64 ans) au lieu de ACTOCC15P (15 ans et +)
        "C21_ACTOCC15P_CS3": None,
        "C21_ACTOCC15P_CS5": None,
        "C21_ACTOCC1564_CS3": "EMP_CTS",    # cadres et prof. intellectuelles sup.
        "C21_ACTOCC1564_CS5": "EMP_APESAS", # employés
    },
    2020: {
        "C20_MEN": None,              # absent du fichier logement 2020
        "P20_MEN": "MEN",             # équivalent : "Nombre de ménages en 2020"
        # 2020 utilise ACTOCC1564 (15-64 ans) au lieu de ACTOCC15P (15 ans et +)
        "C20_ACTOCC15P_CS3": None,
        "C20_ACTOCC15P_CS5": None,
        "C20_ACTOCC1564_CS3": "EMP_CTS",    # cadres et prof. intellectuelles sup.
        "C20_ACTOCC1564_CS5": "EMP_APESAS", # employés
    },
}


def _resolve_codes(annee: int) -> dict[str, str]:
    """Retourne le mapping code_insee→code_indicateur pour une année donnée."""
    aa = str(annee)[-2:]
    codes = {k.replace("{aa}", aa): v for k, v in _CODE_MAP.items()}
    for key, val in _CODE_MAP_OVERRIDES.get(annee, {}).items():
        if val is None:
            codes.pop(key, None)
        else:
            codes[key] = val
    return codes


def _download_zip(url: str, out_path: Path) -> Path:
    """Télécharge un ZIP depuis url vers out_path (skip si déjà présent et valide)."""
    if out_path.exists():
        if zipfile.is_zipfile(out_path):
            log.info(f"Déjà en cache: {out_path.name}")
            return out_path
        log.warning(f"Cache corrompu, re-téléchargement: {out_path.name}")
        out_path.unlink()
    log.info(f"Téléchargement {out_path.name}...")
    for attempt in range(1, 4):
        try:
            r = requests.get(url, timeout=120, stream=True)
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
            break
        except requests.exceptions.RequestException as e:
            if out_path.exists():
                out_path.unlink()
            if attempt == 3:
                raise
            wait = attempt * 5
            log.warning(f"Erreur téléchargement (tentative {attempt}/3), nouvel essai dans {wait}s: {e}")
            time.sleep(wait)
    if not zipfile.is_zipfile(out_path):
        out_path.unlink()
        raise ValueError(
            f"Le fichier téléchargé n'est pas un ZIP valide: {url}\n"
            f"L'URL est peut-être incorrecte ou le fichier a changé de nom sur le serveur INSEE."
        )
    log.info(f"Téléchargé: {out_path.name} ({out_path.stat().st_size / 1e6:.1f} Mo)")
    return out_path


def _read_zip_csv(zip_path: Path) -> pd.DataFrame:
    """Lit le premier CSV base-cc-* d'un ZIP INSEE, retourne un DataFrame indexé sur CODGEO."""
    with zipfile.ZipFile(zip_path) as z:
        csv_names = [n for n in z.namelist() if n.lower().endswith(".csv") and "base-cc" in n.lower()]
        if not csv_names:
            csv_names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise FileNotFoundError(f"Aucun CSV trouvé dans {zip_path.name}. Contenu: {z.namelist()}")
        log.info(f"Lecture de {csv_names[0]} ({zip_path.name})...")
        with z.open(csv_names[0]) as f:
            df = pd.read_csv(
                BytesIO(f.read()),
                sep=";",
                dtype=str,
                # "s" (secret statistique) gardé en littéral pour détection
                # dans load_dossier_complet, avant conversion numérique.
                na_values=["nd", ""],
                low_memory=False,
            )
    codgeo_col = next((c for c in df.columns if c.strip().upper() in ("CODGEO", "COM")), None)
    if codgeo_col is None:
        raise ValueError(f"Colonne CODGEO introuvable dans {zip_path.name}.")
    df = df.rename(columns={codgeo_col: "CODGEO"})
    df["CODGEO"] = df["CODGEO"].astype(str).str.zfill(5)
    return df.set_index("CODGEO")


def download_dossier_complet(annee: int, raw_dir: Path = RAW_DIR / "insee") -> Path:
    """
    Télécharge le dossier complet INSEE RP pour l'année donnée.
    Retourne le chemin du fichier .zip téléchargé.

    Si le fichier est déjà présent dans raw_dir, ne re-télécharge pas.
    Pour forcer : supprimer le fichier manuellement.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)

    url = _RP_URLS.get(annee)
    if url is None:
        raise ValueError(
            f"URL non configurée pour l'année {annee}. "
            f"Années disponibles: {sorted(_RP_URLS.keys())}. "
            f"Mettre à jour _RP_URLS dans sources/insee_rp.py."
        )

    return _download_zip(url, raw_dir / f"dossier_complet_rp_{annee}.zip")


def load_dossier_complet(
    annee: int,
    raw_dir: Path = RAW_DIR / "insee",
    zip_path: Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Charge et normalise le dossier complet INSEE RP pour une année.

    Retourne (df_valeurs, df_secret) - deux DataFrames avec :
      - codgeo (str, 5 caractères, index)
      - colonnes de l'année normalisées selon _CODE_MAP
      df_valeurs : valeurs numériques (NaN si absentes ou masquées)
      df_secret  : booléen, True si la cellule vaut "s" (secret statistique)
                   dans le fichier source

    Pour les années avec fichiers supplémentaires (_RP_EXTRA_URLS), les colonnes
    manquantes sont cherchées dans ces fichiers et fusionnées automatiquement.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)

    if zip_path is None:
        zip_path = raw_dir / f"dossier_complet_rp_{annee}.zip"
    if not zip_path.exists():
        zip_path = download_dossier_complet(annee, raw_dir)

    log.info(f"Chargement dossier complet RP {annee} depuis {zip_path.name}...")
    df = _read_zip_csv(zip_path)

    # Fusionner les fichiers supplémentaires si nécessaire
    for url, nom_local in _RP_EXTRA_URLS.get(annee, []):
        extra_path = raw_dir / nom_local
        _download_zip(url, extra_path)
        df_extra = _read_zip_csv(extra_path)
        # N'ajouter que les colonnes absentes du fichier principal
        cols_nouvelles = [c for c in df_extra.columns if c not in df.columns]
        if cols_nouvelles:
            log.info(f"Fusion de {len(cols_nouvelles)} colonnes depuis {nom_local}")
            df = df.join(df_extra[cols_nouvelles], how="left")

    # Sélection et renommage des colonnes d'intérêt
    code_map = _resolve_codes(annee)
    available = {k: v for k, v in code_map.items() if k in df.columns}
    missing   = set(code_map.keys()) - set(df.columns)
    if missing:
        log.warning(f"Colonnes absentes dans le RP {annee}: {sorted(missing)}")

    df_sel = df[list(available.keys())]

    # --- Détection secret statistique (avant conversion numérique) ---
    df_secret = df_sel.eq("s").rename(columns=available)

    df_out = df_sel.rename(columns=available)
    for col in df_out.columns:
        df_out[col] = pd.to_numeric(df_out[col], errors="coerce")

    n_secret = int(df_secret.to_numpy().sum())
    if n_secret:
        log.info(
            f"RP {annee} : {n_secret} valeurs commune×indicateur masquées "
            f"par le secret statistique"
        )

    log.info(f"RP {annee}: {len(df_out)} communes, {len(df_out.columns)} indicateurs")
    return df_out, df_secret


def extract_indicators(
    annee: int,
    raw_dir: Path = RAW_DIR / "insee",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Point d'entrée principal : charge le dossier complet et retourne
    (df_valeurs, df_secret) codgeo→indicateurs pour l'année donnée.

    Wrapper de load_dossier_complet() avec mise en cache Parquet.
    """
    from config.settings import INTERIM_DIR

    cache_path        = INTERIM_DIR / f"insee_rp_communes_{annee}.parquet"
    cache_path_secret = INTERIM_DIR / f"insee_rp_communes_{annee}_secret.parquet"
    if cache_path.exists() and cache_path_secret.exists():
        log.info(f"Cache INSEE RP {annee}: {cache_path.name}")
        return pd.read_parquet(cache_path), pd.read_parquet(cache_path_secret)

    df, df_secret = load_dossier_complet(annee, raw_dir)
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path)
    df_secret.to_parquet(cache_path_secret)
    log.info(f"INSEE RP {annee} mis en cache: {cache_path.name}")
    return df, df_secret
