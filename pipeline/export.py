"""
pipeline/export.py
==================
Export de la BDD au format long (Parquet) et au format large (XLSX
compatible avec l'outil de territorialisation SNBC Trajectoire GES de
référence).

Double sortie :
  1. bdd_epci.parquet  - format long tidy, une ligne par indicateur × EPCI × année
  2. bdd_epci_wide.xlsx - format large EPCI en lignes, colonnes = CODE_ANNEE
                          compatible avec l'onglet EPCI de Trajectoire GES de référence-V2.X.xlsx

Le format long a été un CSV jusqu'à l'intégration de l'API à la demande
(cf. api/) : Parquet depuis, plus rapide à charger et sans l'inférence de
dtype fragile d'un CSV de 13 Mo - la lecture humaine du format long, rare en
pratique, passe par le XLSX (bdd_epci_wide.xlsx) ou un notebook.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from config.settings import OUTPUT_DIR

log = logging.getLogger(__name__)

COLONNES_LONG = [
    "code_epci", "nom_epci", "niveau_geo", "zone", "code_region", "code_departement",
    "code_indicateur", "annee", "valeur", "unite", "source", "date_extraction", "qualite",
]


def to_xlsx_legacy(
    df_long: pd.DataFrame,
    annees: list[int] | None = None,
    output_dir: Path = OUTPUT_DIR,
) -> Path:
    """
    Exporte la BDD au format large XLSX compatible avec l'outil SNBC.

    Structure :
      - Lignes  : EPCI (code + nom) + EPT et Paris (niveau_geo EPT/COMMUNE,
                  subdivisions de la Métropole du Grand Paris) + lignes FRANCE / HEXAGONE
      - Colonnes: code_epci, nom_epci, puis pour chaque (indicateur, année) :
                  une colonne nommée CODE_ANNEE (ex: POP_2020, DJU_CHAUD_COSTIC_2020)

    Tri des colonnes : par thématique (selon le catalogue), puis par indicateur, puis par année.

    Les EPT et Paris sont sélectionnables dans l'outil standalone au même
    titre qu'un EPCI, mais leur couverture d'indicateurs est partielle
    (agriculture/transport/industrie/emplois seulement - pas de résidentiel,
    tertiaire, DJU ni énergie-climat à cette maille) : l'onglet « Territoire »
    du classeur signale visuellement (`_warn_valeur_territoire_absente`) les
    pivots/recalages sans donnée pour la sélection, plutôt que de laisser
    passer un zéro silencieux.

    Paramètres :
      df_long : BDD au format long
      annees  : liste d'années à inclure (None = toutes)
    """
    from pipeline.aggregate import load_catalogue

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "bdd_epci_wide.xlsx"

    if annees:
        # Les sources multi-années (Omphale, Agreste, calcul_derive) doivent toutes être incluses
        # quelle que soit l'année RP demandée.
        sources_multi_annees = {"INSEE_OMPHALE", "AGRESTE_RA"}
        mask_annee = df_long["annee"].isin(annees)
        mask_multi = df_long["source"].isin(sources_multi_annees)
        df = df_long[mask_annee | mask_multi].copy()
    else:
        df = df_long.copy()

    # Créer la colonne de pivot CODE_ANNEE
    df["col_wide"] = df["code_indicateur"] + "_" + df["annee"].astype(str)

    # Colonnes de métadonnées géographiques (fixes, hors pivot)
    geo_cols = [c for c in ("zone", "code_region", "code_departement", "niveau_geo") if c in df.columns]
    geo_meta = (
        df[["code_epci"] + geo_cols]
        .drop_duplicates("code_epci")
        .set_index("code_epci")
    )

    # Pivot
    df_wide = df.pivot_table(
        index=["code_epci", "nom_epci"],
        columns="col_wide",
        values="valeur",
        aggfunc="first",
    ).reset_index()
    df_wide.columns.name = None

    # Réintégrer les métadonnées géographiques
    df_wide = df_wide.join(geo_meta, on="code_epci")

    # Tri des colonnes selon le catalogue (thématique → code → année)
    catalogue = load_catalogue()
    theme_order = ["territoire", "demographie", "logement", "economie",
                   "industrie", "agriculture", "climatique"]
    code_order = {}
    for i, ind in enumerate(catalogue):
        theme_idx = theme_order.index(ind["thematique"]) if ind["thematique"] in theme_order else 99
        code_order[ind["code"]] = (theme_idx, i)

    def sort_key(col_name: str) -> tuple:
        if col_name in ("code_epci", "nom_epci"):
            return (-1, 0, col_name, 0)
        parts = col_name.rsplit("_", 1)
        if len(parts) == 2 and parts[1].isdigit():
            code, yr = parts[0], int(parts[1])
        else:
            code, yr = col_name, 0
        theme_i, code_i = code_order.get(code, (99, 999))
        return (theme_i, code_i, code, yr)

    meta_cols = ["code_epci", "nom_epci"] + [c for c in geo_cols if c in df_wide.columns]
    sorted_cols = meta_cols + sorted(
        [c for c in df_wide.columns if c not in meta_cols],
        key=sort_key,
    )
    df_wide = df_wide[[c for c in sorted_cols if c in df_wide.columns]]

    # Ordre des lignes : EPCI d'abord (triés par code), puis agrégats
    from config.settings import CODES_TOTAUX as _aggregates
    epci_rows = df_wide[~df_wide["code_epci"].isin(_aggregates)].sort_values("code_epci")
    total_rows = df_wide[df_wide["code_epci"].isin(_aggregates)]
    df_wide = pd.concat([epci_rows, total_rows], ignore_index=True)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df_wide.to_excel(writer, sheet_name="EPCI", index=False)

        # Formater en Table Excel nommée "EPCI"
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.table import Table, TableStyleInfo

        ws = writer.sheets["EPCI"]
        n_rows, n_cols = df_wide.shape
        table_ref = f"A1:{get_column_letter(n_cols)}{n_rows + 1}"
        tbl = Table(displayName="EPCI", ref=table_ref)
        tbl.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium9", showFirstColumn=False,
            showLastColumn=False, showRowStripes=True, showColumnStripes=False,
        )
        ws.add_table(tbl)

        # Onglet catalogue
        cat_rows = [
            {
                "code": ind["code"],
                "libelle": ind["libelle"],
                "unite": ind.get("unite", ""),
                "source": ind.get("source", ""),
                "thematique": ind.get("thematique", ""),
                "methode": ind.get("methode", ""),
            }
            for ind in catalogue
        ]
        pd.DataFrame(cat_rows).to_excel(writer, sheet_name="Catalogue", index=False)

    log.info(
        f"XLSX wide exporté: {out_path} "
        f"({len(df_wide)} EPCI × {len(df_wide.columns)-2} colonnes indicateurs)"
    )
    return out_path


def to_territorialise_csv(
    df_terr: pd.DataFrame, output_dir: Path = OUTPUT_DIR, csv: bool = False
) -> tuple[Path | None, Path]:
    """
    Exporte les résultats territorialisés (brut + recalé) au format long.

    Le **parquet est toujours écrit** : c'est la sortie de référence, celle que
    lisent les scripts de contrôle. Le CSV est optionnel (`csv=True`) car il
    pèse une quinzaine de fois plus pour les mêmes données - environ 545 Mo sur
    les deux zones - pour un usage ponctuel de relecture manuelle.

    Retourne (csv_path ou None, parquet_path).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = output_dir / "resultats_territorialises.parquet"
    df_terr.to_parquet(parquet_path, index=False)

    csv_path = None
    if csv:
        csv_path = output_dir / "resultats_territorialises.csv"
        df_terr.to_csv(csv_path, index=False, encoding="utf-8-sig", sep=";")

    log.info(
        f"Résultats territorialisés exportés: {parquet_path} "
        f"({len(df_terr)} lignes)" + (f" + {csv_path.name}" if csv_path else "")
    )
    return csv_path, parquet_path


