"""
sources/insee_omphale.py
========================
Projections démographiques Omphale 2022 reconstituées au niveau EPCI.

Source : https://www.insee.fr/fr/statistiques/6652134
Licence : Licence Ouverte Etalab 2.0

INSEE publie Omphale au niveau départemental uniquement.
Les projections EPCI sont calculées par moyenne pondérée des taux de
croissance départementaux. Pour les EPCI à cheval sur plusieurs
départements, chaque département contribue proportionnellement à sa
part de la population de l'EPCI (pondération par population communale,
ou par nombre de communes si pop_communes n'est pas fourni).
"""

import logging
import re
from pathlib import Path

import pandas as pd
import requests

from config.settings import RAW_DIR, INTERIM_DIR, ANNEE_REF

OMPHALE_ANNEE_FIN: int = 2050
OMPHALE_PAS_ANNUEL: bool = True

log = logging.getLogger(__name__)

SOURCE_META = {
    "id": "INSEE_OMPHALE",
    "nom": "INSEE Omphale 2022 - Projections demographiques",
    "description": (
        "Projections de population au niveau departemental (scenario central). "
        "Reconstruction a l'echelle EPCI par moyenne ponderee des taux de "
        "croissance departementaux, ponderee par la part de population communale."
    ),
    "urls": "https://www.insee.fr/fr/statistiques/6652134",
    "niveau_geo": "EPCI (reconstruit depuis departement x commune)",
    "annees": "2020 - 2070 (parametrable, defaut 2050)",
    "format": "Excel (feuille detectee automatiquement)",
    "acces": "Automatique (scraping de la page INSEE)",
    "frequence_maj": "~5-10 ans",
    "licence": "Etalab 2.0",
    "theme": "Demographie (projections)",
    "notes": (
        "Seul le scenario central est retenu. "
        "Interpolation annuelle optionnelle (OMPHALE_PAS_ANNUEL). "
        "EPCI a cheval sur plusieurs departements : ponderation par population communale."
    ),
    "indicateurs": [
        {"code": "POP_PROJ_RATIO", "label": "Ratio d'evolution demographique", "unite": "ratio", "methode_agregation": "Moy. ponderee pop.", "annees": "2020-2070"},
    ],
}

_PAGE_URL = "https://www.insee.fr/fr/statistiques/6652134"


# ---------------------------------------------------------------------------
# Helpers géographiques
# ---------------------------------------------------------------------------

def _get_dep_from_codgeo(codgeo: str) -> str:
    """Extrait le code département depuis un code commune INSEE."""
    s = str(codgeo).strip()
    # Corse : codes alphanumériques "2Axxx" / "2Bxxx"
    if s.upper().startswith(("2A", "2B")):
        return s[:2].upper()
    # DOM : 971xx, 972xx, 973xx, 974xx, 976xx
    s5 = s.zfill(5)
    if s5.startswith("97"):
        return s5[:3]
    return s5[:2]


def _normalize_dep(code: str) -> str:
    """Normalise un code département (01-095, 2A, 2B, 971-976)."""
    s = str(code).strip().upper()
    if s in ("2A", "2B"):
        return s
    if re.match(r"^97[1-6]$", s):
        return s
    try:
        return f"{int(s):02d}"
    except ValueError:
        return s


# ---------------------------------------------------------------------------
# Téléchargement
# ---------------------------------------------------------------------------

