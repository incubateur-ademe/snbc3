"""
scripts/rapport_coherence_nationale.py
=======================================
Rapport (pour information, non bloquant) : compare la somme des indicateurs
de recalage par EPCI (OREC énergie-climat + ALDO UTCATF, cf.
config/traj_supra.yaml::code_recalage) à la trajectoire nationale SNBC3, par
code de recalage, à l'année de référence.

Il n'existe pas de fichier de référence SDES national séparé dans ce dépôt :
l'OREC (bouquet RARE, cf. sources/energie_climat.py) sert déjà de proxy
"bottom-up" pour le national - c'est la source utilisée pour recaler le
pipeline de territorialisation.

Des écarts sont attendus par construction : l'OREC (approche cadastrale
Scope 1&2, granularité EPCI) et la SNBC3 (approche sectorielle nationale) ne
couvrent pas exactement le même périmètre méthodologique. Ce script n'est
PAS un test - aucune assertion, code de sortie toujours 0. Objectif : suivi
dans le temps, à relancer manuellement.

Chaque code_recalage reçoit un statut A_EXAMINER / OK / N.D., affiché et
exporté mais non bloquant : un simple repère pour ne pas avoir à relire le
CSV en entier. A_EXAMINER exige un écart relatif ET absolu significatifs à la
fois - le relatif seul explose sur les codes ALDO_UTCATF à faible valeur
nationale (Autres terres, Zones humides), sans qu'un vrai désaccord existe en
dessous du seuil absolu.

Usage :
    uv run python scripts/rapport_coherence_nationale.py
    uv run python scripts/rapport_coherence_nationale.py --annee 2021 --rebuild
    uv run python scripts/rapport_coherence_nationale.py --seuil-ecart-pct 20 --seuil-absolu 1
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Seuils de signalement (léger, non bloquant, cf. docstring). ecart_max_pct=25
# reprend la convention déjà utilisée pour les contrôles divergence_sources de
# config/anomalies_snbc3.yaml.
SEUIL_ECART_PCT = 25.0
SEUIL_ABSOLU = 0.5   # Mt ou TWh, unité nationale du code_recalage


def _load_bdd(annee: int, rebuild: bool):
    import pandas as pd
    from config.settings import OUTPUT_DIR

    parquet_path = OUTPUT_DIR / "bdd_epci.parquet"
    if not rebuild and parquet_path.exists():
        df = pd.read_parquet(parquet_path)
        if annee in set(df["annee"].unique()):
            print(f"BDD lue depuis {parquet_path} (--rebuild pour relancer le pipeline)")
            return df
        print(f"Année {annee} absente de {parquet_path.name}, reconstruction via build_bdd()...")

    from pipeline.aggregate import build_bdd
    print(f"Construction de la BDD ({annee})...")
    return build_bdd(annee=annee)


def _somme_epci_par_recalage(df_long, annee: int, corr) -> dict:
    """Σ EPCI (hors FRANCE/HEXAGONE/DROM) par code_recalage, dans l'unité nationale (Mt/TWh)."""
    from pipeline.aggregate import NIVEAU_GEO_EPCI, load_catalogue
    from pipeline.territorialize import _recalage_unit_factor

    unite_map = {ind["code"]: ind.get("unite", "") for ind in load_catalogue()}
    recal_codes = set(corr["code_recalage"].dropna().unique())

    # SNBC3_SHEETS["HEXAGONE"] est un scope hexagone uniquement (cf. config/settings.py) :
    # exclure explicitement les EPCI DROM, pas seulement les lignes agrégées
    # FRANCE/HEXAGONE/DROM. Ce filtre est indispensable depuis que l'IGT du
    # CITEPA alimente les codes EC_EMIS_* sur les EPCI DROM
    # (cf. sources/citepa_igt.py) : sans lui, la somme comparée au national
    # hexagonal inclurait les émissions observées des DROM.
    # Les EPT et Paris (niveau_geo EPT / COMMUNE) sont imbriqués dans la MGP :
    # les sommer avec les EPCI compterait deux fois la MGP (sensible sur les
    # codes ALDO, publiés à ces mailles, cf. pipeline/aggregate.py).
    mask = (
        df_long["code_indicateur"].isin(recal_codes)
        & (df_long["annee"] == annee)
        & (df_long["zone"] == "HEXAGONE")
        & (df_long["niveau_geo"] == NIVEAU_GEO_EPCI)
    )
    obs = df_long.loc[mask].dropna(subset=["valeur"]).groupby("code_indicateur")["valeur"].sum()
    return {
        code: val * _recalage_unit_factor(unite_map.get(code, ""))
        for code, val in obs.items()
    }


