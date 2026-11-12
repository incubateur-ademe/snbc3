"""
sources/aldo.py
===============
Chargement des donnees UTCATF issues d'ALDO (ADEME) au niveau communal.

Source : opendata ADEME - ALDO
         https://data.ademe.fr/datasets?topics=osPW_q87OEXxcllo_gAj4
Licence : Licence Ouverte Etalab 2.0

Deux datasets CSV telecharges depuis l'API data.ademe.fr (data-fair) :
  - flux1 : changements d'affectation des sols (surfaces converties + flux)
  - flux3 : accroissement biomasse forestiere + produits bois

Indicateurs produits (ktCO2e/an, un par sous-secteur SNBC UTCATF) :
  ALDO_UTCATF_FOR       - Forets (biomasse + CAS vers foret)
  ALDO_UTCATF_CULT      - Cultures (CAS vers cultures)
  ALDO_UTCATF_PRAI      - Prairies (CAS vers prairies)
  ALDO_UTCATF_ZOHU      - Zones humides (CAS vers zones humides)
  ALDO_UTCATF_ARTI      - Sols artificiels (CAS vers sols artificiels)
  ALDO_UTCATF_AUT       - Autres terres (CAS vers vergers/vignes)
  ALDO_UTCATF_PRBO      - Produits bois, approche consommation (reserve,
                          non utilise dans le pivot)
  ALDO_UTCATF_PRBO_PROD - Produits bois, approche production/recolte (pivot)

Indicateur de surface (ha) :
  ALDO_SURF_FORET       - Surface forestiere (4 types de foret de flux3), cle
                          de repartition des sols forestiers hors inventaire :
                          toujours positive, contrairement au flux net de la
                          foret qui est emetteur sur certains territoires.

Convention de sortie : positif = emission, negatif = sequestration (comme le
reste du projet). Les valeurs ALDO brutes (positif = sequestration) sont
inversees et converties de tCO2e en ktCO2e (flux uniquement, pas la surface).

Mapping flux1 : chaque transition est attribuee a la categorie de destination
(convention CITEPA). Flux3 : biomasse foret ajoutee a FOR. Produits bois
disponibles selon deux approches ADEME : "production"/recolte (colonne
total_recolte_flux_tCO2e_an-1, ALDO_UTCATF_PRBO_PROD, approche par defaut
d'ALDO retenue comme pivot SNBC) et "consommation" (colonne
total_consommation_flux_tCO2e_an-1, ALDO_UTCATF_PRBO, gardee en reserve).
"""

import logging
import re

import pandas as pd
import requests

from config.settings import INTERIM_DIR, RAW_DIR

log = logging.getLogger(__name__)

SOURCE_META = {
    "id": "ALDO",
    "nom": "ADEME ALDO - Donnees UTCATF communales",
    "description": (
        "Donnees UTCATF issues d'ALDO (ADEME) au niveau communal. "
        "Flux de changement d'affectation des sols et accroissement "
        "biomasse forestiere."
    ),
    "urls": "https://data.ademe.fr/datasets?topics=osPW_q87OEXxcllo_gAj4",
    "niveau_geo": "Commune - agregation EPCI",
    "annees": "Derniere annee disponible",
    "format": "CSV (API data-fair ADEME)",
    "acces": "Automatique (telechargement)",
    "frequence_maj": "Irreguliere",
    "licence": "Etalab 2.0",
    "theme": "UTCATF",
    "notes": (
        "Positif = emission, negatif = sequestration (convention inversee par "
        "rapport aux donnees ALDO brutes). Conversion tCO2e en ktCO2e."
    ),
    "indicateurs": [
        {"code": "ALDO_UTCATF_FOR",  "label": "UTCATF - Forets",              "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": "2020"},
        {"code": "ALDO_UTCATF_CULT", "label": "UTCATF - Cultures",             "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": "2020"},
        {"code": "ALDO_UTCATF_PRAI", "label": "UTCATF - Prairies",             "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": "2020"},
        {"code": "ALDO_UTCATF_ZOHU", "label": "UTCATF - Zones humides",        "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": "2020"},
        {"code": "ALDO_UTCATF_ARTI", "label": "UTCATF - Sols artificiels",     "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": "2020"},
        {"code": "ALDO_UTCATF_AUT",  "label": "UTCATF - Autres terres",        "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": "2020"},
        {"code": "ALDO_UTCATF_PRBO", "label": "UTCATF - Produits bois (approche consommation)", "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": "2020"},
        {"code": "ALDO_UTCATF_PRBO_PROD", "label": "UTCATF - Produits bois (approche production/recolte)", "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": "2020"},
        {"code": "ALDO_SURF_FORET", "label": "Surface forestiere", "unite": "ha", "methode_agregation": "Somme", "annees": "2020"},
    ],
}