def _download_omphale_dep(raw_dir: Path) -> Path:
    """
    Télécharge le fichier Excel Omphale départemental depuis INSEE.
    Scrape la page pour trouver le lien .xlsx, puis le met en cache.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = raw_dir / "omphale_dep.xlsx"
    if xlsx_path.exists():
        log.info(f"Cache Omphale dép: {xlsx_path.name}")
        return xlsx_path

    log.info(f"Recherche du lien Omphale sur {_PAGE_URL}...")
    r = requests.get(_PAGE_URL, timeout=60)
    r.raise_for_status()

    matches = re.findall(
        r'href="(/fr/statistiques/fichier/\d+/[^"]+\.xlsx)"',
        r.text,
        re.IGNORECASE,
    )
    if not matches:
        raise RuntimeError(
            f"Aucun lien .xlsx trouvé sur {_PAGE_URL}. "
            "La structure de la page INSEE a peut-être changé."
        )

    # Préférer un fichier dont le nom contient "dep" ou "dept"
    dep_matches = [m for m in matches if re.search(r"dep", m, re.IGNORECASE)]
    url_path = dep_matches[0] if dep_matches else matches[0]
    file_url = f"https://www.insee.fr{url_path}"

    log.info(f"Téléchargement depuis {file_url}...")
    r2 = requests.get(file_url, timeout=180)
    r2.raise_for_status()
    xlsx_path.write_bytes(r2.content)
    log.info(f"Sauvegardé : {xlsx_path}")
    return xlsx_path


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_omphale_dep(xlsx_path: Path) -> pd.DataFrame:
    """
    Parse le fichier Excel Omphale départemental.

    Stratégie :
      - Cherche une feuille dont le nom évoque le scénario central.
      - Détecte la colonne département et les colonnes années.
      - Si le fichier est désagrégé par âge/sexe, agrège par somme.

    Retourne un DataFrame : code_dep, annee, POP_DEP
    """
    xl = pd.ExcelFile(xlsx_path)
    log.debug(f"Feuilles disponibles : {xl.sheet_names}")

    # Sélectionner la feuille la plus pertinente
    # Priorité : feuille contenant les données départementales
    target = None
    for name in xl.sheet_names:
        nl = name.lower()
        if any(kw in nl for kw in ("population_dep", "pop_dep", "dep_", "_dep",
                                    "central", "ensemble", "total",
                                    "scenario 1", "scénario 1")):
            target = name
            break
    if target is None:
        # Ignorer les feuilles de documentation, prendre la première feuille de données
        data_sheets = [n for n in xl.sheet_names
                       if not any(kw in n.lower() for kw in ("doc", "notice", "lecture", "readme"))]
        target = data_sheets[0] if data_sheets else xl.sheet_names[0]
    log.info(f"Feuille Omphale : '{target}'")

    # --- Lecture avec détection automatique des lignes d'en-tête ---
    # Certains fichiers INSEE ont plusieurs lignes de métadonnées avant l'en-tête réel.
    # On cherche la première ligne dont une cellule contient "DEP" ou "CODGEO".
    df_raw = pd.read_excel(xlsx_path, sheet_name=target, header=None, dtype=str)
    header_row = None
    for i, row in df_raw.iterrows():
        vals = [str(v).strip().upper() for v in row.values if pd.notna(v)]
        if any(v in ("DEP", "DEPT", "CODE_DEP", "DEPARTEMENT", "CODGEO", "DEPART") for v in vals):
            header_row = i
            break
    if header_row is None:
        raise ValueError(
            f"Ligne d'en-tête introuvable dans la feuille '{target}'. "
            f"Premières lignes : {df_raw.iloc[:6, :3].to_string()}"
        )

    df = pd.read_excel(xlsx_path, sheet_name=target, header=header_row, dtype=str)
    log.debug(f"En-tête détecté à la ligne {header_row}. Colonnes : {list(df.columns[:10])}")

    # --- Colonne département ---
    dep_col = None
    for col in df.columns:
        cu = str(col).strip().upper()
        if cu in ("DEP", "DEPT", "CODE_DEP", "DEPARTEMENT", "CODGEO", "DEPART"):
            dep_col = col
            break
    if dep_col is None:
        # Heuristique : colonne dont les valeurs ressemblent à des codes dép
        for col in df.columns:
            vals = df[col].dropna().head(10).astype(str).str.strip()
            if vals.str.match(r"^\d{2,3}$|^2[AB]$").mean() > 0.8:
                dep_col = col
                break
    if dep_col is None:
        raise ValueError(
            f"Colonne département introuvable. Colonnes : {list(df.columns[:15])}"
        )

    df = df.rename(columns={dep_col: "code_dep"})
    df["code_dep"] = df["code_dep"].astype(str).str.strip().apply(_normalize_dep)
    # Supprimer les lignes sans code département valide (notes de bas de page, etc.)
    df = df[df["code_dep"].str.match(r"^\d{2,3}$|^2[AB]$", na=False)]

    # --- Colonnes années ---
    # Supporte à la fois les colonnes nommées "2020" et "POP_2020"
    annee_cols: dict[str, int] = {}
    for col in df.columns:
        s = str(col).strip()
        # Format direct : "2020"
        try:
            yr = int(s[:4])
            if 2000 <= yr <= 2100:
                annee_cols[col] = yr
                continue
        except (ValueError, TypeError):
            pass
        # Format préfixé : "POP_2020", "POP2020"
        m = re.search(r"(20\d{2})", s)
        if m:
            yr = int(m.group(1))
            if 2000 <= yr <= 2100:
                annee_cols[col] = yr

    if not annee_cols:
        raise ValueError(f"Aucune colonne année détectée dans '{target}'")

    for col in annee_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # --- Agrégation si le fichier est ventilé par âge/sexe ---
    age_like_cols = [
        c for c in df.columns
        if str(c).strip().upper() in ("AGE", "AGEQ", "AGE5", "TRANCHE_AGE", "SEXE", "SEX", "GENRE")
    ]
    if age_like_cols:
        age_col = age_like_cols[0]
        ensemble_vals = {"ensemble", "ens", "total", "tous", "0", "t", "e"}
        mask = df[age_col].astype(str).str.strip().str.lower().isin(ensemble_vals)
        if mask.any():
            df = df[mask]
        else:
            # Sommer toutes les tranches
            df = (
                df.groupby("code_dep")[list(annee_cols)].sum(numeric_only=True).reset_index()
            )

    # --- Mise en forme longue ---
    val_cols = list(annee_cols)
    df_long = df[["code_dep"] + val_cols].melt(
        id_vars="code_dep",
        value_vars=val_cols,
        var_name="_col",
        value_name="POP_DEP",
    )
    df_long["annee"]   = df_long["_col"].map(annee_cols)
    df_long["POP_DEP"] = pd.to_numeric(df_long["POP_DEP"], errors="coerce")
    df_long = df_long.dropna(subset=["POP_DEP"])[["code_dep", "annee", "POP_DEP"]]

    # --- Conversion milliers → habitants si nécessaire ---
    # Le fichier Omphale 2022 exprime la population en milliers
    raw_check = pd.read_excel(xlsx_path, sheet_name=target, header=None, dtype=str, nrows=6)
    is_milliers = any(
        "millier" in str(v).lower()
        for row in raw_check.values
        for v in row
        if pd.notna(v)
    )
    if is_milliers:
        df_long["POP_DEP"] = df_long["POP_DEP"] * 1000
        log.info("Omphale : valeurs converties de milliers vers habitants")

    log.info(
        f"Omphale dép : {df_long['code_dep'].nunique()} départements "
        f"× {df_long['annee'].nunique()} années"
    )
    return df_long


# ---------------------------------------------------------------------------
# Pondération EPCI → département
# ---------------------------------------------------------------------------

def _build_epci_dep_weights(
    pop_communes: pd.DataFrame | None = None,
    correspondance: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Calcule les poids département→territoire (EPCI par défaut, ou tout autre
    découpage commune→territoire fourni - EPT, Paris...).

    Paramètres
    ----------
    pop_communes : DataFrame (codgeo, population) - population communale
        pour la pondération. Si None, utilise une commune = 1 unité.
    correspondance : DataFrame (codgeo, code_epci) - découpage commune→territoire.
        Si None, charge la correspondance commune→EPCI complète (comportement
        historique). Permet de réutiliser ce mécanisme pour l'EPT ou Paris
        (mêmes colonnes, territoire nested dans un seul EPCI).

    Retourne
    --------
    DataFrame : code_epci, code_dep, poids  (sum(poids) = 1 par territoire)
    """
    if correspondance is None:
        from sources.geo_epci import load_commune_epci_cog
        correspondance = load_commune_epci_cog()

    cog = correspondance[["codgeo", "code_epci"]].copy()
    cog["code_dep"] = cog["codgeo"].apply(_get_dep_from_codgeo)

    if pop_communes is not None:
        pop = pop_communes[["codgeo", "population"]].copy()
        pop["population"] = pd.to_numeric(pop["population"], errors="coerce").fillna(0)
        cog = cog.merge(pop, on="codgeo", how="left")
        cog["population"] = cog["population"].fillna(0)
    else:
        log.debug("pop_communes non fourni : pondération par nombre de communes")
        cog["population"] = 1.0

    w = cog.groupby(["code_epci", "code_dep"])["population"].sum().reset_index()
    totaux = w.groupby("code_epci")["population"].sum().rename("total")
    w = w.merge(totaux, on="code_epci")
    w["poids"] = w["population"] / w["total"]
    return w[["code_epci", "code_dep", "poids"]]


