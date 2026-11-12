"""
sources/citepa_igt.py
=====================
Inventaire GES Territorialise (IGT) du CITEPA - emissions communales par secteur.

Source : https://www.citepa.org/donnees-air-climat/donnees-gaz-a-effet-de-serre/igt-inventaire-ges-territorialise/
         Edition 2023 (emissions 2021), ZIP contenant le rapport methodologique
         et le classeur de synthese.
Licence : Etalab 2.0 (donnees publiques CITEPA / MTECT)

Role dans le projet
-------------------
L'IGT sert de **repli aux donnees OREC pour les EPCI des DROM**, ou aucun
observatoire regional ne publie le bouquet d'indicateurs communs (seule La
Reunion dispose d'un observatoire, la Guadeloupe uniquement a l'echelle
regionale). Il alimente donc **les memes codes EC_EMIS_*** que
`sources/energie_climat.py`, avec `source = "CITEPA_IGT"` pour la tracabilite.
Le perimetre d'application est pilote par `IGT_ZONES` (config/settings.py) et
l'absence de recouvrement avec l'OREC est verifiee dans `pipeline.aggregate`.

Conventions retenues (verifiees sur l'onglet « Synthese FRDOM » du classeur)
---------------------------------------------------------------------------
- Les valeurs sont en **tCO2e deja ponderes du PRG**, une ligne par
  (commune x gaz). Converties ici en ktCO2e/an pour coller a l'unite OREC.
- Le **CO2 biogenique** (`CODPOL = "CO2bio"`) est reporte separement par le
  CITEPA et **exclu du total inventaire** : il est ecarte ici, conformement a
  la convention SECTEN/SNBC utilisee par le reste du projet.
- Les colonnes « Autres transports internationaux » et « export » sont
  signalees « hors total inventaire » par le CITEPA : elles sont exclues de
  `EC_EMIS_TOTAL`. Les soutes internationales restent exposees a part dans
  `IGT_EMIS_TRANSPORT_INTL`, a titre de **diagnostic uniquement** (elles
  servent a mesurer la concentration des emissions aeriennes/maritimes sur les
  EPCI portuaires et aeroportuaires des DROM, cf. arbitrage cadastral vs
  responsabilite), et ne sont jamais utilisees comme pivot ni comme recalage.
- L'IGT **ne couvre pas l'UTCATF** (« hors puits de carbone ») : aucun
  equivalent ALDO n'en est derive.

Limite methodologique a rappeler dans les livrables : l'IGT est une estimation
**descendante** (desagregation de l'inventaire national), pas un diagnostic
local. Le CITEPA precise que les donnees ne sont representatives qu'agregees a
l'echelle EPCI.

Format de sortie
----------------
DataFrame indexe sur CODGEO (code INSEE commune, 5 caracteres),
colonnes = IGT_CODES. L'agregation commune->EPCI est faite en aval par
`pipeline.aggregate.aggregate_communes_to_epci`.
"""

import logging
import zipfile

import pandas as pd
import requests

from config.settings import INTERIM_DIR, RAW_DIR

log = logging.getLogger(__name__)

# Millesime des emissions porte par l'edition telechargee.
IGT_ANNEE = 2021

