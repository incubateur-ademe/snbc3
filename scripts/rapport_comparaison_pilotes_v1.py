"""
scripts/rapport_comparaison_pilotes_v1.py
==========================================
Rapport (pour information, non bloquant) : compare, pour chaque territoire
pilote ayant testé l'outil v1 ("Retours territoires"), les résultats
sectoriels obtenus avec l'outil Excel v1 (onglet Caract_territoire pour le
code EPCI, onglet TOUS SECTEURS / TrajRef_territoire pour les résultats) aux
résultats du pipeline Python actuel, pour le même EPCI.

Des écarts sont attendus par construction : changement de pivots, de
méthodologie de recalage et de version de la trajectoire nationale SNBC entre
l'outil v1 (années de référence ~2015-2018 selon le fichier) et le pipeline
actuel (ANNEE_REF dans config/settings.py). Ce script n'est PAS un test -
aucune assertion, code de sortie toujours 0 : objectif exploratoire, pour
documenter l'ampleur des écarts méthodologiques.

Usage :
    uv run python scripts/rapport_comparaison_pilotes_v1.py "<dossier Retours territoires>"
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SECTEURS_CONNUS = {
    "Résidentiel", "Tertiaire", "Industrie", "Agriculture",
    "Transports", "Déchets", "Branche énergie", "UTCATF", "CSC",
}

RESULTATS_SHEET_CANDIDATS = ["TOUS SECTEURS", "TrajRef_territoire"]

# Fichiers du dossier "Retours territoires" qui ne sont pas des exports de
# l'outil (pas d'onglet Caract_territoire) : à ignorer.
_EXCLUS_MOTS = ["questionnaire", "suivi retours"]


def _named_cell_value(wb, name: str, sheet_fallback: str, row_fallback: int, col_fallback: int):
    """Résout une plage nommée (ex. terr_num_epci) ; repli sur une cellule fixe si absente/invalide."""
    try:
        dn = wb.defined_names.get(name)
        if dn is not None:
            for sheet_name, coord in dn.destinations:
                return wb[sheet_name][coord].value
    except Exception:
        pass
    try:
        return wb[sheet_fallback].cell(row_fallback, col_fallback).value
    except Exception:
        return None


def _find_results_sheet(wb):
    for name in RESULTATS_SHEET_CANDIDATS:
        if name in wb.sheetnames:
            return wb[name]
    return None


def _extract_secteur_totals(ws, ref_annee):
    """
    Parcourt la feuille de résultats (iter_rows séquentiel - rapide en
    read_only, contrairement à un accès cellule par cellule) et retourne
    {secteur: (ges_kt, conso_gwh)} pour l'année de référence du fichier.

    Repère la colonne-année via l'en-tête (ligne 1), colonnes G et suivantes.
    Les lignes GES sont en unité 'ktCO2eq', les lignes conso en 'GWh' - ce qui
    les distingue déjà des lignes nationales plus haut dans la même feuille
    (unités 'MtCO2eq' / 'TWh').
    """
    rows = list(ws.iter_rows(min_row=1, max_row=250, values_only=True))
    if not rows:
        return {}

    header = rows[0]
    year_cols = [
        i for i, v in enumerate(header)
        if isinstance(v, (int, float)) and 2000 <= int(v) <= 2100
    ]
    if not year_cols:
        return {}
    # Colonne correspondant à l'année de référence du fichier (repli : 1re colonne-année)
    col_idx = next((i for i in year_cols if int(header[i]) == ref_annee), year_cols[0])

    out: dict[str, list] = {s: [None, None] for s in SECTEURS_CONNUS}
    for row in rows[1:]:
        if len(row) <= col_idx:
            continue
        secteur = row[1] if len(row) > 1 else None
        unite = row[4] if len(row) > 4 else None
        if secteur not in SECTEURS_CONNUS or unite not in ("ktCO2eq", "GWh"):
            continue
        val = row[col_idx]
        if val is None:
            continue
        slot = 0 if unite == "ktCO2eq" else 1
        out[secteur][slot] = val
    return out


def _resultats_pipeline(df_pipeline, code_epci: str, annee: int):
    """Σ valeur_recalee par secteur (GES en kt, conso en GWh) pour un EPCI, pipeline actuel, à `annee`."""
    sub = df_pipeline[(df_pipeline["code_epci"] == code_epci) & (df_pipeline["annee"] == annee)]
    if sub.empty:
        return None

    out = {}
    for secteur, grp in sub.groupby("secteur"):
        ges = grp.loc[grp["type"] == "GES", "valeur_recalee"].sum() * 1000  # Mt -> kt
        conso = grp.loc[grp["type"] == "conso", "valeur_recalee"].sum() * 1000  # TWh -> GWh
        out[secteur] = (ges if not grp.loc[grp["type"] == "GES"].empty else None,
                        conso if not grp.loc[grp["type"] == "conso"].empty else None)
    return out


def _comparer_fichier(path: Path, df_pipeline, annee_pipeline: int):
    import openpyxl

    lignes = []
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        return [{"fichier": path.name, "secteur": "", "commentaire": f"ouverture impossible : {e}"}]

    if "Caract_territoire" not in wb.sheetnames:
        wb.close()
        return [{"fichier": path.name, "secteur": "", "commentaire": "onglet Caract_territoire absent"}]

    code_epci = _named_cell_value(wb, "terr_num_epci", "Caract_territoire", 6, 6)
    ref_annee = _named_cell_value(wb, "Annee_ref", "Caract_territoire", 4, 6)
    nom_territoire = _named_cell_value(wb, "terr_nom_epci", "Caract_territoire", 7, 6)

    if code_epci is None or ref_annee is None:
        wb.close()
        return [{"fichier": path.name, "secteur": "", "commentaire": "code EPCI ou année de référence introuvable"}]
    code_epci = str(int(code_epci)) if isinstance(code_epci, float) else str(code_epci).strip()
    ref_annee = int(ref_annee)

    ws_res = _find_results_sheet(wb)
    if ws_res is None:
        wb.close()
        return [{"fichier": path.name, "secteur": "", "commentaire": "onglet résultats introuvable (TOUS SECTEURS / TrajRef_territoire)"}]

    v1_totaux = _extract_secteur_totals(ws_res, ref_annee)
    wb.close()

    try:
        pipeline_totaux = _resultats_pipeline(df_pipeline, code_epci, annee_pipeline)
    except Exception as e:
        return [{"fichier": path.name, "secteur": "", "commentaire": f"pipeline échoué pour EPCI {code_epci} : {e}"}]

    if pipeline_totaux is None:
        return [{
            "fichier": path.name, "secteur": "",
            "commentaire": f"EPCI {code_epci} ({nom_territoire}) absent du périmètre pipeline actuel",
        }]

    for secteur in sorted(SECTEURS_CONNUS):
        v1_ges, v1_conso = v1_totaux.get(secteur, (None, None))
        py_ges, py_conso = pipeline_totaux.get(secteur, (None, None))
        if v1_ges is None and v1_conso is None and py_ges is None and py_conso is None:
            continue
        lignes.append({
            "fichier": path.name,
            "code_epci": code_epci,
            "territoire": nom_territoire,
            "annee_v1": ref_annee,
            "annee_pipeline": annee_pipeline,
            "secteur": secteur,
            "ges_v1_kt": v1_ges,
            "ges_pipeline_kt": py_ges,
            "conso_v1_gwh": v1_conso,
            "conso_pipeline_gwh": py_conso,
            "commentaire": "",
        })
    return lignes


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dossier", type=Path, help="Dossier 'Retours territoires' contenant les .xlsx de l'outil v1")
    parser.add_argument("--annee", type=int, default=None, help="Année pipeline actuel (défaut : ANNEE_REF)")
    args = parser.parse_args()

    from config.settings import ANNEE_REF, OUTPUT_DIR
    annee_pipeline = args.annee or ANNEE_REF

    if not args.dossier.is_dir():
        print(f"Dossier introuvable : {args.dossier}")
        sys.exit(1)

    fichiers = sorted(
        p for p in args.dossier.glob("*.xlsx")
        if not any(mot in p.name.lower() for mot in _EXCLUS_MOTS) and not p.name.startswith("~$")
    )
    print(f"{len(fichiers)} fichier(s) pilote(s) trouvé(s) dans {args.dossier}")

    from pipeline.territorialize import build_territorialise
    print(f"Construction du pipeline actuel (année {annee_pipeline})...")
    df_pipeline = build_territorialise(annee=annee_pipeline, zone="HEXAGONE")

    import pandas as pd
    toutes_lignes = []
    for p in fichiers:
        print(f"  - {p.name}")
        toutes_lignes.extend(_comparer_fichier(p, df_pipeline, annee_pipeline))

    df = pd.DataFrame(toutes_lignes)
    pd.set_option("display.width", 160)
    print("\n" + df.to_string(index=False))

    out_path = OUTPUT_DIR / f"comparaison_pilotes_v1_{date.today()}.csv"
    df.to_csv(out_path, index=False, sep=";", encoding="utf-8-sig")
    print(f"\n-> Rapport exporté : {out_path}")
    print(
        "\nNote : écarts attendus (changement de pivots/méthodologie de recalage/"
        "version SNBC entre l'outil v1 et le pipeline actuel). Pour information "
        "uniquement, pas d'assertion."
    )


if __name__ == "__main__":
    main()