# ---------------------------------------------------------------------------
# Fonction principale
# ---------------------------------------------------------------------------

def load_omphale(
    raw_dir: Path = RAW_DIR / "insee",
    annee_ref: int = ANNEE_REF,
    pop_communes: pd.DataFrame | None = None,
    correspondance: pd.DataFrame | None = None,
    cache_suffix: str = "epci",
) -> pd.DataFrame:
    """
    Projections de population Omphale reconstituées au niveau EPCI (ou tout
    autre découpage commune→territoire fourni - EPT, Paris).

    Paramètres
    ----------
    raw_dir : répertoire de stockage des fichiers bruts téléchargés.
    annee_ref : année de référence pour le calcul du ratio de croissance.
    pop_communes : DataFrame (codgeo, population) utilisé pour pondérer
        les territoires à cheval sur plusieurs départements. Si None,
        pondération par nombre de communes.
    correspondance : DataFrame (codgeo, code_epci[, nom_epci]) - découpage
        commune→territoire. Si None, correspondance commune→EPCI complète
        (comportement historique). Permet de réutiliser ce mécanisme pour
        l'EPT ou Paris (mêmes colonnes que le retour, code_epci porte alors
        un code EPT/commune).
    cache_suffix : distingue le cache selon le découpage utilisé ("epci" par
        défaut ; ex. "ept", "paris") - sans quoi un appel EPT écraserait le
        cache EPCI ou inversement.

    Retourne
    --------
    DataFrame long : code_epci, annee, POP_PROJ_RATIO
        POP_PROJ_RATIO = population projetée / population de référence (annee_ref).
        POP_PROJ est omis (non disponible sans population EPCI de référence).
    """
    cache_path = INTERIM_DIR / f"omphale_{cache_suffix}.parquet"
    if cache_path.exists() and pop_communes is None:
        log.info(f"Cache Omphale : {cache_path.name}")
        return pd.read_parquet(cache_path)

    # 1. Données départementales
    xlsx_path = _download_omphale_dep(raw_dir)
    df_dep    = _parse_omphale_dep(xlsx_path)

    # 2. Ratio de croissance par département / annee_ref
    annees_dispo = sorted(df_dep["annee"].unique())
    if annee_ref not in annees_dispo:
        # Fallback : année disponible la plus proche
        annee_ref_eff = min(annees_dispo, key=lambda y: abs(y - annee_ref))
        log.warning(
            f"Année de référence {annee_ref} absente des données Omphale. "
            f"Utilisation de {annee_ref_eff} comme référence."
        )
    else:
        annee_ref_eff = annee_ref

    pop_ref = (
        df_dep[df_dep["annee"] == annee_ref_eff][["code_dep", "POP_DEP"]]
        .rename(columns={"POP_DEP": "POP_REF"})
    )
    df_dep = df_dep.merge(pop_ref, on="code_dep", how="left")
    df_dep["RATIO_DEP"] = df_dep["POP_DEP"] / df_dep["POP_REF"]

    # 3. Poids territoire × département
    weights = _build_epci_dep_weights(pop_communes, correspondance)

    # 4. Moyenne pondérée des ratios départementaux → ratio EPCI
    df = df_dep[["code_dep", "annee", "RATIO_DEP"]].merge(weights, on="code_dep", how="inner")
    df_epci = (
        df.assign(contrib=df["RATIO_DEP"] * df["poids"])
        .groupby(["code_epci", "annee"])["contrib"]
        .sum()
        .reset_index(name="POP_PROJ_RATIO")
    )

    # 5. Filtrer [annee_ref, OMPHALE_ANNEE_FIN] et interpoler au pas annuel si demandé
    df_epci = df_epci[
        (df_epci["annee"] >= annee_ref_eff) & (df_epci["annee"] <= OMPHALE_ANNEE_FIN)
    ].copy()

    if OMPHALE_PAS_ANNUEL:
        annees_cibles = list(range(annee_ref_eff, OMPHALE_ANNEE_FIN + 1))
        df_wide = df_epci.pivot(index="annee", columns="code_epci", values="POP_PROJ_RATIO")
        df_wide = df_wide.reindex(annees_cibles).interpolate(method="linear")
        df_epci = (
            df_wide.reset_index()
            .melt(id_vars="annee", var_name="code_epci", value_name="POP_PROJ_RATIO")
        )
        log.info(f"Omphale : interpolation linéaire → {len(annees_cibles)} années annuelles")

    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    df_epci.to_parquet(cache_path, index=False)
    log.info(
        f"Omphale : {df_epci['code_epci'].nunique()} EPCI "
        f"× {df_epci['annee'].nunique()} années"
    )
    return df_epci