def to_pivot_parquet(
    df_long: pd.DataFrame,
    annee: int,
    output_dir: Path = OUTPUT_DIR,
) -> tuple[Path, Path]:
    """
    Publie la BDD long (données pivot, EPCI + EPT/Paris + lignes agrégées de
    zone) en Parquet - format long de référence, cf. docstring du module.
    Chargé à froid par le service à la demande (cf. api/pivot_store.py),
    sans dépendre du réseau ni relancer `build_bdd()` à chaque démarrage.

    Écrit, à plat dans `output_dir` (comme `bdd_epci_wide.xlsx` - toujours
    écrasés, pas de sous-dossier ni de version datée : rien n'exige encore
    de faire coexister plusieurs versions) :
      - bdd_epci.parquet : le DataFrame long (colonnes `COLONNES_LONG`)
      - bdd_epci_manifest.json : métadonnées (date de génération, année de
        référence, nombre de lignes/territoires) pour `GET /version`

    Retourne (parquet_path, manifest_path).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    parquet_path = output_dir / "bdd_epci.parquet"
    df_export = df_long[[c for c in COLONNES_LONG if c in df_long.columns]]
    df_export.to_parquet(parquet_path, index=False)

    manifest = {
        "annee_ref": annee,
        "date_generation": datetime.now(timezone.utc).isoformat(),
        "n_lignes": len(df_long),
        "n_territoires": int(df_long["code_epci"].nunique()),
    }
    manifest_path = output_dir / "bdd_epci_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    log.info(f"Pivot Parquet publié: {parquet_path} ({len(df_long)} lignes)")
    return parquet_path, manifest_path


def export_all(
    df_long: pd.DataFrame,
    annees: list[int] | None = None,
    output_dir: Path = OUTPUT_DIR,
) -> tuple[Path, Path]:
    """
    Lance les deux exports (Parquet long + XLSX wide) en une seule commande.
    Retourne (parquet_path, xlsx_path).
    """
    # Dédupliquer : clé primaire = (code_epci, code_indicateur, annee)
    # Agreste et Omphale peuvent apparaître en double si build_bdd est appelé
    # pour plusieurs années (ils produisent des données hors de l'année RP).
    n_before = len(df_long)
    df_long = df_long.drop_duplicates(
        subset=["code_epci", "code_indicateur", "annee"], keep="first"
    )
    n_dup = n_before - len(df_long)
    if n_dup > 0:
        log.info(f"Déduplication : {n_dup} lignes supprimées")

    # Année de référence du manifeste : la première demandée, comme le reste
    # du pipeline (cf. main.py, ex. cmd_full utilise args.annee[0]).
    from config.settings import ANNEE_REF
    annee_ref = annees[0] if annees else ANNEE_REF

    parquet_path, _manifest_path = to_pivot_parquet(df_long, annee_ref, output_dir)
    xlsx_path = to_xlsx_legacy(df_long, annees, output_dir)
    return parquet_path, xlsx_path
