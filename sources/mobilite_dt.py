"""
sources/mobilite_dt.py
======================
Émissions GES et consommation d'énergie des déplacements domicile-travail,
à l'échelle communale (commune de résidence).

Source officielle :
  Estimation des émissions individuelles de GES lors des déplacements
  domicile-travail - INSEE × SDES
  https://www.data.gouv.fr/datasets/estimation-des-emissions-individuelles-de-gaz-a-effet-de-serre-lors-des-deplacements-domicile-travail

Méthodologie :
  Pour chaque individu du fichier (pondéré par IPONDI) :
    CO2_HEBDO   : émissions hebdomadaires en gCO₂eq (tous modes inclus)
    CARBU_HEBDO : consommation de carburant hebdomadaire en litres
  Agrégation par commune de résidence (LIEU_RESID) × 52 semaines.

Années disponibles : 2019, 2021, 2022 (2020 exclu - COVID-19).

Conversion énergie :
  CARBU_HEBDO (litres/sem) × IPONDI × 52 semaines → litres/an
  × PCI moyen du mix carburant routier (9,5 kWh/L PCI, mix essence/diesel)
  → GWh/an
  Source PCI : ADEME base carbone (essence ≈ 8,96 kWh/L, diesel ≈ 9,97 kWh/L)
  Mix 2021 ≈ 55 % diesel / 45 % essence → 9,5 kWh/L PCI retenu.
"""

import logging
import zipfile
from pathlib import Path

import pandas as pd
import requests

from config.settings import RAW_DIR, INTERIM_DIR

log = logging.getLogger(__name__)

SOURCE_META = {
    "id": "MOBILITE_DT",
    "nom": "Mobilite domicile-travail - INSEE x SDES",
    "description": (
        "Emissions GES et consommation d'energie finale des deplacements "
        "domicile-travail par commune de residence. Estimation individuelle "
        "ponderee (IPONDI) depuis le fichier detail navettage du recensement "
        "de la population."
    ),
    "urls": (
        "https://www.data.gouv.fr/datasets/"
        "estimation-des-emissions-individuelles-de-gaz-a-effet-de-serre-"
        "lors-des-deplacements-domicile-travail"
    ),
    "niveau_geo": "Commune - agregation EPCI",
    "annees": "2019, 2021, 2022 (2020 exclu COVID-19)",
    "format": "ZIP + CSV individus (separateur ; ou ,)",
    "acces": "Automatique (API data.gouv.fr)",
    "frequence_maj": "~2 ans",
    "licence": "Etalab 2.0",
    "theme": "Mobilite",
    "notes": (
        "CO2_HEBDO (gCO2eq/sem) x IPONDI x 52 - ktCO2eq/an. "
        "CARBU_HEBDO (L/sem) x IPONDI x 52 x 9,5 kWh/L PCI - GWh/an. "
        "Filtre CHAMP_CO2=1 (lignes avec CO2 calculable)."
    ),
    "indicateurs": [
        {"code": "MOB_DT_EMISSIONS",    "label": "Emissions GES mobilite domicile-travail",       "unite": "ktCO2e/an", "methode_agregation": "Somme", "annees": "2019, 2021, 2022"},
        {"code": "MOB_DT_CONSO_FINALE", "label": "Consommation finale mobilite domicile-travail", "unite": "GWh/an",    "methode_agregation": "Somme", "annees": "2019, 2021, 2022"},
    ],
}

_DATASET_ID = (
    "estimation-des-emissions-individuelles-de-gaz-a-effet-de-serre-"
    "lors-des-deplacements-domicile-travail"
)
_API_URL = f"https://www.data.gouv.fr/api/1/datasets/{_DATASET_ID}/"
_ANNEES_DISPONIBLES = {2019, 2021, 2022}

# Pouvoir calorifique inférieur moyen du mix carburant VP France (GWh par litre)
_PCI_MOYEN_GWH_PAR_L: float = 9.5e-6


def _get_download_url(annee: int) -> str:
    resp = requests.get(_API_URL, timeout=30)
    resp.raise_for_status()
    resources = resp.json().get("resources", [])
    target = f"depl-dom-trav-co2-{annee}.zip"
    for r in resources:
        if target in r.get("url", "") or r.get("title", "").endswith(target):
            return r["url"]
    raise ValueError(
        f"Ressource '{target}' introuvable sur data.gouv.fr.\n"
        f"Titres disponibles : {[r.get('title') for r in resources]}"
    )


def _get_zip(annee: int) -> Path:
    """Télécharge et met en cache le ZIP (sans l'extraire sur disque)."""
    raw_dir = RAW_DIR / "mobilite_dt"
    raw_dir.mkdir(parents=True, exist_ok=True)
    zip_path = raw_dir / f"depl-dom-trav-co2-{annee}.zip"
    tmp_path = zip_path.with_suffix(".tmp")

    if zip_path.exists():
        log.info(f"Mobilité DT {annee} : ZIP en cache ({zip_path.stat().st_size / 1e6:.0f} Mo)")
        return zip_path

    url = _get_download_url(annee)
    log.info(f"Mobilité DT {annee} : téléchargement depuis {url} ...")
    resp = requests.get(url, timeout=600, stream=True)
    resp.raise_for_status()

    with open(tmp_path, "wb") as f:
        downloaded = 0
        for chunk in resp.iter_content(chunk_size=4 * 1024 * 1024):
            f.write(chunk)
            downloaded += len(chunk)
    tmp_path.rename(zip_path)
    log.info(f"Mobilité DT {annee} : ZIP sauvegardé ({zip_path.stat().st_size / 1e6:.0f} Mo)")
    return zip_path