SOURCE_META = {
    "id": "CITEPA_IGT",
    "nom": "CITEPA - Inventaire GES Territorialise (IGT)",
    "description": (
        "Emissions de GES par commune et par secteur, desagregees depuis "
        "l'inventaire national (approche PCAET : les emissions de la production "
        "centralisee d'electricite, de chaleur et de froid sont reaffectees aux "
        "secteurs consommateurs). Utilise comme repli aux donnees OREC sur les "
        "EPCI des DROM, ou les observatoires regionaux sont absents."
    ),
    "urls": (
        "https://www.citepa.org/donnees-air-climat/donnees-gaz-a-effet-de-serre/"
        "igt-inventaire-ges-territorialise/"
    ),
    "niveau_geo": "Commune - agregation EPCI",
    "annees": f"{IGT_ANNEE} (millesimes anterieurs : 2016, 2018)",
    "format": "XLSX dans une archive ZIP",
    "acces": "Automatique (telechargement)",
    "frequence_maj": "Irreguliere (~2-3 ans)",
    "licence": "Etalab 2.0",
    "theme": "Energie / Climat",
    "notes": (
        "Estimation descendante, non representative en dessous de l'echelle EPCI. "
        "CO2 biogenique, soutes internationales et export exclus du total. "
        "Ne couvre pas l'UTCATF."
    ),
    "indicateurs": [
        {"code": "EC_EMIS_RESIDENTIEL",       "label": "Emissions GES - residentiel",                    "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": str(IGT_ANNEE)},
        {"code": "EC_EMIS_TERTIAIRE",         "label": "Emissions GES - tertiaire",                      "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": str(IGT_ANNEE)},
        {"code": "EC_EMIS_INDUSTRIE_HORS_BE", "label": "Emissions GES - industrie hors branche energie", "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": str(IGT_ANNEE)},
        {"code": "EC_EMIS_BRANCHE_ENERGIE",   "label": "Emissions GES - branche energie",                "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": str(IGT_ANNEE)},
        {"code": "EC_EMIS_AGRICULTURE",       "label": "Emissions GES - agriculture",                    "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": str(IGT_ANNEE)},
        {"code": "EC_EMIS_TRANSPORT_ROUTIER", "label": "Emissions GES - transport routier",              "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": str(IGT_ANNEE)},
        {"code": "EC_EMIS_AUTRE_TRANSPORT",   "label": "Emissions GES - autres transports domestiques",  "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": str(IGT_ANNEE)},
        {"code": "EC_EMIS_DECHETS",           "label": "Emissions GES - dechets",                        "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": str(IGT_ANNEE)},
        {"code": "EC_EMIS_TOTAL",             "label": "Emissions GES - total",                          "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": str(IGT_ANNEE)},
        {"code": "IGT_EMIS_TRANSPORT_INTL",   "label": "Emissions GES - soutes internationales (diagnostic, hors total)", "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": str(IGT_ANNEE)},
    ],
}

_RAW_DIR   = RAW_DIR / "citepa_igt"
_ZIP_PATH  = _RAW_DIR / "IGT_edition_2023.zip"
_XLSX_PATH = _RAW_DIR / "IGT_2021.xlsx"
_CACHE     = INTERIM_DIR / "citepa_igt_communes.parquet"

_ZIP_URL = "https://www.citepa.org/wp-content/uploads/2025/02/IGT_edition_2023.zip"

_SHEET = "TOT_CO2E"

# Gaz exclu du total inventaire par le CITEPA (reporte separement).
_CODPOL_EXCLU = "CO2bio"

# Colonne sectorielle du classeur -> code indicateur du projet.
# Les libelles sont ceux de l'en-tete de l'onglet TOT_CO2E (edition 2023).
_SECTEUR_TO_CODE: dict[str, str] = {
    "Energie":                                 "EC_EMIS_BRANCHE_ENERGIE",
    "Industrie (hors prod. centr. d'énergie)": "EC_EMIS_INDUSTRIE_HORS_BE",
    "Residentiel":                             "EC_EMIS_RESIDENTIEL",
    "Tertiaire":                               "EC_EMIS_TERTIAIRE",
    "Dechets":                                 "EC_EMIS_DECHETS",
    "Routier":                                 "EC_EMIS_TRANSPORT_ROUTIER",
    "Autres transports":                       "EC_EMIS_AUTRE_TRANSPORT",
    "Agriculture":                             "EC_EMIS_AGRICULTURE",
    # Hors total inventaire - diagnostic uniquement
    "Autres transports internationaux":        "IGT_EMIS_TRANSPORT_INTL",
}

# Codes entrant dans EC_EMIS_TOTAL (perimetre inventaire national).
_CODES_TOTAL = [
    "EC_EMIS_BRANCHE_ENERGIE",
    "EC_EMIS_INDUSTRIE_HORS_BE",
    "EC_EMIS_RESIDENTIEL",
    "EC_EMIS_TERTIAIRE",
    "EC_EMIS_DECHETS",
    "EC_EMIS_TRANSPORT_ROUTIER",
    "EC_EMIS_AUTRE_TRANSPORT",
    "EC_EMIS_AGRICULTURE",
]

IGT_CODES = _CODES_TOTAL + ["EC_EMIS_TOTAL", "IGT_EMIS_TRANSPORT_INTL"]

# Codes que l'IGT peut substituer aux donnees OREC (l'indicateur de diagnostic
# et le total ne servent ni de pivot ni de recalage, mais restent exposes).
IGT_CODES_EC = _CODES_TOTAL + ["EC_EMIS_TOTAL"]

# Valeurs de la colonne PERIMETRE correspondant aux 5 DROM.
PERIMETRES_DROM = ("Guadeloupe", "Martinique", "Guyane", "La Réunion", "Mayotte")


