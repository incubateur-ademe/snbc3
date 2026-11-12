"""
scripts/verif_tir_excel.py
==========================
Script MANUEL (pas un test pytest) : vérifie, dans le tableur Excel standalone,
si la somme des résultats obtenus en sélectionnant chaque EPCI d'un échantillon
un par un ("tirs individuels") correspond au résultat obtenu en sélectionnant
l'ensemble de l'échantillon en une seule fois ("tir groupé") dans l'onglet
Territoire.

Deux niveaux de lecture, à ne pas confondre :

  - Par code_recalage (secteur agrégé, ex. « Industrie hors branche énergie ») :
    le total DOIT être conservé exactement, tir individuel ou groupé - c'est
    Σ_EPCI obs(EPCI, code_recalage), une quantité observée indépendante du
    regroupement. Un écart notable à ce niveau est un bug, pas un effet
    attendu.
  - Par usage détaillé (code_traj_supra, ex. « emi_ind_met » au sein
    d'Industrie) : un écart PEUT être normal, pour deux raisons non
    linéaires distinctes :
      1. Corrections DJU/démographiques : moyenne pondérée par population
         sur les EPCI cochés (cf.
         standalone_tool/update_standalone_tool.py::_build_calcul), non
         linéaire en la composition du groupe.
      2. Recalage partagé : le total observé par (EPCI, code_recalage) est
         redistribué entre usages au prorata de leur poids dans le brut ;
         ce prorata diffère selon qu'il est calculé EPCI par EPCI (tir
         individuel) ou sur le groupe agrégé (tir groupé) - même mécanisme
         que Excel vs Python dans tests/test_excel_vs_python.py, mais ici
         interne à Excel selon la sélection.

Ce script affiche les deux niveaux pour distinguer un écart normal (usage,
raison 2) d'un vrai bug (secteur agrégé, raison 1 absente).

Prérequis :
  - Excel installé (pywin32/xlwings pilotent l'instance via COM).
  - Le tableur cible doit exister (standalone_tool/output/... ou --fichier).
  - RECALCULE LA SÉLECTION DU TERRITOIRE dans le classeur ouvert : à lancer sur
    une copie de travail si vous ne voulez pas perdre la sélection actuelle.

Usage :
    uv run python scripts/verif_tir_excel.py --departement 88 --max-epci 12
    uv run python scripts/verif_tir_excel.py --fichier "chemin.xlsx" --epci 200068757 200035756
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SHEET_TERRITOIRE = "Territoire"
S0 = 7          # 1re ligne de sélection (après le bloc ⓪ Zone, cf. update_standalone_tool)
HC, HL = 24, 26  # colonnes cachées : code EPCI (X), libellé désambiguïsé (Z)


def _find_or_open(fichier: Path):
    """Classeur ouvert dans une instance Excel active, sinon ouvert dans une
    nouvelle instance : (classeur, app, ouvert_par_le_script)."""
    import xlwings as xw
    for app in xw.apps:
        for book in app.books:
            try:
                if Path(book.fullname).resolve() == fichier:
                    return book, app, False
            except Exception:
                continue
    app = xw.App(visible=True)
    return app.books.open(str(fichier)), app, True


def _excel_perf_on(app) -> dict:
    """Coupe l'affichage, le calcul automatique et les événements."""
    etat = {}
    try:
        etat["screen"] = app.screen_updating
        app.screen_updating = False
        etat["calc"] = app.api.Calculation
        app.api.Calculation = -4135  # xlCalculationManual
        etat["events"] = app.api.EnableEvents
        app.api.EnableEvents = False
    except Exception:
        pass
    return etat


def _excel_perf_off(app, etat: dict) -> None:
    try:
        app.api.EnableEvents = etat.get("events", True)
        app.api.Calculation = etat.get("calc", -4105)  # xlCalculationAutomatic
        app.screen_updating = etat.get("screen", True)
    except Exception:
        pass


def _derniere_ligne(ws) -> int:
    """
    Dernière ligne de la liste des EPCI (colonne cachée des codes). Lue sur
    le classeur plutôt que figée : la liste grandit avec les zones et les
    subdivisions (EPT, Paris) couvertes.
    """
    return ws.range((S0, HC)).end("down").row


def _epci_label_map(ws) -> dict[str, str]:
    """Code EPCI -> libellé désambiguïsé, depuis les colonnes cachées de l'onglet Territoire."""
    s1 = _derniere_ligne(ws)
    codes = ws.range((S0, HC), (s1, HC)).value
    labels = ws.range((S0, HL), (s1, HL)).value
    codes = codes if isinstance(codes, list) else [codes]
    labels = labels if isinstance(labels, list) else [labels]
    return {
        (str(int(c)) if isinstance(c, float) else str(c)): lbl
        for c, lbl in zip(codes, labels) if c
    }


def _set_selection(ws, labels: list[str]) -> None:
    """Vide la colonne A (sélection) puis écrit les libellés donnés."""
    ws.range((S0, 1), (_derniere_ligne(ws), 1)).clear_contents()
    if labels:
        ws.range((S0, 1)).options(transpose=True).value = labels


