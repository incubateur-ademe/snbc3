"""
scripts/verif_zone_excel.py
===========================
Rapport (pour information, non bloquant) : compare l'onglet `CALCUL` du tableur
au pipeline Python, pour un ou plusieurs EPCI, dans **n'importe quelle zone**.

Pourquoi ce script
------------------
`tests/test_excel_vs_python.py` ne couvre que l'hexagone : il lit le classeur
tel qu'il a été généré, donc avec sa sélection pré-remplie. Rien n'attraperait
une divergence propre à une autre zone. Ce script pilote Excel pour poser une
sélection arbitraire, recalculer, puis confronter les résultats recalés au
parquet produit par `main.py territorialize`.

Il sert aussi à vérifier le **garde-fou multi-zone** : avec des EPCI de zones
différentes, la zone doit ressortir vide et tous les résultats à 0.

Le classeur est fermé **sans enregistrement** : la sélection d'origine du
livrable est préservée.

Prérequis : `main.py territorialize` puis `standalone_tool.update_standalone_tool`
doivent avoir tourné (parquet et classeur à jour, sinon la comparaison n'a pas
de sens).

Usage :
    uv run python scripts/verif_zone_excel.py --epci 249740119
    uv run python scripts/verif_zone_excel.py --epci 249740119 200046977
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Repères de mise en page, alignés sur update_standalone_tool.py
HR, D0, FY = 5, 6, 11       # ligne en-tête / 1re ligne de données / 1re colonne-année (CALCUL)
S0, HC, HL = 7, 24, 26      # 1re ligne de sélection / colonnes masquées code et libellé
SEUIL = 1e-6                # écart relatif au-delà duquel on signale


def _code(v) -> str:
    """xlwings renvoie les nombres en float : 249740119.0 doit valoir '249740119'."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip() if v is not None else ""


def _lire_excel(codes_epci: list[str]):
    """Pose la sélection, recalcule, et retourne (zone, contrôle, DataFrame)."""
    import pandas as pd
    import xlwings as xw

    from config.settings import TRAJ_GES_TARGET_PATH

    app = xw.App(visible=False)
    app.display_alerts = False
    try:
        wb = app.books.open(str(TRAJ_GES_TARGET_PATH))
        # Le classeur est enregistré en calcul manuel (mode performance du
        # build) : sans ce forçage la sélection ne se propage pas.
        app.api.Calculation = -4105                      # xlCalculationAutomatic

        ws = wb.sheets["Territoire"]
        # Dernière ligne lue sur le classeur plutôt que figée : la liste
        # grandit avec les zones et les subdivisions (EPT, Paris) couvertes.
        s1 = ws.range((S0, HC)).end("down").row
        codes = ws.range((S0, HC), (s1, HC)).value
        labels = ws.range((S0, HL), (s1, HL)).value
        index = {_code(c): i for i, c in enumerate(codes) if c}

        manquants = [c for c in codes_epci if c not in index]
        if manquants:
            raise SystemExit(
                f"EPCI absent(s) de la liste du tableur : {manquants}\n"
                "Un EPCI d'une zone non territorialisée (Mayotte) en est "
                "volontairement retiré."
            )

        ws.range((S0, 1), (s1, 1)).clear_contents()
        for j, c in enumerate(codes_epci):
            ws.range((S0 + j, 1)).value = labels[index[c]]
        app.api.CalculateFullRebuild()

        zone = ws.range((3, 2)).value
        controle = ws.range((3, 5)).value

        cw = wb.sheets["CALCUL"]
        last = cw.range((cw.cells.last_cell.row, 4)).end("up").row
        meta = cw.range((D0, 4), (last, 8)).value          # D..H : code … étape
        annees = cw.range((HR, FY), (HR, FY + 30)).value
        vals = cw.range((D0, FY), (last, FY + 30)).value

        lignes = []
        erreurs = 0
        for m, v in zip(meta, vals):
            if m[4] != "Résultat recalé":
                continue
            for a, x in zip(annees, v):
                if a is None:
                    continue
                if isinstance(x, str) and x.startswith("#"):
                    erreurs += 1
                    continue
                if isinstance(x, (int, float)):
                    lignes.append({"code_traj_supra": m[0], "annee": int(a),
                                   "excel": x})
        if erreurs:
            print(f"  ATTENTION : {erreurs} cellule(s) en erreur dans CALCUL")
        wb.close()                      # sans enregistrement
        return zone, controle, pd.DataFrame(lignes)
    finally:
        app.quit()


def main():
    import pandas as pd

    from config.settings import OUTPUT_DIR

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--epci", nargs="+", required=True, help="Codes EPCI à sélectionner")
    args = p.parse_args()

    zone, controle, excel = _lire_excel(args.epci)
    print(f"\nEPCI sélectionné(s) : {', '.join(args.epci)}")
    print(f"Zone détectée       : {zone!r}")
    print(f"Contrôle            : {controle}")

    if not zone:
        total = excel["excel"].abs().sum() if not excel.empty else 0.0
        print(f"\nZone indéterminée : somme des résultats = {total:,.3f} "
              f"({'OK, neutralisés' if total == 0 else 'ANOMALIE, devrait être nulle'})")
        return

    if len(args.epci) > 1:
        print("\nComparaison au pipeline non applicable : le parquet est par EPCI.")
        return

    parquet = OUTPUT_DIR / "resultats_territorialises.parquet"
    if not parquet.exists():
        raise SystemExit(f"{parquet} absent : lancer main.py territorialize d'abord.")
    py = pd.read_parquet(parquet)
    py = py[py["code_epci"] == args.epci[0]][["code_traj_supra", "annee", "valeur_recalee"]]
    if py.empty:
        raise SystemExit(f"{args.epci[0]} absent du parquet : zone non territorialisée ?")
    py["python"] = py["valeur_recalee"] * 1000        # Mt/TWh -> kt/GWh

    m = excel.merge(py, on=["code_traj_supra", "annee"], how="inner")
    m["ecart_rel"] = (m["excel"] - m["python"]) / m["python"].replace(0, float("nan"))
    hors = m[m["ecart_rel"].abs() > SEUIL].dropna(subset=["ecart_rel"])

    print(f"\n{len(m)} couples (sous-secteur, année) comparés "
          f"sur {m['code_traj_supra'].nunique()} sous-secteurs")
    print(f"écart relatif maximal : {m['ecart_rel'].abs().max():.2e}")
    print(f"couples au-delà de {SEUIL:.0e} : {len(hors)}")
    if len(hors):
        print(hors.nlargest(10, "ecart_rel", keep="all")[
            ["code_traj_supra", "annee", "excel", "python", "ecart_rel"]
        ].to_string(index=False))

    absents = set(py["code_traj_supra"]) - set(excel["code_traj_supra"])
    if absents:
        print(f"Sous-secteurs présents dans le pipeline mais pas dans CALCUL : {sorted(absents)}")
    print("\nPour information, pas d'assertion.")


if __name__ == "__main__":
    main()