def _download() -> None:
    """Telecharge l'archive IGT et en extrait le classeur (idempotent)."""
    if _XLSX_PATH.exists():
        return
    _RAW_DIR.mkdir(parents=True, exist_ok=True)

    if not _ZIP_PATH.exists():
        log.info(f"Telechargement IGT : {_ZIP_URL}")
        r = requests.get(_ZIP_URL, timeout=600, stream=True)
        r.raise_for_status()
        with open(_ZIP_PATH, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)

    with zipfile.ZipFile(_ZIP_PATH) as z:
        xlsx = [n for n in z.namelist() if n.lower().endswith(".xlsx")]
        if len(xlsx) != 1:
            raise RuntimeError(
                f"Archive IGT : 1 classeur attendu, {len(xlsx)} trouve(s) : {xlsx}"
            )
        _XLSX_PATH.write_bytes(z.read(xlsx[0]))
        log.info(f"IGT : classeur extrait ({xlsx[0]})")


def _read_tot_co2e() -> pd.DataFrame:
    """
    Lit l'onglet TOT_CO2E en streaming (279k lignes) et retourne un DataFrame
    (INSEE_COM, PERIMETRE, CODPOL, <colonnes sectorielles>).
    """
    import openpyxl

    wb = openpyxl.load_workbook(_XLSX_PATH, read_only=True, data_only=True)
    if _SHEET not in wb.sheetnames:
        raise RuntimeError(
            f"Onglet '{_SHEET}' absent du classeur IGT (onglets : {wb.sheetnames})"
        )
    ws = wb[_SHEET]
    rows = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows)]

    manquantes = [c for c in _SECTEUR_TO_CODE if c not in header]
    if manquantes:
        raise RuntimeError(
            f"Colonnes sectorielles IGT introuvables : {manquantes}\n"
            f"En-tete du classeur : {header}"
        )
    for col in ("INSEE_COM", "PERIMETRE", "CODPOL"):
        if col not in header:
            raise RuntimeError(f"Colonne '{col}' absente de l'onglet {_SHEET}")

    i_com, i_per, i_pol = (header.index(c) for c in ("INSEE_COM", "PERIMETRE", "CODPOL"))
    sect_idx = [(header.index(c), c) for c in _SECTEUR_TO_CODE]

    records = []
    for r in rows:
        codgeo = r[i_com]
        if codgeo is None:
            continue
        if str(r[i_pol]).strip() == _CODPOL_EXCLU:
            continue
        rec = {
            "CODGEO":    str(codgeo).strip().zfill(5),
            "PERIMETRE": str(r[i_per]).strip() if r[i_per] is not None else "",
        }
        for i, col in sect_idx:
            v = r[i] if i < len(r) else None
            rec[col] = pd.to_numeric(v, errors="coerce")
        records.append(rec)
    wb.close()

    df = pd.DataFrame.from_records(records)
    log.info(f"IGT : {len(df)} lignes lues (hors {_CODPOL_EXCLU})")
    return df


def extract_indicators(perimetres: tuple[str, ...] | None = None) -> pd.DataFrame:
    """
    Charge les emissions IGT agregees par commune (somme sur les gaz).

    Parametres
    ----------
    perimetres : restriction sur la colonne PERIMETRE du classeur
                 (ex. PERIMETRES_DROM). None = tous les perimetres.

    Retourne
    --------
    DataFrame indexe sur CODGEO, colonnes = IGT_CODES, en ktCO2e/an.
    """
    if _CACHE.exists():
        log.info(f"Cache IGT : {_CACHE.name}")
        df = pd.read_parquet(_CACHE)
    else:
        _download()
        df_raw = _read_tot_co2e()

        # Somme sur les gaz, par commune, en conservant le perimetre
        sect_cols = list(_SECTEUR_TO_CODE)
        df = (
            df_raw.groupby(["CODGEO", "PERIMETRE"], as_index=False)[sect_cols]
            .sum(min_count=1)
            .rename(columns=_SECTEUR_TO_CODE)
        )

        # tCO2e -> ktCO2e
        val_cols = list(_SECTEUR_TO_CODE.values())
        df[val_cols] = df[val_cols] * 1e-3

        df["EC_EMIS_TOTAL"] = df[_CODES_TOTAL].sum(axis=1, min_count=1)
        df = df.set_index("CODGEO")[["PERIMETRE"] + IGT_CODES]

        INTERIM_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(_CACHE)
        log.info(f"IGT : {len(df)} communes, {len(IGT_CODES)} indicateurs (cache ecrit)")

    if perimetres is not None:
        inconnus = set(perimetres) - set(df["PERIMETRE"].unique())
        if inconnus:
            raise RuntimeError(
                f"Perimetres IGT inconnus : {sorted(inconnus)} "
                f"(disponibles : {sorted(df['PERIMETRE'].unique())})"
            )
        df = df[df["PERIMETRE"].isin(perimetres)]

    return df[IGT_CODES]