def _valeur_nationale_par_recalage(annee: int, corr) -> dict:
    """Σ SNBC3 (Mt/TWh) des sous-secteurs partageant chaque code_recalage."""
    from pipeline.territorialize import load_national_traj

    national = load_national_traj(set(corr["code_traj_supra"]), "HEXAGONE")
    national = national[national["annee"] == annee]
    merged = national.merge(corr[["code_traj_supra", "code_recalage"]], on="code_traj_supra")
    merged = merged.dropna(subset=["code_recalage"])
    return merged.groupby("code_recalage")["valeur_nationale"].sum().to_dict()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--annee", type=int, default=None, help="Année (défaut : ANNEE_REF)")
    parser.add_argument("--rebuild", action="store_true", help="Force la reconstruction de la BDD")
    parser.add_argument("--seuil-ecart-pct", type=float, default=SEUIL_ECART_PCT,
                        help=f"Écart relatif (%%) au-delà duquel un code est signalé (défaut : {SEUIL_ECART_PCT:g})")
    parser.add_argument("--seuil-absolu", type=float, default=SEUIL_ABSOLU,
                        help="Valeur nationale (Mt/TWh) en deçà de laquelle l'écart relatif est jugé "
                             f"non significatif (défaut : {SEUIL_ABSOLU:g})")
    args = parser.parse_args()

    import pandas as pd

    from config.settings import ANNEE_REF, OUTPUT_DIR
    from pipeline.territorialize import load_correspondance

    annee = args.annee or ANNEE_REF
    corr = load_correspondance("HEXAGONE")

    df_long = _load_bdd(annee, args.rebuild)
    somme_epci = _somme_epci_par_recalage(df_long, annee, corr)
    snbc = _valeur_nationale_par_recalage(annee, corr)

    secteur_by_code = (
        corr.dropna(subset=["code_recalage"])
        .drop_duplicates("code_recalage")
        .set_index("code_recalage")["secteur"]
        .to_dict()
    )

    rows = []
    for code in sorted(set(somme_epci) | set(snbc)):
        v_epci = somme_epci.get(code)
        v_snbc = snbc.get(code)
        ecart_pct = (v_epci - v_snbc) / v_snbc * 100 if v_epci is not None and v_snbc else None
        if ecart_pct is None:
            statut = "N.D."
        elif abs(v_snbc) < args.seuil_absolu:
            statut = "N.D."
        elif abs(ecart_pct) > args.seuil_ecart_pct:
            statut = "A_EXAMINER"
        else:
            statut = "OK"
        rows.append({
            "code_recalage": code,
            "secteur": secteur_by_code.get(code, ""),
            "somme_epci": v_epci,
            "snbc3_national": v_snbc,
            "ecart_pct": ecart_pct,
            "statut": statut,
        })

    df_rapport = pd.DataFrame(rows).sort_values("secteur")

    pd.set_option("display.width", 140)
    print(f"\nCohérence somme EPCI (OREC/ALDO) vs national SNBC3 - année {annee}\n")
    print(df_rapport.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))

    a_examiner = df_rapport[df_rapport["statut"] == "A_EXAMINER"]
    print(f"\n{'-' * 94}")
    print(f"A_EXAMINER : écart relatif > {args.seuil_ecart_pct:g} % ET valeur nationale "
          f"> {args.seuil_absolu:g} Mt/TWh ({len(a_examiner)} code(s))")
    print(f"{'-' * 94}")
    if len(a_examiner):
        for _, r in a_examiner.iterrows():
            print(f"  {r['code_recalage']:<28} {r['secteur']:<14} "
                  f"écart {r['ecart_pct']:+,.0f} %  (EPCI {r['somme_epci']:,.2f} / "
                  f"SNBC3 {r['snbc3_national']:,.2f})")
    else:
        print("  Aucun.")

    out_path = OUTPUT_DIR / f"coherence_nationale_{annee}_{date.today()}.csv"
    df_rapport.to_csv(out_path, index=False, sep=";", encoding="utf-8-sig")
    print(f"\n-> Rapport exporté : {out_path}")
    print(
        "\nNote : un écart n'est pas en soi une anomalie - la SNBC3 est une "
        "trajectoire-cible, pas une mesure observée, et les périmètres "
        "sectoriels OREC/SNBC3 peuvent différer - mais ce script ne "
        "diagnostique PAS la cause : il fournit la matière brute pour une "
        "lecture avec connaissance du périmètre OREC vs SNBC3. Le statut "
        "A_EXAMINER n'est qu'un repère de tri, pas une assertion."
    )


if __name__ == "__main__":
    main()
