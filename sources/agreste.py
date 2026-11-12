"""
sources/agreste.py
==================
Données communales d'agriculture (RPG 2020 + RA 2020), agrégées par EPCI.

Source : fichier retraité par Solagro (2026).
Fichier : data/input/260716_RPG2020_RA2020_COM_2025.xlsx (mise à jour 2026-07-16 avec DROM)

Méthodologie (cf. onglet "Notes" du fichier) :
  - Surfaces (SAU, cultures, prairies, vergers, vignes) : issues du RPG 2020
    (Registre Parcellaire Graphique, déclarations PAC), disponibles nativement
    à la maille communale. Valeurs directes, pas de retraitement.
  - Cheptel (bovins, porcins, volailles, UGB) : issues du RA 2020
    (Recensement Agricole), dont Solagro disposait d'une version déjà
    retraitée à la maille EPCI. Ces effectifs ont été reventilés au niveau
    communal au prorata de la SAU de chaque commune dans son EPCI.
    => Les valeurs communales de cheptel sont donc des estimations ; elles ne
    sont fiables qu'une fois ré-agrégées au niveau EPCI (ce que fait
    load_ra_epci), où elles retrouvent les totaux RA 2020 d'origine.
  - RPG 2020 a été choisi (plutôt qu'un millésime plus récent) pour rester
    sur la même année que le RA 2020.

Millésime EPCI : le fichier fournit directement `siren_epci_2025`, mais on
ré-agrège ici via la correspondance commune->EPCI utilisée par les autres
sources (sources/geo_epci.load_commune_epci_cog : millésime Insee le plus
récent ; le cache agrégé porte ce millésime dans son nom), pour rester
cohérent avec le reste de bdd_epci.parquet. Les évolutions intercommunales
postérieures au RA 2020 (renumérotations, scissions, dissolutions, créations)
sont ainsi couvertes. Conséquence assumée : pour les EPCI dont le périmètre
diffère de celui sur lequel Solagro a pivoté le cheptel (au prorata de la SAU),
les totaux de cheptel ne retombent pas exactement sur les totaux RA 2020
d'origine.

Colonnes disponibles dans le fichier mais non retenues comme indicateurs
(cf. onglet RPG_RA2020_COM2025) : sau_bio_com, surf_vergers, surf_vignes.
"""

import logging

import pandas as pd

from config.settings import INPUT_DIR, INTERIM_DIR

log = logging.getLogger(__name__)

SOURCE_META = {
    "id": "AGRESTE_RA",
    "nom": "RPG 2020 + Recensement Agricole 2020 (retraitement Solagro)",
    "description": (
        "Surfaces agricoles (RPG 2020) et cheptel (RA 2020, pivote sur la SAU "
        "par Solagro). Donnees communales, agregees par EPCI."
    ),
    "urls": (
        "https://artificialisation.developpement-durable.gouv.fr/bases-donnees/registre-parcellaire-graphique\n"
        "https://stats.agriculture.gouv.fr/cartostat/"
    ),
    "niveau_geo": "Commune - agregation EPCI",
    "annees": "2020 (RPG et RA)",
    "format": "XLSX (data/input/260716_RPG2020_RA2020_COM_2025.xlsx)",
    "acces": "Fichier retraite transmis par Solagro (2026)",
    "frequence_maj": "~10 ans (cycle Recensement Agricole)",
    "licence": "Etalab 2.0 (RPG, RA) - retraitement Solagro",
    "theme": "Agriculture",
    "notes": (
        "Surfaces (SAU, cultures, prairies) : RPG 2020, valeurs communales "
        "directes. Cheptel (bovins, porcins, volailles, UGB) : RA 2020, "
        "retraite par Solagro a la maille EPCI puis reventile par commune au "
        "prorata de la SAU. Agregation ici sur le decoupage EPCI en vigueur "
        "(comme les autres sources), donc legere distorsion du cheptel pour "
        "les EPCI dont le perimetre a change (cf. docstring du module)."
    ),
    "indicateurs": [
        {"code": "SAU",                "label": "Superficie Agricole Utilisee",           "unite": "ha",  "methode_agregation": "Somme", "annees": "2020"},
        {"code": "CHEPTEL_BOVINS",     "label": "Cheptel bovins",                         "unite": "tetes", "methode_agregation": "Somme", "annees": "2020"},
        {"code": "CHEPTEL_PORCINS",    "label": "Cheptel porcins",                        "unite": "tetes", "methode_agregation": "Somme", "annees": "2020"},
        {"code": "CHEPTEL_VOLAILLES",  "label": "Cheptel volailles",                      "unite": "tetes", "methode_agregation": "Somme", "annees": "2020"},
        {"code": "CHEPTEL_UGB",        "label": "Cheptel total (UGB, tous elevages)",      "unite": "UGB", "methode_agregation": "Somme", "annees": "2020"},
        {"code": "CULT_SURF_TOTALE",   "label": "Surface cultivee (toutes cultures)",      "unite": "ha",  "methode_agregation": "Somme", "annees": "2020"},
        {"code": "CULT_SURF_NON_BIO",  "label": "Surface cultivee non bio",                "unite": "ha",  "methode_agregation": "Somme", "annees": "2020"},
        {"code": "SURF_PRAIRIES",      "label": "Surface en prairies",                     "unite": "ha",  "methode_agregation": "Somme", "annees": "2020"},
    ],
}