def extract_indicators(annee: int) -> pd.DataFrame:
    """
    Émissions GES et consommation d'énergie des déplacements domicile-travail
    par commune de résidence.

    Retourne un DataFrame indexé sur CODGEO (str, 5 chars) avec colonnes :
        MOB_DT_EMISSIONS    (ktCO₂eq/an)
        MOB_DT_CONSO_FINALE (GWh/an)
    """
    if annee not in _ANNEES_DISPONIBLES:
        raise ValueError(
            f"Mobilité DT : année {annee} non disponible. "
            f"Années disponibles : {sorted(_ANNEES_DISPONIBLES)}"
        )

    cache_path = INTERIM_DIR / f"mobilite_dt_{annee}.parquet"
    if cache_path.exists():
        log.info(f"Mobilité DT {annee} : cache parquet utilisé")
        return pd.read_parquet(cache_path)

    zip_path = _get_zip(annee)

    # Lecture directe depuis le ZIP - le CSV n'est jamais extrait sur disque
    with zipfile.ZipFile(zip_path) as z:
        csv_names = [n for n in z.namelist() if n.endswith(".csv") and "dessin" not in n.lower()]
        if not csv_names:
            raise ValueError(f"Aucun CSV principal dans le ZIP {annee}. Contenu : {z.namelist()}")
        with z.open(csv_names[0]) as zf:
            first_line = zf.readline().decode("utf-8", errors="replace")
            sep = ";" if first_line.count(";") >= first_line.count(",") else ","

    log.info(f"Mobilité DT {annee} : lecture CSV depuis ZIP (sep='{sep}', colonnes sélectives)...")
    with zipfile.ZipFile(zip_path) as z:
        with z.open(csv_names[0]) as zf:
            df = pd.read_csv(
                zf,
                sep=sep,
                decimal=",",
                dtype={"LIEU_RESID": str},
                usecols=["LIEU_RESID", "IPONDI", "CO2_HEBDO", "CARBU_HEBDO", "CHAMP_CO2"],
                low_memory=False,
            )
    log.info(f"Mobilité DT {annee} : {len(df):,} lignes chargées")

    # Diagnostic : valeurs uniques CHAMP_CO2 et échantillon CO2_HEBDO
    log.debug(f"  CHAMP_CO2 uniques : {df['CHAMP_CO2'].value_counts().to_dict()}")
    log.debug(f"  CO2_HEBDO échantillon (5 premiers non-nuls) : {df.loc[df['CO2_HEBDO'] != 0, 'CO2_HEBDO'].head().tolist()}")

    # Filtrer les lignes avec CO2 calculable (CHAMP_CO2 = 1 ou True)
    champ = df["CHAMP_CO2"]
    if champ.dtype == object:
        mask = champ.astype(str).str.strip().isin({"1", "True", "true", "oui", "yes"})
    else:
        mask = champ == 1
    n_avant = len(df)
    df = df[mask].copy()
    log.info(f"Mobilité DT {annee} : {len(df):,} / {n_avant:,} lignes avec CO2 calculable")

    df["LIEU_RESID"]  = df["LIEU_RESID"].astype(str).str.zfill(5)
    df["CO2_HEBDO"]   = pd.to_numeric(df["CO2_HEBDO"],   errors="coerce").fillna(0.0)
    df["CARBU_HEBDO"] = pd.to_numeric(df["CARBU_HEBDO"], errors="coerce").fillna(0.0)
    df["IPONDI"]      = pd.to_numeric(df["IPONDI"],      errors="coerce").fillna(0.0)
    log.debug(f"  CO2_HEBDO max après conversion : {df['CO2_HEBDO'].max():.2f}, IPONDI max : {df['IPONDI'].max():.2f}")

    # Contribution annuelle pondérée par individu
    df["_co2_annuel"]   = df["IPONDI"] * df["CO2_HEBDO"]   * 52  # gCO₂eq/an
    df["_carbu_annuel"] = df["IPONDI"] * df["CARBU_HEBDO"] * 52  # litres/an

    agg = (
        df.groupby("LIEU_RESID")[["_co2_annuel", "_carbu_annuel"]]
        .sum()
        .rename_axis("CODGEO")
    )

    agg["MOB_DT_EMISSIONS"]    = agg["_co2_annuel"]   / 1e9                  # g → kt
    agg["MOB_DT_CONSO_FINALE"] = agg["_carbu_annuel"] * _PCI_MOYEN_GWH_PAR_L  # L → GWh

    result = agg[["MOB_DT_EMISSIONS", "MOB_DT_CONSO_FINALE"]]

    log.info(
        f"Mobilité DT {annee} : {len(result):,} communes - "
        f"émissions = {result['MOB_DT_EMISSIONS'].sum():,.1f} ktCO₂eq, "
        f"conso = {result['MOB_DT_CONSO_FINALE'].sum():,.0f} GWh"
    )

    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    result.to_parquet(cache_path)
    return result
