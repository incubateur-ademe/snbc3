"""
sources/parc_vehicules.py
=========================
Parc de véhicules routiers par commune (VP, VUL, PL, Bus-cars).

Source officielle :
  Parc de véhicules routiers - SDES (RSVERO), ressource « Données sur le parc
  de véhicules au niveau communal »
  https://www.data.gouv.fr/datasets/parc-de-vehicules-routiers
  (ancien jeu « donnees-sur-le-parc-de-vehicules-au-niveau-communal », retiré
  de data.gouv.fr en 2026 : l'API y renvoie 404)

Structure du CSV source (édition 2026) :
  COMMUNE_CODE        - code commune (5 caractères, 2A/2B pour la Corse) ;
                        codes fictifs 00000 et DD000 quand la commune de
                        l'utilisateur n'est pas identifiée (XXXXX dans
                        l'édition précédente)
  GROUPE              - catégorie : VP, VUL, PL, TCP (CLASSE_VEHICULE dans
                        l'édition précédente)
  CATEGORIE, CARBURANT, CRIT_AIR, STATUT_UTILISATEUR - détail, non utilisé
  PARC_YYYY           - nombre de véhicules au 1er janvier de l'année YYYY

Révisions : les données sont provisoires pour les années récentes et
révisées à chaque édition (cf. description du jeu sur data.gouv.fr).

Couverture temporelle : 2011 → dernière année disponible (mise à jour annuelle).
Maille : commune de résidence / siège social.

Limite connue : cette maille (résidence/siège social) ne reflète pas le lieu réel
de circulation/émission, notamment pour le fret PL longue distance/transit et les
autocars interurbains. Voir docs/resultats_territorialises.md,
"Limite connue : pivots PL et bus".
"""

import logging
from pathlib import Path

import pandas as pd
import requests

from config.settings import RAW_DIR, INTERIM_DIR

log = logging.getLogger(__name__)

SOURCE_META = {
    "id": "PARC_VEHICULES",
    "nom": "SDES - Parc de vehicules routiers par commune (RSVERO)",
    "description": (
        "Parc de vehicules routiers par commune de residence/siege social, "
        "par categorie (VP, VUL, PL, bus-cars) et type de carburant."
    ),
    "urls": "https://www.data.gouv.fr/datasets/parc-de-vehicules-routiers",
    "niveau_geo": "Commune - agregation EPCI",
    "annees": "2011 - derniere annee disponible (2026 provisoire a l'edition 2026)",
    "format": "CSV (API data.gouv.fr)",
    "acces": "Automatique (telechargement)",
    "frequence_maj": "Annuelle",
    "licence": "Etalab 2.0",
    "theme": "Mobilite",
    "notes": "Categories : VP, VUL, PL, TCP (bus/cars). Maille : commune de residence / siege social.",
    "indicateurs": [
        {"code": "PARC_VP",  "label": "Parc vehicules particuliers", "unite": "vehicules", "methode_agregation": "Somme", "annees": "2011-2026"},
        {"code": "PARC_VUL", "label": "Parc vehicules utilitaires legers", "unite": "vehicules", "methode_agregation": "Somme", "annees": "2011-2026"},
        {"code": "PARC_PL",  "label": "Parc poids lourds",           "unite": "vehicules", "methode_agregation": "Somme", "annees": "2011-2026"},
        {"code": "PARC_BUS", "label": "Parc bus et cars",             "unite": "vehicules", "methode_agregation": "Somme", "annees": "2011-2026"},
    ],
}

# Identifiant (et non slug) du jeu data.gouv.fr : le slug précédent a disparu
# lors de la republication du jeu par le SDES en 2026.
_DATASET_ID = "689c430e4bdb87869ae1fce7"  # parc-de-vehicules-routiers
_API_URL = f"https://www.data.gouv.fr/api/1/datasets/{_DATASET_ID}/"

# Mapping des valeurs de catégorie (GROUPE, ou CLASSE_VEHICULE dans l'édition
# précédente) vers les codes indicateurs.
# tcp = Transports Collectifs de Personnes (autobus, autocars)
_CLASSE_TO_IND: dict[str, str] = {
    "vp":     "PARC_VP",
    "vul":    "PARC_VUL",
    "pl":     "PARC_PL",
    "tcp":    "PARC_BUS",
    "autcar": "PARC_BUS",
    "bus":    "PARC_BUS",
}
_INDICATEURS = ["PARC_VP", "PARC_VUL", "PARC_PL", "PARC_BUS"]

# Colonnes candidates, de l'édition la plus récente à la plus ancienne.
_COLS_COMMUNE = ("COMMUNE_CODE", "CODGEO", "COM", "CODE_COMMUNE")
_COLS_CLASSE  = ("GROUPE", "CLASSE_VEHICULE", "CLASSE")

# Communes réelles : 5 chiffres ou 2A/2B + 3 chiffres (Corse), hors codes
# fictifs DD000 (département connu, commune non identifiée) et 00000.
_RE_COMMUNE  = r"^(?:\d{5}|2[AB]\d{3})$"
_RE_FICTIVE  = r"000$"

# Nom des caches : à changer à chaque changement d'édition ou de traitement,
# pour ne pas resservir un cache produit à partir de l'ancienne version.
_RAW_CSV_NAME  = "parc_vehicules_communes_sdes2026.csv"
_CACHE_VERSION = 2