AGRESTE_XLSX = INPUT_DIR / "260716_RPG2020_RA2020_COM_2025.xlsx"
AGRESTE_SHEET = "RPG_RA2020_COM2025"

_TARGET_CODES = [
    "SAU", "CHEPTEL_BOVINS", "CHEPTEL_PORCINS", "CHEPTEL_VOLAILLES",
    "CHEPTEL_UGB", "CULT_SURF_TOTALE", "CULT_SURF_NON_BIO", "SURF_PRAIRIES",
]


def _load_communes() -> pd.DataFrame:
    """Charge et retraite le fichier communal RPG/RA Solagro."""
    df = pd.read_excel(AGRESTE_XLSX, sheet_name=AGRESTE_SHEET, dtype={"insee_com_2025": str})

    df = df.rename(columns={"insee_com_2025": "codgeo"})
    df["codgeo"] = df["codgeo"].str.zfill(5)

    df["SAU"] = df["sau_totale"]
    df["CHEPTEL_BOVINS"] = df["total_bovins"]
    df["CHEPTEL_PORCINS"] = df["total_porcins"]
    df["CHEPTEL_VOLAILLES"] = df["total_volailles"]
    df["CHEPTEL_UGB"] = df["total_ugb"]
    df["CULT_SURF_TOTALE"] = df["surf_totale_cultures"]
    df["CULT_SURF_NON_BIO"] = (df["surf_totale_cultures"] - df["surf_cultures_bio"]).clip(lower=0)
    df["SURF_PRAIRIES"] = df["surf_prairies"]

    return df[["codgeo"] + _TARGET_CODES]


def load_ra_epci(annee: int = 2020) -> pd.DataFrame:
    """
    Charge les données RPG/RA communales et agrège par EPCI (correspondance commune->EPCI en vigueur).

    Retourne un DataFrame indexé sur code_epci avec colonnes = _TARGET_CODES.
    """
    if annee != 2020:
        raise ValueError(
            f"Seul le millésime 2020 est disponible. Demandé : {annee}"
        )

    from sources.geo_epci import load_commune_epci_cog, millesime_epci

    cache_path = INTERIM_DIR / f"agreste_epci_2020_cog{millesime_epci()}.parquet"
    if cache_path.exists():
        log.info(f"Cache Agreste RPG/RA EPCI 2020: {cache_path.name}")
        return pd.read_parquet(cache_path)

    df = _load_communes()
    n_communes = len(df)

    corr = load_commune_epci_cog()
    merged = df.merge(corr[["codgeo", "code_epci"]], on="codgeo", how="inner")
    n_matched = merged["codgeo"].nunique()

    if n_matched < n_communes * 0.9:
        log.warning(
            f"Agreste RPG/RA : seulement {n_matched}/{n_communes} communes "
            f"trouvées dans la correspondance COG"
        )

    result = merged.groupby("code_epci")[_TARGET_CODES].sum()

    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    result.to_parquet(cache_path)
    log.info(
        f"Agreste RPG/RA EPCI 2020 : {len(result)} EPCI "
        f"({n_matched}/{n_communes} communes agrégées)"
    )
    return result
