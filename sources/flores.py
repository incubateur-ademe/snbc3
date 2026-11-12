"""
sources/flores.py
=================
Extraction des emplois industriels par secteur depuis FLORES (INSEE).

Source : https://www.insee.fr/fr/statistiques/4991205
  "Postes salariés des établissements actifs fin {année} par secteur d'activité
  en A88, par commune"

Fichier distribué : ZIP contenant un CSV (séparateur ';'), format large :
  CODGEO ; EFF_TOT ; EFF_01 ; EFF_02 ; … ; EFF_99
  - CODGEO   : code commune INSEE (5 chiffres)
  - EFF_TOT  : total postes salariés toutes activités
  - EFF_XX   : postes salariés pour la division NAF A88 numéro XX

Nomenclature du fichier : TD_FLORES{année}_NA88_NBSAL_CSV.zip
  - NA88  : découpage en 88 divisions NAF rév.2
  - NBSAL : nombre de postes salariés (≠ NBETAB = nombre d'établissements)

Correspondance A88 → indicateurs catalogue (issue de Correspondances FLORES.xlsx) :
  EMP_IND_METAUX  ← EFF_07 (minerais métalliques), EFF_24 (métallurgie)
  EMP_IND_IAA     ← EFF_10 (ind. alimentaires), EFF_11 (boissons)
  EMP_IND_PAPIER  ← EFF_17 (papier et carton)
  EMP_IND_CHIMIE  ← EFF_19 (cokéfaction/raffinage), EFF_20 (chimie), EFF_21 (pharma)
  EMP_IND_NONMET  ← EFF_22 (caoutchouc/plastique), EFF_23 (minéraux non métal.)
  EMP_IND_EQUIP   ← EFF_26 (inform./élec./optique), EFF_27 (équip. élec.),
                     EFF_28 (machines), EFF_29 (automobile), EFF_30 (autres transports),
                     EFF_31 (meubles), EFF_32 (autres manuf.)
  EMP_IND_CONST   ← EFF_41 (génie civil), EFF_42 (travaux de construction spécialisés),
                     EFF_43 (construction de bâtiments)
  EMP_IND_AUTRES  ← autres secteurs industriels : extraction (EFF_05-09),
                     tabac/textile/bois/imprimerie (EFF_12-16, EFF_18),
                     produits métalliques (EFF_25), réparation (EFF_33),
                     énergie/eau/déchets (EFF_35-39)

Note secret statistique :
  Les cellules masquées par le secret statistique ont la valeur "s" dans le
  CSV FLORES. Elles sont chargées comme NaN, et tracées séparément dans un
  DataFrame "secret" (booléen, même forme que le DataFrame de valeurs) :
  un indicateur EMP_IND_* est marqué secret pour une commune si au moins une
  des colonnes EFF_XX qui le composent est masquée dans le fichier source.
"""

import logging
import zipfile
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests

from config.settings import RAW_DIR, INTERIM_DIR

log = logging.getLogger(__name__)

SOURCE_META = {
    "id": "FLORES",
    "nom": "INSEE FLORES - Postes salaries par secteur d'activite (NAF 88 divisions)",
    "description": (
        "Effectifs salaries par commune et par secteur d'activite NAF rev.2 "
        "(88 divisions). Agregation en 8 filieres industrielles : metaux, "
        "chimie, mineraux non-metalliques, IAA, equipements, papier, "
        "construction, autres industries."
    ),
    "urls": "https://www.insee.fr/fr/statistiques/4991205",
    "niveau_geo": "Commune - agregation EPCI",
    "annees": "2017, 2018, 2019, 2020, 2021",
    "format": "ZIP + CSV (separateur ;)",
    "acces": "Automatique (telechargement)",
    "frequence_maj": "Annuelle",
    "licence": "Etalab 2.0",
    "theme": "Industrie",
    "notes": (
        "EMP_IND_METAUX <- EFF_07+EFF_24 | EMP_IND_IAA <- EFF_10+EFF_11 | "
        "EMP_IND_PAPIER <- EFF_17 | EMP_IND_CHIMIE <- EFF_19+EFF_20+EFF_21 | "
        "EMP_IND_NONMET <- EFF_22+EFF_23 | EMP_IND_EQUIP <- EFF_26-32 | "
        "EMP_IND_CONST <- EFF_41-43 | EMP_IND_AUTRES <- secteurs restants."
    ),
    "indicateurs": [
        {"code": "EMP_IND_METAUX", "label": "Emplois industrie metallurgie",      "unite": "emplois", "methode_agregation": "Somme", "annees": "2017-2021"},
        {"code": "EMP_IND_CHIMIE", "label": "Emplois industrie chimique",          "unite": "emplois", "methode_agregation": "Somme", "annees": "2017-2021"},
        {"code": "EMP_IND_NONMET", "label": "Emplois mineraux non-metalliques",    "unite": "emplois", "methode_agregation": "Somme", "annees": "2017-2021"},
        {"code": "EMP_IND_IAA",    "label": "Emplois industrie agro-alimentaire",  "unite": "emplois", "methode_agregation": "Somme", "annees": "2017-2021"},
        {"code": "EMP_IND_EQUIP",  "label": "Emplois industrie equipements",       "unite": "emplois", "methode_agregation": "Somme", "annees": "2017-2021"},
        {"code": "EMP_IND_PAPIER", "label": "Emplois industrie papier-carton",     "unite": "emplois", "methode_agregation": "Somme", "annees": "2017-2021"},
        {"code": "EMP_IND_CONST",  "label": "Emplois construction",               "unite": "emplois", "methode_agregation": "Somme", "annees": "2017-2021"},
        {"code": "EMP_IND_AUTRES", "label": "Emplois autres industries",           "unite": "emplois", "methode_agregation": "Somme", "annees": "2017-2021"},
    ],
}

