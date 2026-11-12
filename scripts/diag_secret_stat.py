"""
scripts/diag_secret_stat.py
============================
Diagnostic du secret statistique dans les sources de données.

Trois sources portent un mécanisme de masquage identifié par lecture du
code (pas supposé) :
  - FLORES        : cellules "s" dans le CSV communal INSEE (cf. sources/flores.py)
  - INSEE_RP      : cellules "s" dans le CSV communal INSEE (cf. sources/insee_rp.py),
                    alimente notamment EMP_CTS/EMP_APESAS -> EMP_TER (tertiaire)
  - ENERGIE_CLIMAT: colonne "confidentiel" dans les CSV OREC (cf. sources/energie_climat.py),
                    alimente notamment EC_CONSO_TERTIAIRE/EC_EMIS_TERTIAIRE (tertiaire)

Pour ces trois sources, les modules sources/*.py renvoient désormais
(df_valeurs, df_qualite) - le masquage est réellement tracé (qualite=
"secret_stat") jusque dans bdd_epci.parquet, plutôt que simplement documenté.
Ce script en mesure l'ampleur : taux de communes/EPCI touchés, par indicateur
et par année, avant et après agrégation.

Pour les autres sources du pipeline, ce script vérifie ce qu'il peut
vérifier empiriquement (PARC_VEHICULES : lecture brute en chaînes de
caractères, recherche de codes non numériques inattendus) et documente,
pour celles où le mécanisme ne s'applique structurellement pas
(MOBILITE_DT, CITEPA_IGT, METEO_FRANCE, OMPHALE, ALDO, AGRESTE), la raison
établie par lecture du code - plutôt que de les ignorer silencieusement.

Ce script n'est PAS un test : aucune assertion, code de sortie toujours 0.

Usage :
    uv run python scripts/diag_secret_stat.py --annees 2019 2020 2021
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _diag_source_communale(df_val, df_secret, annee, corr):
    """
    Mesure le masquage secret statistique d'une source communale (FLORES,
    INSEE_RP), avant agrégation (commune) et après (EPCI - secret_stat dès
    qu'une commune membre est masquée, cf.
    pipeline/aggregate.py::aggregate_communes_to_epci).

    df_val, df_secret : DataFrames indexés sur le même code commune (CODGEO),
    colonnes = indicateurs. corr : DataFrame (codgeo, code_epci).
    """
    import pandas as pd

    if df_val.empty:
        return pd.DataFrame()

    n_communes_dataset = len(df_val)
    idx_name = df_secret.index.name or "codgeo"
    df_s = df_secret.reset_index().rename(columns={idx_name: "codgeo"})
    df_s["codgeo"] = df_s["codgeo"].astype(str)
    df_s = df_s.merge(corr, on="codgeo", how="inner")

    rows = []
    for col in df_val.columns:
        if col not in df_secret.columns:
            continue
        n_secret_communes = int(df_secret[col].sum())
        if n_secret_communes == 0:
            continue
        epci_secret = df_s.groupby("code_epci")[col].any()
        n_epci_total = len(epci_secret)
        n_epci_secret = int(epci_secret.sum())
        rows.append({
            "annee": annee,
            "indicateur": col,
            "n_communes_masquees": n_secret_communes,
            "n_communes_total": n_communes_dataset,
            "taux_masquage_commune_pct": round(100 * n_secret_communes / n_communes_dataset, 3),
            "n_epci_touches": n_epci_secret,
            "n_epci_total": n_epci_total,
            "taux_epci_touches_pct": round(100 * n_epci_secret / n_epci_total, 2) if n_epci_total else float("nan"),
        })
    return pd.DataFrame(rows)


def _diag_energie_climat(annee):
    """
    Mesure le masquage secret statistique (colonne 'confidentiel' OREC) sur
    ENERGIE_CLIMAT, déjà à l'échelle EPCI (pas d'agrégation commune→EPCI).
    """
    import pandas as pd
    from sources.energie_climat import extract_indicators

    df_val, df_qual = extract_indicators(annee)
    if df_val.empty:
        return pd.DataFrame()

    rows = []
    for col in df_val.columns:
        n_total = int(df_qual[col].notna().sum())
        if n_total == 0:
            continue
        n_secret = int((df_qual[col] == "secret_stat").sum())
        if n_secret == 0:
            continue
        rows.append({
            "annee": annee,
            "indicateur": col,
            "n_epci_touches": n_secret,
            "n_epci_total": n_total,
            "taux_epci_touches_pct": round(100 * n_secret / n_total, 2),
        })
    return pd.DataFrame(rows)


def _audit_parc_vehicules(annee_test):
    """
    PARC_VEHICULES (SDES) n'a pas de mécanisme de secret statistique connu -
    vérification empirique plutôt que supposition : lecture brute en chaînes
    de caractères de la colonne PARC_{annee}, recherche de tout code non
    numérique qui aurait échappé au pd.to_numeric(errors='coerce') actuel
    (sources/parc_vehicules.py), lequel convertirait silencieusement un
    éventuel code de masquage en 0.
    """
    import pandas as pd
    from sources.parc_vehicules import _download_csv

    parc_col = f"PARC_{annee_test}"
    try:
        csv_path = _download_csv()
    except Exception as e:
        print(f"  PARC_VEHICULES : téléchargement impossible ({e}), vérification ignorée.")
        return

    df = pd.read_csv(csv_path, sep=";", dtype=str, low_memory=False)
    col_match = next((c for c in df.columns if c.strip().upper() == parc_col), None)
    if col_match is None:
        print(f"  PARC_VEHICULES : colonne {parc_col} introuvable, vérification ignorée.")
        return

    vals = df[col_match].dropna()
    vals = vals[vals.str.strip() != ""]
    non_numeriques = vals[pd.to_numeric(vals, errors="coerce").isna()]
    tokens = sorted(non_numeriques.unique())
    if tokens:
        print(
            f"  PARC_VEHICULES {annee_test} ({parc_col}) : {len(tokens)} code(s) non "
            f"numérique(s) trouvé(s) -> {tokens[:20]} - actuellement convertis en 0 "
            f"silencieusement (pd.to_numeric(errors='coerce').fillna(0.0)), à investiguer."
        )
    else:
        print(
            f"  PARC_VEHICULES {annee_test} ({parc_col}) : aucun code non numérique "
            f"trouvé sur {len(vals):,} valeurs - pas de masquage détecté."
        )


def _audit_autres_sources(annees):
    print(f"\n{'=' * 100}")
    print("AUTRES SOURCES")
    print(f"{'=' * 100}\n")

    _audit_parc_vehicules(annees[0])

    print(
        "\n  Sources suivantes : aucun mécanisme de secret statistique INSEE "
        "identifié dans le code\n  (vérifié par lecture du code, pas supposé) :\n"
        "\n"
        "  MOBILITE_DT   : calculé depuis les micro-données individuelles du RP "
        "pondérées (IPONDI),\n"
        "                  pas une table pré-agrégée soumise à un masquage par "
        "cellule.\n"
        "  CITEPA_IGT    : inventaire territorialisé par désagrégation descendante "
        "(modèle),\n"
        "                  pas une statistique d'enquête avec seuil de "
        "confidentialité par maille.\n"
        "  METEO_FRANCE  : mesures physiques de stations, aucune notion de secret "
        "statistique.\n"
        "  OMPHALE       : projections démographiques Insee à l'échelle EPCI/DROM, "
        "pas de masquage\n"
        "                  par cellule dans le fichier utilisé.\n"
        "  ALDO          : calculateur UTCATF (facteurs d'émission x surfaces), pas "
        "une donnée\n"
        "                  d'enquête masquable.\n"
        "  AGRESTE (RA)  : fichier retraité par Solagro (cheptel RA 2020 déjà "
        "reventilé au niveau\n"
        "                  communal), colonnes numériques nativement typées à la "
        "lecture Excel, sans\n"
        "                  colonne 'confidentiel' ni littéral 's' : un masquage "
        "résiduel provoquerait\n"
        "                  une erreur de type au chargement plutôt qu'un passage "
        "silencieux - non\n"
        "                  observé en pratique.\n"
    )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--annees", type=int, nargs="+", default=None, help="Années (défaut : ANNEE_REF)")
    args = parser.parse_args()

    import pandas as pd
    from config.settings import ANNEE_REF, OUTPUT_DIR
    from sources.geo_epci import load_commune_epci_cog, regrouper_arrondissements_plm

    annees = args.annees or [ANNEE_REF]
    corr = load_commune_epci_cog()[["codgeo", "code_epci"]]

    resultats = []

    # --- FLORES ---
    print(f"\n{'=' * 100}")
    print("FLORES - secret statistique (industrie)")
    print(f"{'=' * 100}")
    from sources.flores import load_flores
    for annee in annees:
        try:
            df_val, df_secret = load_flores(annee)
        except Exception as e:
            print(f"  FLORES {annee} : erreur ({e})")
            continue
        df_val = regrouper_arrondissements_plm(df_val)
        # Regroupement booléen (sum sur bool = compte de True, >0 = "any" par groupe)
        df_secret = regrouper_arrondissements_plm(df_secret.astype(int)) > 0
        res = _diag_source_communale(df_val, df_secret, annee, corr)
        if not res.empty:
            res.insert(0, "source", "FLORES")
            resultats.append(res)

    # --- INSEE_RP ---
    print(f"\n{'=' * 100}")
    print("INSEE_RP - secret statistique (dont EMP_CTS/EMP_APESAS -> EMP_TER, tertiaire)")
    print(f"{'=' * 100}")
    from sources.insee_rp import extract_indicators as extract_rp
    for annee in annees:
        try:
            df_val, df_secret = extract_rp(annee)
        except Exception as e:
            print(f"  INSEE_RP {annee} : erreur ({e})")
            continue
        res = _diag_source_communale(df_val, df_secret, annee, corr)
        if not res.empty:
            res.insert(0, "source", "INSEE_RP")
            resultats.append(res)

    # --- ENERGIE_CLIMAT ---
    print(f"\n{'=' * 100}")
    print("ENERGIE_CLIMAT (OREC) - secret statistique (dont EC_CONSO_TERTIAIRE/EC_EMIS_TERTIAIRE)")
    print(f"{'=' * 100}")
    for annee in annees:
        try:
            res = _diag_energie_climat(annee)
        except Exception as e:
            print(f"  ENERGIE_CLIMAT {annee} : erreur ({e})")
            continue
        if not res.empty:
            res.insert(0, "source", "ENERGIE_CLIMAT")
            resultats.append(res)

    if resultats:
        df_all = pd.concat(resultats, ignore_index=True)
        pd.set_option("display.width", 200)
        print()
        print(df_all.to_string(index=False))
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUTPUT_DIR / f"diag_secret_stat_{'-'.join(map(str, annees))}.csv"
        df_all.to_csv(out, index=False, sep=";", encoding="utf-8-sig")
        print(f"\n-> {out}")
    else:
        print("\nAucune valeur masquée par le secret statistique détectée sur les années demandées.")

    _audit_autres_sources(annees)

    print(
        "Lecture : 'taux_epci_touches_pct' est le taux d'EPCI dont la valeur agrégée "
        "est potentiellement sous-estimée par au moins une commune masquée (FLORES, "
        "INSEE_RP) ou directement masquée (ENERGIE_CLIMAT). Ce sont ces lignes qui "
        "portent qualite='secret_stat' dans bdd_epci.parquet - y compris pour les "
        "indicateurs dérivés (EMP_TER, EMP_IND) dès qu'un opérande est masqué.\n"
        "Pour information uniquement, pas d'assertion."
    )


if __name__ == "__main__":
    main()