_RAW_DIR = RAW_DIR / "aldo"

_FLUX1_URL = (
    "https://data.ademe.fr/data-fair/api/v1/datasets/"
    "aldo-flux-total-et-surfaces-converties/full"
)
_FLUX3_URL = (
    "https://data.ademe.fr/data-fair/api/v1/datasets/"
    "aldo-flux-annuels-lies-a-laccroissement-net-de-la-biomasse-en-forets/full"
)

_FLUX1_CSV = _RAW_DIR / "aldo_flux1.csv"
_FLUX3_CSV = _RAW_DIR / "aldo_flux3.csv"
_CACHE = INTERIM_DIR / "aldo_utcatf_communes.parquet"

# Flux (ktCO2e/an, convention emission) puis surface (ha)
ALDO_FLUX_CODES = [
    "ALDO_UTCATF_FOR",
    "ALDO_UTCATF_CULT",
    "ALDO_UTCATF_PRAI",
    "ALDO_UTCATF_ZOHU",
    "ALDO_UTCATF_ARTI",
    "ALDO_UTCATF_AUT",
    "ALDO_UTCATF_PRBO",
    "ALDO_UTCATF_PRBO_PROD",
]
ALDO_CODES = ALDO_FLUX_CODES + ["ALDO_SURF_FORET"]

_DEST_TO_SNBC = {
    "cultures": "ALDO_UTCATF_CULT",
    "prairies zones arborées": "ALDO_UTCATF_PRAI",
    "prairies zones herbacées": "ALDO_UTCATF_PRAI",
    "prairies zones arbustives": "ALDO_UTCATF_PRAI",
    "zones humides": "ALDO_UTCATF_ZOHU",
    "vergers": "ALDO_UTCATF_AUT",
    "vignes": "ALDO_UTCATF_AUT",
    "sols artificiels arbustifs": "ALDO_UTCATF_ARTI",
    "sols artificiels imperméabilisés": "ALDO_UTCATF_ARTI",
    "sols artificiels arborés et buissonants": "ALDO_UTCATF_ARTI",
    "forêt mixte": "ALDO_UTCATF_FOR",
    "forêt feuillu": "ALDO_UTCATF_FOR",
    "forêt conifere": "ALDO_UTCATF_FOR",
    "forêt peupleraie": "ALDO_UTCATF_FOR",
}

_FLUX3_FOREST_COLS = [
    "forêt mixte_flux_tCO2e_an-1",
    "forêt feuillu_flux_tCO2e_an-1",
    "forêt conifere_flux_tCO2e_an-1",
    "forêt peupleraie_flux_tCO2e_an-1",
]
_FLUX3_FOREST_SURF_COLS = [c.replace("_flux_tCO2e_an-1", "_surface_ha") for c in _FLUX3_FOREST_COLS]
_FLUX3_PRBO_COL = "total_consommation_flux_tCO2e_an-1"
_FLUX3_PRBO_PROD_COL = "total_recolte_flux_tCO2e_an-1"