# ---------------------------------------------------------------------------
# Correspondance ET_XX → code indicateur
# Source : Correspondances FLORES.xlsx (onglet "Correspondance secteurs")
# Seuls les secteurs industriels (divisions 05-43) sont pris en compte.
# ---------------------------------------------------------------------------
_IND_COLS: dict[str, list[str]] = {
    "EMP_IND_METAUX": ["EFF_07", "EFF_24"],
    "EMP_IND_IAA":    ["EFF_10", "EFF_11"],
    "EMP_IND_PAPIER": ["EFF_17"],
    "EMP_IND_CHIMIE": ["EFF_19", "EFF_20", "EFF_21"],
    "EMP_IND_NONMET": ["EFF_22", "EFF_23"],
    "EMP_IND_EQUIP":  ["EFF_26", "EFF_27", "EFF_28", "EFF_29", "EFF_30", "EFF_31", "EFF_32"],
    "EMP_IND_CONST":  ["EFF_41", "EFF_42", "EFF_43"],
    "EMP_IND_AUTRES": [
        "EFF_05", "EFF_06", "EFF_08", "EFF_09",
        "EFF_12", "EFF_13", "EFF_14", "EFF_15", "EFF_16",
        "EFF_18",
        "EFF_25", "EFF_33",
        "EFF_35",
        "EFF_36", "EFF_37", "EFF_38", "EFF_39",
    ],
}

_ALL_EFF_COLS: set[str] = {c for cols in _IND_COLS.values() for c in cols}

# ---------------------------------------------------------------------------
# URLs de téléchargement par millésime
# À mettre à jour lors de la publication d'un nouvel exercice FLORES.
# Page de référence : https://www.insee.fr/fr/statistiques/4991205
# Les IDs de fichier (premier segment numérique) changent à chaque édition.
# ---------------------------------------------------------------------------
_FLORES_BASE = "https://www.insee.fr/fr/statistiques/fichier/4991205"
_FLORES_URLS: dict[int, str] = {
    yr: f"{_FLORES_BASE}/TD_FLORES{yr}_NA88_NBSAL_CSV.zip"
    for yr in [2017, 2018, 2019, 2020, 2021]
}