def _read_calcul_recale(wb) -> dict[str, float]:
    """{code_traj_supra: valeur "Résultat recalé" à l'année de référence (kt/GWh), colonne K = Annee_ref."""
    ws = wb.sheets["CALCUL"]
    last_row = ws.used_range.last_cell.row
    data = ws.range((6, 4), (last_row, 11)).value  # colonnes D (code) .. K (1re année)
    out = {}
    for row in data:
        code, etape, val = row[0], row[4], row[7]
        if code and etape == "Résultat recalé" and val is not None:
            out[code] = val
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fichier", type=Path, default=None, help="Classeur cible (défaut : TRAJ_GES_TARGET_PATH)")
    parser.add_argument("--departement", type=str, default=None, help="Code département (2-3 chiffres) pour choisir l'échantillon d'EPCI")
    parser.add_argument("--epci", nargs="+", default=None, help="Liste explicite de codes EPCI (alternative à --departement)")
    parser.add_argument("--max-epci", type=int, default=12, help="Taille max de l'échantillon (défaut 12 - chaque EPCI = 1 recalc complet)")
    args = parser.parse_args()

    from config.settings import ANNEE_REF, TRAJ_GES_TARGET_PATH
    from pipeline.aggregate import build_bdd

    fichier = args.fichier or TRAJ_GES_TARGET_PATH
    if not fichier.exists():
        print(f"Fichier introuvable : {fichier}")
        sys.exit(1)

    if args.epci:
        echantillon = list(args.epci)
    elif args.departement:
        df = build_bdd(annee=ANNEE_REF)
        echantillon = sorted(
            df.loc[df["code_departement"].astype(str).str.contains(args.departement, na=False), "code_epci"]
            .unique()
        )[: args.max_epci]
    else:
        print("Fournir --departement ou --epci")
        sys.exit(1)

    if not echantillon:
        print("Aucun EPCI trouvé pour cet échantillon.")
        sys.exit(1)
    print(f"Échantillon ({len(echantillon)} EPCI) : {echantillon}")

    wb, owner_app, opened = _find_or_open(fichier)
    if SHEET_TERRITOIRE not in [s.name for s in wb.sheets]:
        print(f"Onglet « {SHEET_TERRITOIRE} » introuvable - abandon.")
        if opened:
            wb.close()
        sys.exit(1)

    ws_terr = wb.sheets[SHEET_TERRITOIRE]
    label_map = _epci_label_map(ws_terr)
    manquants = [c for c in echantillon if c not in label_map]
    if manquants:
        print(f"AVERTISSEMENT : EPCI absents de l'onglet Territoire (ignorés) : {manquants}")
    echantillon = [c for c in echantillon if c in label_map]

    perf_state = _excel_perf_on(wb.app)
    try:
        somme_individuels: dict[str, float] = {}
        for i, code in enumerate(echantillon):
            print(f"  [{i+1}/{len(echantillon)}] tir individuel : {code} ({label_map[code]})")
            _set_selection(ws_terr, [label_map[code]])
            t0 = time.time()
            wb.app.calculate()
            resultats = _read_calcul_recale(wb)
            print(f"      recalcul {time.time() - t0:.1f}s")
            for code_traj, val in resultats.items():
                somme_individuels[code_traj] = somme_individuels.get(code_traj, 0.0) + val

        print(f"  Tir groupé : {len(echantillon)} EPCI ensemble")
        _set_selection(ws_terr, [label_map[c] for c in echantillon])
        wb.app.calculate()
        resultat_groupe = _read_calcul_recale(wb)
    finally:
        _excel_perf_off(wb.app, perf_state)
        if opened:
            wb.close()

    from config.settings import ZONES_TERRITORIALISEES
    from pipeline.territorialize import load_correspondance_union
    corr = load_correspondance_union(list(ZONES_TERRITORIALISEES))
    recalage_map = dict(zip(corr["code_traj_supra"], corr["code_recalage"]))

    import pandas as pd
    codes_traj = sorted(set(somme_individuels) | set(resultat_groupe))
    rows = []
    for c in codes_traj:
        v_ind = somme_individuels.get(c, 0.0)
        v_grp = resultat_groupe.get(c, 0.0)
        ecart_pct = (v_ind - v_grp) / v_grp * 100 if v_grp else None
        rows.append({
            "code_traj_supra": c, "code_recalage": recalage_map.get(c) or "(aucun)",
            "somme_individuels": v_ind, "tir_groupe": v_grp, "ecart_pct": ecart_pct,
        })
    df_out = pd.DataFrame(rows)
    pd.set_option("display.width", 140)
    print("\nPar usage (code_traj_supra) :")
    print(df_out.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))

    # Agrégation par code_recalage : le recalage redistribue un même total
    # observé par (EPCI, code_recalage) entre les usages qui le partagent - ce
    # total DOIT être conservé exactement (linéaire), que la redistribution
    # interne diffère ou non entre tir individuel et tir groupé.
    df_secteur = (
        df_out.groupby("code_recalage")[["somme_individuels", "tir_groupe"]].sum().reset_index()
    )
    df_secteur["ecart_pct"] = (
        (df_secteur["somme_individuels"] - df_secteur["tir_groupe"]) / df_secteur["tir_groupe"] * 100
    )
    print("\nAgrégé par code_recalage (doit être ~0% - propriété linéaire) :")
    print(df_secteur.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))

    print(
        "\nNote : au niveau de chaque code_recalage, le total est conservé "
        "exactement (somme des obs par EPCI, indépendant du regroupement) - un écart "
        "significatif dans le tableau agrégé ci-dessus serait un signal de bug, "
        "pas un effet attendu. En revanche, au niveau de l'usage détaillé "
        "(1re table), un écart peut être normal pour deux raisons non "
        "linéaires distinctes : (1) DJU/démo - moyenne pondérée par "
        "population, non linéaire selon la composition du groupe ; "
        "(2) recalage partagé - un même total observé par (EPCI, "
        "code_recalage) est redistribué entre usages au prorata de leur poids "
        "dans le brut, et ce poids diffère selon qu'on calcule le prorata "
        "EPCI par EPCI (tir individuel) ou sur le groupe agrégé (tir groupé) "
        "- cf. tests/test_excel_vs_python.py pour l'effet équivalent Excel vs "
        "Python. Pas d'assertion, script de vérification manuelle uniquement."
    )


if __name__ == "__main__":
    main()