def _get_csv_url() -> str:
    resp = requests.get(_API_URL, timeout=30)
    resp.raise_for_status()
    resources = resp.json().get("resources", [])
    for r in resources:
        fmt   = r.get("format", "").lower()
        title = r.get("title",  "").lower()
        if fmt == "csv" and "dictionnaire" not in title and "dessin" not in title:
            return r["url"]
    for r in resources:
        if r.get("format", "").lower() == "csv":
            return r["url"]
    raise ValueError(
        f"Aucune ressource CSV trouvée dans le dataset {_DATASET_ID}.\n"
        f"Ressources : {[r.get('title') for r in resources]}"
    )


def _download_csv() -> Path:
    raw_dir = RAW_DIR / "parc_vehicules"
    raw_dir.mkdir(parents=True, exist_ok=True)
    csv_path = raw_dir / _RAW_CSV_NAME

    if csv_path.exists():
        log.info(f"Parc véhicules : cache local utilisé ({csv_path.name})")
        return csv_path

    url = _get_csv_url()
    log.info(f"Parc véhicules : téléchargement depuis {url} ...")
    resp = requests.get(url, timeout=300, stream=True)
    resp.raise_for_status()

    tmp_path = csv_path.with_suffix(".part")
    with open(tmp_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=4 * 1024 * 1024):
            f.write(chunk)
    tmp_path.replace(csv_path)

    log.info(f"Parc véhicules : téléchargé → {csv_path.name} ({csv_path.stat().st_size / 1e6:.0f} Mo)")
    return csv_path


def _premiere_colonne(colonnes, candidats: tuple[str, ...], quoi: str) -> str:
    col = next((c for c in candidats if c in colonnes), None)
    if col is None:
        raise ValueError(f"Colonne {quoi} introuvable ({candidats}). Colonnes : {list(colonnes)}")
    return col


def _agreger(df: pd.DataFrame, annee: int) -> pd.DataFrame:
    """Agrège le CSV brut (colonnes en majuscules) par commune et catégorie."""
    parc_col = f"PARC_{annee}"
    if parc_col not in df.columns:
        avail = sorted(c for c in df.columns if c.startswith("PARC_"))
        raise ValueError(
            f"Colonne {parc_col} absente du CSV parc véhicules.\n"
            f"Colonnes disponibles : {avail}"
        )
    codgeo_col = _premiere_colonne(df.columns, _COLS_COMMUNE, "commune")
    classe_col = _premiere_colonne(df.columns, _COLS_CLASSE, "catégorie de véhicule")

    codes = df[codgeo_col].str.strip().str.upper()
    reelles = codes.str.match(_RE_COMMUNE, na=False) & ~codes.str.contains(_RE_FICTIVE, na=False)
    df = df[reelles].assign(**{codgeo_col: codes[reelles]})

    classes = df[classe_col].str.strip().str.lower()
    classes_detectees = set(classes.dropna().unique())
    log.info(f"Parc véhicules {annee} : catégories détectées → {sorted(classes_detectees)}")
    inconnues = classes_detectees - set(_CLASSE_TO_IND)
    if inconnues:
        log.debug(f"Parc véhicules : catégories non mappées (ignorées) : {sorted(inconnues)}")

    df = df.assign(_ind=classes.map(_CLASSE_TO_IND))
    df = df[df["_ind"].notna()]
    valeurs = pd.to_numeric(df[parc_col], errors="coerce").astype(float).fillna(0.0)

    agg = (
        valeurs.groupby([df[codgeo_col], df["_ind"]])
        .sum()
        .unstack(fill_value=0.0)
        .rename_axis(index="CODGEO", columns=None)
    )
    for col in _INDICATEURS:
        if col not in agg.columns:
            agg[col] = 0.0
            log.warning(f"Parc véhicules {annee} : catégorie {col} absente du fichier source")
    return agg[_INDICATEURS].astype(float)


def extract_indicators(annee: int) -> pd.DataFrame:
    """
    Parc de véhicules par commune au 1er janvier de l'année demandée.

    Retourne un DataFrame indexé sur CODGEO (str, 5 chars) avec colonnes :
        PARC_VP   - voitures particulières
        PARC_VUL  - véhicules utilitaires légers
        PARC_PL   - poids lourds (camions)
        PARC_BUS  - bus et autocars
    """
    cache_path = INTERIM_DIR / f"parc_vehicules_{annee}_v{_CACHE_VERSION}.parquet"
    if cache_path.exists():
        log.info(f"Parc véhicules {annee} : cache parquet utilisé")
        return pd.read_parquet(cache_path)

    csv_path = _download_csv()
    log.info(f"Parc véhicules {annee} : lecture du CSV...")
    usecols = lambda c: c.strip().upper() in (*_COLS_COMMUNE, *_COLS_CLASSE, f"PARC_{annee}")
    df = pd.read_csv(csv_path, sep=";", dtype=str, usecols=usecols, low_memory=False)
    df.columns = df.columns.str.strip().str.upper()

    result = _agreger(df, annee)

    log.info(
        f"Parc véhicules {annee} : {len(result):,} communes - "
        f"VP={result['PARC_VP'].sum():,.0f}, "
        f"VUL={result['PARC_VUL'].sum():,.0f}, "
        f"PL={result['PARC_PL'].sum():,.0f}, "
        f"BUS={result['PARC_BUS'].sum():,.0f}"
    )

    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    result.to_parquet(cache_path)
    return result
