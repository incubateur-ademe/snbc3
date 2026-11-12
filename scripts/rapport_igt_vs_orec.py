"""
scripts/rapport_igt_vs_orec.py
==============================
Rapport (pour information, non bloquant) en deux volets, servant à documenter
le recours à l'inventaire GES territorialisé (IGT) du CITEPA sur les DROM.

Volet 1 - Substituabilité IGT / OREC
    Compare, EPCI par EPCI et secteur par secteur, l'IGT aux données OREC là
    où les deux existent, c'est-à-dire dans l'hexagone. Les DROM ne disposant pas
    d'observatoire (cf. config/settings.py::IGT_ZONES), c'est le seul terrain
    où la fiabilité du repli peut être mesurée. Objectif : savoir, indicateur
    par indicateur, si l'IGT est un substitut acceptable ou seulement un
    ordre de grandeur.

    Rappel : l'IGT est une estimation descendante (désagrégation de
    l'inventaire national) et l'OREC une compilation cadastrale régionale.
    Un écart n'est donc pas une erreur de l'un ou de l'autre, mais peut
    traduire une convention de localisation différente - typiquement pour les
    déchets et la branche énergie, dont les émissions sont concentrées sur les
    sites de traitement ou de production.

Volet 2 - Concentration des émissions "autres transports" dans les DROM
    Mesure, par DROM, la part des émissions du poste "autres transports"
    (aérien et maritime domestiques) portée par l'EPCI le plus émetteur.
    Ce poste est réparti de façon strictement cadastrale : l'intégralité des
    émissions d'un vol ou d'une liaison maritime est affectée à l'EPCI qui
    porte l'aéroport ou le port. L'approche alternative, en responsabilité
    (émissions des acteurs du territoire), donnerait une répartition très
    différente. Ce volet chiffre l'enjeu avant arbitrage.

Ce script n'est PAS un test : aucune assertion, code de sortie toujours 0.

Usage :
    uv run python scripts/rapport_igt_vs_orec.py
    uv run python scripts/rapport_igt_vs_orec.py --annee 2021 --rebuild
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Seuil (ktCO2e/an) en dessous duquel un couple (EPCI, indicateur) est écarté
# du calcul de ratio : sur des valeurs quasi nulles, le ratio explose sans
# que l'écart absolu soit significatif.
SEUIL_KT = 1.0


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


def _volet_substituabilite(annee: int):
    """
    Compare IGT et OREC sur les EPCI hexagonaux.

    L'IGT n'étant pas injecté dans l'hexagone (IGT_ZONES = les zones DROM), il est
    rechargé et agrégé ici directement depuis la source.
    """
    import pandas as pd

    from config.settings import OUTPUT_DIR
    from sources.citepa_igt import IGT_CODES_EC, extract_indicators as extract_igt
    from sources.energie_climat import extract_indicators as extract_orec
    from sources.geo_epci import load_commune_epci_cog

    igt_com = extract_igt()
    corr = load_commune_epci_cog()[["codgeo", "code_epci"]]
    igt = (
        igt_com.reset_index().rename(columns={"CODGEO": "codgeo"})
        .merge(corr, on="codgeo", how="inner")
        .groupby("code_epci")[IGT_CODES_EC].sum()
    )

    orec, _ = extract_orec(annee)
    codes = [c for c in IGT_CODES_EC if c in orec.columns]
    common = orec.index.intersection(igt.index)

    rows = []
    for code in codes:
        a, b = orec.loc[common, code], igt.loc[common, code]
        m = a.notna() & b.notna() & (a.abs() > SEUIL_KT)
        if not m.any():
            continue
        ratio = b[m] / a[m]
        rows.append({
            "indicateur": code,
            "n_epci": int(m.sum()),
            "somme_orec_kt": a[m].sum(),
            "somme_igt_kt": b[m].sum(),
            "ecart_somme_pct": 100 * (b[m].sum() / a[m].sum() - 1),
            "ratio_median": ratio.median(),
            "ratio_p10": ratio.quantile(0.10),
            "ratio_p90": ratio.quantile(0.90),
            "part_epci_ecart_sup_50pct": 100 * ((ratio - 1).abs() > 0.5).mean(),
        })

    df = pd.DataFrame(rows)
    out = OUTPUT_DIR / f"igt_vs_orec_{annee}_{date.today()}.csv"
    df.to_csv(out, index=False, sep=";", encoding="utf-8-sig")

    pd.set_option("display.width", 200)
    print(f"\n{'=' * 100}")
    print(f"VOLET 1 - Substituabilité IGT / OREC, EPCI hexagonaux, année {annee}")
    print(f"{'=' * 100}")
    print(f"{len(common)} EPCI couverts par les deux sources "
          f"(couples écartés si |OREC| <= {SEUIL_KT} ktCO2e/an)\n")
    print(df.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    print(f"\n-> {out}")
    print(
        "\nLecture : 'ecart_somme_pct' mesure l'accord sur le total national, "
        "'ratio_median' et 'part_epci_ecart_sup_50pct' mesurent l'accord sur la "
        "répartition territoriale. Un total juste avec une répartition dispersée "
        "signale une convention de localisation différente, pas une erreur de "
        "niveau. Les indicateurs qui servent à la fois de pivot et de recalage "
        "(déchets, branche énergie, autres transports) reproduisent exactement la "
        "donnée observée en année de référence : l'écart n'y crée pas "
        "d'incohérence interne, il déplace le point de départ affiché."
    )
    return df


def _volet_concentration_transport(df_long, annee: int):
    """Concentration du poste 'autres transports' par DROM (enjeu aérien/maritime)."""
    import pandas as pd

    from config.settings import CODES_TOTAUX as AGREGATS, OUTPUT_DIR, ZONES_DROM

    CODE = "EC_EMIS_AUTRE_TRANSPORT"
    INTL = "IGT_EMIS_TRANSPORT_INTL"

    d = df_long[
        df_long["zone"].isin(ZONES_DROM)
        & (df_long["annee"] == annee)
        & (~df_long["code_epci"].isin(AGREGATS))
        & (df_long["code_indicateur"].isin([CODE, INTL, "EC_EMIS_TOTAL", "POP"]))
    ]
    if d.empty:
        print("\nVOLET 2 : aucune donnée DROM disponible (IGT non injecté ?).")
        return pd.DataFrame()

    piv = d.pivot_table(index=["code_departement", "code_epci", "nom_epci"],
                        columns="code_indicateur", values="valeur").reset_index()
    for c in (CODE, INTL, "EC_EMIS_TOTAL", "POP"):
        if c not in piv.columns:
            piv[c] = float("nan")

    rows = []
    for dep, g in piv.groupby("code_departement"):
        tot = g[CODE].sum()
        if not tot:
            continue
        top = g.loc[g[CODE].idxmax()]
        pop_tot = g["POP"].sum()
        rows.append({
            "departement": dep,
            "n_epci": len(g),
            "autres_transports_kt": tot,
            "part_dans_emissions_drom_pct": 100 * tot / g["EC_EMIS_TOTAL"].sum()
                                            if g["EC_EMIS_TOTAL"].sum() else float("nan"),
            "epci_le_plus_emetteur": top["nom_epci"],
            "part_epci_top_pct": 100 * top[CODE] / tot,
            "part_pop_epci_top_pct": 100 * top["POP"] / pop_tot if pop_tot else float("nan"),
            # Poids du poste dans l'inventaire de l'EPCI qui le porte : c'est
            # cette part qui est directement en jeu dans l'arbitrage cadastral
            # vs responsabilité, du point de vue de la collectivité concernée.
            "poids_dans_inventaire_epci_top_pct": 100 * top[CODE] / top["EC_EMIS_TOTAL"]
                                                  if top["EC_EMIS_TOTAL"] else float("nan"),
            "soutes_intl_kt": g[INTL].sum(),
        })

    df = pd.DataFrame(rows).sort_values("autres_transports_kt", ascending=False)
    out = OUTPUT_DIR / f"igt_concentration_transport_drom_{annee}_{date.today()}.csv"
    df.to_csv(out, index=False, sep=";", encoding="utf-8-sig")

    print(f"\n{'=' * 100}")
    print(f"VOLET 2 - Concentration du poste 'autres transports' par DROM, année {annee}")
    print(f"{'=' * 100}\n")
    print(df.to_string(index=False, float_format=lambda v: f"{v:,.1f}"))
    print(f"\n-> {out}")
    print(
        "\nLecture : comparer 'part_epci_top_pct' à 'part_pop_epci_top_pct'. Un "
        "écart important signifie que l'affectation cadastrale concentre les "
        "émissions aériennes et maritimes sur l'EPCI équipé, indépendamment de "
        "la population desservie. 'soutes_intl_kt' est hors total inventaire et "
        "n'entre dans aucun calcul de l'outil : il n'est indiqué que pour "
        "situer la part des liaisons internationales."
    )
    return df


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--annee", type=int, default=None, help="Année (défaut : ANNEE_REF)")
    parser.add_argument("--rebuild", action="store_true", help="Force la reconstruction de la BDD")
    args = parser.parse_args()

    from config.settings import ANNEE_REF

    annee = args.annee or ANNEE_REF

    _volet_substituabilite(annee)
    df_long = _load_bdd(annee, args.rebuild)
    _volet_concentration_transport(df_long, annee)

    print("\nPour information uniquement, pas d'assertion.")


if __name__ == "__main__":
    main()