def download_flores(annee: int, raw_dir: Path = RAW_DIR / "flores") -> Path:
    """
    Télécharge le fichier FLORES (ZIP+CSV) pour l'année donnée depuis insee.fr.
    Retourne le chemin du .zip téléchargé.
    Si le fichier est déjà présent, ne re-télécharge pas.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / f"flores_{annee}.zip"

    if out_path.exists():
        log.info(f"FLORES {annee} déjà en cache : {out_path.name}")
        return out_path

    url = _FLORES_URLS.get(annee)
    if url is None:
        raise ValueError(
            f"URL non configurée pour FLORES {annee}. "
            f"Années disponibles : {sorted(_FLORES_URLS)}. "
            f"Mettre à jour _FLORES_URLS dans sources/flores.py, "
            f"ou déposer manuellement le fichier sous {out_path}."
        )

    log.info(f"Téléchargement FLORES {annee} depuis {url} ...")
    r = requests.get(url, timeout=120, stream=True)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
    log.info(f"Téléchargé : {out_path.name} ({out_path.stat().st_size / 1e6:.1f} Mo)")
    return out_path


def load_flores(
    annee: int,
    raw_dir: Path = RAW_DIR / "flores",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Charge FLORES pour l'année donnée et retourne les indicateurs industriels
    agrégés par commune selon la correspondance A88 → EMP_IND_*.

    Retourne (df_valeurs, df_secret) - deux DataFrames indexés sur CODGEO
    (str, 5 chiffres), colonnes = codes indicateurs du catalogue
    (EMP_IND_METAUX, …) :
      - df_valeurs : valeurs numériques (NaN si absentes ou masquées)
      - df_secret  : booléen, True si l'indicateur est masqué par le secret
                     statistique pour cette commune (au moins une colonne
                     EFF_XX sous-jacente valait "s" dans le fichier source)

    Si le ZIP n'existe pas localement, tente un téléchargement automatique.
    Résultat mis en cache Parquet dans INTERIM_DIR.
    """
    cache_path        = INTERIM_DIR / f"flores_communes_{annee}.parquet"
    cache_path_secret = INTERIM_DIR / f"flores_communes_{annee}_secret.parquet"
    if cache_path.exists() and cache_path_secret.exists():
        log.info(f"Cache FLORES {annee} : {cache_path.name}")
        return pd.read_parquet(cache_path), pd.read_parquet(cache_path_secret)

    zip_path = raw_dir / f"flores_{annee}.zip"
    if not zip_path.exists():
        log.info(f"Fichier FLORES {annee} absent, tentative de téléchargement...")
        zip_path = download_flores(annee, raw_dir)

    log.info(f"Chargement FLORES {annee} depuis {zip_path.name} ...")
    with zipfile.ZipFile(zip_path) as z:
        csv_names = [n for n in z.namelist() if n.lower().endswith(".csv") and "meta" not in n.lower()]
        if not csv_names:
            raise FileNotFoundError(
                f"Aucun CSV de données trouvé dans {zip_path.name}. "
                f"Contenu : {z.namelist()}"
            )
        with z.open(csv_names[0]) as f:
            df = pd.read_csv(
                BytesIO(f.read()),
                sep=";",
                dtype=str,
                # "s" (secret statistique) gardé en littéral pour détection
                # ci-dessous, avant conversion numérique (cf. note module).
                na_values=["nd", ""],
            )

    # --- Normalisation CODGEO ---
    codgeo_col = next(
        (c for c in df.columns if c.strip().upper() in ("CODGEO", "COM", "CODE_INSEE")),
        None,
    )
    if codgeo_col is None:
        raise ValueError(
            f"Colonne CODGEO introuvable dans {zip_path.name}. "
            f"Colonnes : {list(df.columns[:15])}"
        )
    df = df.rename(columns={codgeo_col: "CODGEO"})
    df["CODGEO"] = df["CODGEO"].astype(str).str.zfill(5)
    df = df.set_index("CODGEO")

    # --- Vérification colonnes ET_XX ---
    present = _ALL_EFF_COLS & set(df.columns)
    missing = _ALL_EFF_COLS - present
    if not present:
        raise ValueError(
            f"Aucune colonne ET_XX trouvée dans {zip_path.name}. "
            f"Colonnes disponibles : {list(df.columns[:20])}"
        )
    if missing:
        log.warning(f"FLORES {annee} : {len(missing)} colonnes absentes → {sorted(missing)}")

    # --- Détection secret statistique (avant conversion numérique) ---
    # "s" = cellule masquée par le secret statistique dans le fichier source.
    df_secret_eff = df[list(present)].eq("s")

    # --- Conversion numérique --- ("s" et "nd" deviennent NaN via coerce)
    for col in present:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # --- Agrégation A88 → indicateurs ---
    df_out = pd.DataFrame(index=df.index)
    df_out_secret = pd.DataFrame(index=df.index)
    for ind_code, et_cols in _IND_COLS.items():
        cols_ok = [c for c in et_cols if c in df.columns]
        if cols_ok:
            df_out[ind_code] = df[cols_ok].sum(axis=1, min_count=1)
            df_out_secret[ind_code] = df_secret_eff[cols_ok].any(axis=1)
        else:
            df_out[ind_code] = float("nan")
            df_out_secret[ind_code] = False

    n_secret = int(df_out_secret.to_numpy().sum())
    if n_secret:
        log.info(
            f"FLORES {annee} : {n_secret} valeurs commune×indicateur masquées "
            f"par le secret statistique"
        )

    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    df_out.to_parquet(cache_path)
    df_out_secret.to_parquet(cache_path_secret)
    log.info(f"FLORES {annee} : {len(df_out)} communes → {cache_path.name}")
    return df_out, df_out_secret