def _download(url, dest):
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info(f"Telechargement ALDO : {dest.name}")
    r = requests.get(url, timeout=300, stream=True)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=65536):
            f.write(chunk)
    return dest


def _parse_dest(col):
    m = re.match(r"^.+_vers_(.+)_tCO2e_an-1$", col)
    return m.group(1) if m else None


def extract_indicators():
    """
    Charge et agrege les indicateurs ALDO UTCATF au niveau communal.

    Retourne un DataFrame indexe sur CODGEO (code INSEE commune),
    colonnes = ALDO_CODES.
    """
    if _CACHE.exists():
        cached = pd.read_parquet(_CACHE)
        if set(ALDO_CODES) <= set(cached.columns):
            log.info(f"Cache ALDO UTCATF : {_CACHE.name}")
            return cached
        log.info("Cache ALDO UTCATF incomplet (indicateur ajoute) : reconstruction")

    _download(_FLUX1_URL, _FLUX1_CSV)
    _download(_FLUX3_URL, _FLUX3_CSV)

    # --- flux1 : changements d'affectation des sols ---
    df1 = pd.read_csv(_FLUX1_CSV, dtype={"insee": str, "epci": str, "departement": str})

    col_mapping = {}
    for col in df1.columns:
        if not col.endswith("_tCO2e_an-1") or col == "flux_tCO2e_an-1":
            continue
        dest = _parse_dest(col)
        if dest is None:
            continue
        snbc = _DEST_TO_SNBC.get(dest)
        if snbc:
            col_mapping[col] = snbc

    result = df1[["insee"]].copy()
    for code in ALDO_CODES:
        cols = [c for c, s in col_mapping.items() if s == code]
        result[code] = df1[cols].sum(axis=1) if cols else 0.0

    # --- flux3 : forets et produits bois ---
    df3 = pd.read_csv(_FLUX3_CSV, dtype={"insee": str, "epci": str, "departement": str})

    forest_cols = [c for c in _FLUX3_FOREST_COLS if c in df3.columns]
    if forest_cols:
        biomass = df3.set_index("insee")[forest_cols].sum(axis=1)
        result = result.set_index("insee")
        result["ALDO_UTCATF_FOR"] += biomass.reindex(result.index, fill_value=0.0)
        result = result.reset_index()

    surf_cols = [c for c in _FLUX3_FOREST_SURF_COLS if c in df3.columns]
    if surf_cols:
        surf = df3.set_index("insee")[surf_cols].sum(axis=1)
        result = result.set_index("insee")
        result["ALDO_SURF_FORET"] = surf.reindex(result.index, fill_value=0.0)
        result = result.reset_index()

    if _FLUX3_PRBO_COL in df3.columns:
        prbo = df3.set_index("insee")[_FLUX3_PRBO_COL]
        result = result.set_index("insee")
        result["ALDO_UTCATF_PRBO"] += prbo.reindex(result.index, fill_value=0.0)
        result = result.reset_index()

    if _FLUX3_PRBO_PROD_COL in df3.columns:
        prbo_prod = df3.set_index("insee")[_FLUX3_PRBO_PROD_COL]
        result = result.set_index("insee")
        result["ALDO_UTCATF_PRBO_PROD"] += prbo_prod.reindex(result.index, fill_value=0.0)
        result = result.reset_index()

    result["insee"] = result["insee"].astype(str).str.zfill(5)
    result = result.set_index("insee").fillna(0.0)
    result.index.name = "CODGEO"

    # Convention emissions (positif = emission) + conversion tCO2e -> ktCO2e
    result[ALDO_FLUX_CODES] = result[ALDO_FLUX_CODES] * (-1e-3)

    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    result.to_parquet(_CACHE)
    log.info(f"ALDO UTCATF : {len(result)} communes, {len(ALDO_CODES)} indicateurs (cache ecrit)")
    return result
