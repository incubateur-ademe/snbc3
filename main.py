"""
main.py
=======
Orchestrateur CLI du projet Traj GES.

Sous-commandes disponibles :
  extract   -- Télécharge/met en cache les données brutes
  dju       -- Calcule les DJU par EPCI (interpolation IDW depuis stations MF)
  aggregate -- Agrège commune→EPCI et produit la BDD au format long
  export    -- Exporte la BDD en CSV long et XLSX large (format SNBC)
  territorialize -- Applique la trajectoire nationale SNBC3 aux pivots EPCI (brut + recalé)
  update_satool -- Met à jour le tableur Trajectoire GES avec bdd_epci_wide.xlsx
  full      -- Enchaîne extract + dju + aggregate + export + territorialize + update_satool + generate_sources + document_territorialize
  carte     -- Génère la carte interactive des stations Météo-France
  generate_sources -- Génère le fichier Excel de documentation des sources
  document_territorialize -- Génère le fichier Excel de doc du parquet territorialisé (colonnes + code_traj_supra)
  release   -- Paquet de publication figé (résultats, classeur, rapport PDF, manifeste)

Exemples :
  uv run python main.py dju --annee 2020
  uv run python main.py dju --annee 2020 2021 --methode costic
  uv run python main.py aggregate --annee 2020
  uv run python main.py export --annee 2020
  uv run python main.py territorialize --annee 2021
  uv run python main.py update_satool
  uv run python main.py update_satool --traj "chemin/vers/Trajectoire GES.xlsx"
  uv run python main.py full --annee 2020
  uv run python main.py extract --sources meteo --annee 2020 --force
  uv run python main.py extract --sources flores energie_climat --annee 2020
  uv run python main.py carte
  uv run python main.py generate_sources
  uv run python main.py document_territorialize
  uv run python main.py release --version 2.0

Trajectoire nationale autre que la SNBC3 (options globales, avant la sous-commande) :
  uv run python main.py --traj-nationale "snw27_trajges.xlsx" --onglet "HEXAGONE=SnW27 HEX" \
      --nom-scenario SnW27 territorialize --sortie "dossier/resultats"
  uv run python main.py --traj-nationale "snw27_trajges.xlsx" --onglet "HEXAGONE=SnW27 HEX" \
      --nom-scenario SnW27 update_satool --traj "dossier/Trajectoire GES SnW27.xlsx" --zone HEXAGONE
"""

import argparse
import logging
import sys
from pathlib import Path

from config.logging_config import setup_logging
from config.settings import ANNEE_REF, INTERIM_DIR, OUTPUT_DIR
from sources.dju import DJU_SEUIL_CHAUD_DEFAULT, DJU_SEUIL_FROID_DEFAULT

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sous-commande : dju
# ---------------------------------------------------------------------------

def cmd_dju(args):
    """Calcule les DJU par EPCI (+ EPT/Paris) pour une ou plusieurs années."""
    import pandas as pd
    from sources.meteo_france import load_postes, load_daily_temps_for_year
    from sources.dju import compute_station_dju, interpolate_dju_to_epci
    from sources.geo_epci import (
        load_epci_centroids, load_commune_ept_cog, compute_infra_centroids,
        CODE_COMMUNE_PARIS, NOM_COMMUNE_PARIS,
    )

    postes = load_postes(force=args.force)
    epci   = load_epci_centroids(force=args.force)

    if args.epci:
        epci = epci[epci["code_epci"].isin(args.epci)]
        log.info(f"Filtre EPCI: {len(epci)} EPCI")

    # Centroïdes EPT + Paris : geo.api.gouv.fr ne connaît pas ces mailles, on
    # les calcule ici (moyenne pondérée par population des communes membres -
    # cf. compute_infra_centroids). Pondération par population si le RP est
    # disponible pour l'année demandée, sinon poids égal par commune.
    pop_communes = None
    try:
        from sources.insee_rp import extract_indicators as extract_rp
        df_rp, _ = extract_rp(args.annee[0])
        if "POP" in df_rp.columns:
            pop_communes = (
                df_rp[["POP"]].reset_index()
                .rename(columns={"CODGEO": "codgeo", "POP": "population"})
            )
    except Exception as e:
        log.warning(
            f"Population communale indisponible pour pondérer les centroïdes "
            f"EPT/Paris ({e}) - poids égal par commune."
        )

    correspondance_ept = load_commune_ept_cog().rename(
        columns={"code_ept": "code_epci", "nom_ept": "nom_epci"}
    )[["codgeo", "code_epci", "nom_epci"]]
    correspondance_paris = pd.DataFrame([{
        "codgeo": CODE_COMMUNE_PARIS, "code_epci": CODE_COMMUNE_PARIS, "nom_epci": NOM_COMMUNE_PARIS,
    }])

    infra = pd.concat([
        compute_infra_centroids(correspondance_ept, pop_communes),
        compute_infra_centroids(correspondance_paris, pop_communes),
    ], ignore_index=True)
    log.info(f"Centroïdes infra-EPCI : {len(infra)} EPT/Paris")

    for annee in args.annee:
        log.info(f"=== DJU {annee} ===")
        df_temps = load_daily_temps_for_year(annee, force=args.force)

        postes_ok = postes[postes["num_poste"].isin(df_temps["num_poste"].unique())]
        df_dju    = compute_station_dju(df_temps, args.seuil_chaud, args.seuil_froid, args.methode)
        df_epci   = interpolate_dju_to_epci(df_dju, epci, postes_ok)
        df_infra  = interpolate_dju_to_epci(df_dju, infra, postes_ok)
        df_all    = pd.concat([df_epci, df_infra], ignore_index=True)

        INTERIM_DIR.mkdir(parents=True, exist_ok=True)
        out = INTERIM_DIR / f"dju_epci_{annee}.parquet"
        df_all.to_parquet(out, index=False)
        log.info(f"DJU {annee}: {len(df_epci)} EPCI + {len(df_infra)} EPT/Paris → {out.name}")

    print(f"\n✓ DJU calculés pour {args.annee}")


# ---------------------------------------------------------------------------
# Sous-commande : extract
# ---------------------------------------------------------------------------

def cmd_extract(args):
    """Télécharge/met en cache les données brutes."""
    ALL_SOURCES = [
        "geo", "meteo", "insee_rp", "flores", "mobilite_dt",
        "parc_vehicules", "energie_climat", "agreste", "aldo", "omphale",
    ]
    sources = args.sources or ALL_SOURCES

    if "geo" in sources:
        log.info("Extraction référentiel EPCI + COG...")
        from sources.geo_epci import load_epci_centroids, load_commune_epci_cog
        load_epci_centroids(force=args.force)
        load_commune_epci_cog(force=args.force)

    if "meteo" in sources:
        log.info(f"Pré-téléchargement données Météo-France {args.annee}...")
        from sources.meteo_france import load_postes, load_daily_temps_for_year
        load_postes(force=args.force)
        for annee in args.annee:
            load_daily_temps_for_year(annee, force=args.force)

    echecs: list[str] = []
    if "insee_rp" in sources:
        log.info(f"Extraction INSEE RP {args.annee}...")
        from sources.insee_rp import extract_indicators
        for annee in args.annee:
            try:
                extract_indicators(annee)
            except Exception as e:
                echecs.append(f"INSEE RP {annee} : {e}")

    if "flores" in sources:
        log.info(f"Extraction FLORES {args.annee}...")
        from sources.flores import load_flores
        for annee in args.annee:
            try:
                load_flores(annee)
            except Exception as e:
                echecs.append(f"FLORES {annee} : {e}")

    if "mobilite_dt" in sources:
        log.info(f"Extraction Mobilité DT {args.annee}...")
        from sources.mobilite_dt import extract_indicators as extract_mobilite_dt
        for annee in args.annee:
            try:
                extract_mobilite_dt(annee)
            except Exception as e:
                echecs.append(f"Mobilité DT {annee} : {e}")

    if "parc_vehicules" in sources:
        log.info(f"Extraction Parc véhicules {args.annee}...")
        from sources.parc_vehicules import extract_indicators as extract_parc
        for annee in args.annee:
            try:
                extract_parc(annee)
            except Exception as e:
                echecs.append(f"Parc véhicules {annee} : {e}")

    if "energie_climat" in sources:
        log.info(f"Extraction Énergie-climat {args.annee}...")
        from sources.energie_climat import extract_indicators as extract_ec
        for annee in args.annee:
            try:
                extract_ec(annee)
            except Exception as e:
                echecs.append(f"Énergie-climat {annee} : {e}")

    if "agreste" in sources:
        log.info("Extraction Agreste RA...")
        from sources.agreste import load_ra_epci
        try:
            load_ra_epci(2020)
        except Exception as e:
            echecs.append(f"Agreste RA : {e}")

    if "aldo" in sources:
        log.info("Extraction ALDO UTCATF...")
        from sources.aldo import extract_indicators as extract_aldo
        try:
            extract_aldo()
        except Exception as e:
            echecs.append(f"ALDO : {e}")

    if "omphale" in sources:
        log.info("Extraction Omphale...")
        from sources.insee_omphale import load_omphale
        try:
            load_omphale()
        except Exception as e:
            echecs.append(f"Omphale : {e}")

    if echecs:
        for echec in echecs:
            log.error(echec)
        raise SystemExit(
            f"Extraction en échec pour {len(echecs)} source(s) :\n  - " + "\n  - ".join(echecs)
        )

    print(f"\n✓ Extraction terminée pour les sources: {sources}")


# ---------------------------------------------------------------------------
# Sous-commande : aggregate
# ---------------------------------------------------------------------------

def cmd_aggregate(args):
    """Agrège commune→EPCI et produit la BDD au format long."""
    from pipeline.aggregate import build_bdd

    for annee in args.annee:
        log.info(f"=== Agrégation {annee} ===")
        df = build_bdd(annee=annee, sources=args.sources)
        log.info(f"BDD {annee}: {df['code_epci'].nunique()} entités, {len(df)} lignes")

    print(f"\n✓ Agrégation terminée pour {args.annee}")


# ---------------------------------------------------------------------------
# Sous-commande : export
# ---------------------------------------------------------------------------

def cmd_export(args):
    """Exporte la BDD en Parquet long (bdd_epci.parquet, lu par le service
    API à la demande, cf. api/pivot_store.py) et XLSX large."""
    import pandas as pd
    from pipeline.aggregate import build_bdd
    from pipeline.export import export_all

    all_dfs = []
    for annee in args.annee:
        df = build_bdd(annee=annee)
        all_dfs.append(df)

    df_all = pd.concat(all_dfs, ignore_index=True)
    parquet_path, xlsx_path = export_all(df_all, annees=args.annee)

    print(f"\n✓ Export terminé:")
    print(f"  Parquet: {parquet_path}")
    print(f"  XLSX   : {xlsx_path}")


# ---------------------------------------------------------------------------
# Sous-commande : update_satool
# ---------------------------------------------------------------------------

def cmd_update_satool(args):
    """Met à jour le tableur Trajectoire GES avec bdd_epci_wide.xlsx."""
    from config.settings import OUTPUT_DIR, TRAJ_GES_TARGET_PATH, TRAJ_GES_TEMPLATE, zones_demandees
    from standalone_tool.update_standalone_tool import run as satool_run

    traj_path = (Path(args.traj) if args.traj else TRAJ_GES_TARGET_PATH).resolve()
    wide_path = (OUTPUT_DIR / "bdd_epci_wide.xlsx").resolve()
    # Classeur cible absent : créé depuis le template (sinon modifié en place)
    template = None if args.traj and traj_path.exists() else TRAJ_GES_TEMPLATE.resolve()

    if template is not None:
        if not template.exists():
            log.error(f"Template introuvable : {template}")
            sys.exit(1)
    elif not traj_path.exists():
        log.error(f"Fichier introuvable : {traj_path}")
        sys.exit(1)
    if not wide_path.exists():
        log.error(f"bdd_epci_wide.xlsx introuvable : {wide_path}")
        sys.exit(1)

    zones = zones_demandees(args.zone) if args.zone else None
    satool_run(traj_path, wide_path, sheet=args.sheet, zones=zones, template_path=template)


# ---------------------------------------------------------------------------
# Sous-commande : territorialize
# ---------------------------------------------------------------------------

def _territorialize_zones(df_long, zones, annee, dju_variant):
    """
    Territorialise une ou plusieurs zones depuis une BDD long déjà construite et
    concatène les résultats.

    Une zone en échec (typiquement : onglet SNBC3 retraité pas encore produit)
    n'interrompt pas les autres, mais est signalée explicitement - le silence
    sur une zone demandée serait pire qu'un arrêt.

    Retourne (df concaténé ou None, liste des zones en échec).
    """
    import pandas as pd
    from pipeline.territorialize import territorialize

    frames, echecs = [], []
    for zone in zones:  # zones déjà développées (cf. zones_demandees)
        try:
            frames.append(territorialize(
                df_long,
                zone=zone,
                ref_year=annee,
                dju_variant=dju_variant,
            ))
        except Exception as e:
            log.error(f"Territorialisation {zone} : {e}")
            echecs.append(zone)

    return (pd.concat(frames, ignore_index=True) if frames else None), echecs


def _print_territorialize_summary(df_terr, zones, echecs, csv_path, parquet_path):
    reussies = [z for z in zones if z not in echecs]
    print(f"\n✓ Territorialisation terminée ({', '.join(reussies)}):")
    print(f"  CSV     : {csv_path}")
    print(f"  Parquet : {parquet_path}")
    print(f"  Lignes  : {len(df_terr)} ({df_terr['code_epci'].nunique()} EPCI)")
    for zone, g in df_terr.groupby("zone"):
        n_pivot_abs  = (g["qualite"] == "pivot_absent").sum()
        n_non_recale = g["qualite"].str.startswith("non_recale").sum()
        n_pass       = (g["qualite"] == "pass_through_sans_recalage").sum()
        detail = [f"{g['code_epci'].nunique()} EPCI"]
        if n_pivot_abs:
            detail.append(f"{n_pivot_abs} sans pivot")
        if n_non_recale:
            detail.append(f"{n_non_recale} non recalées (donnée observée absente)")
        if n_pass:
            detail.append(f"{n_pass} sans recalage prévu")
        print(f"    {zone:<10} {len(g):>7} lignes - {', '.join(detail)}")
    if echecs:
        print(f"  ⚠ zone(s) en échec, absente(s) du fichier : {', '.join(echecs)}")


def cmd_territorialize(args):
    """Applique la trajectoire nationale SNBC3 aux pivots EPCI (brut + recalé)."""
    from config.settings import zones_demandees
    from pipeline.aggregate import build_bdd
    from pipeline.export import to_territorialise_csv

    args.zone = zones_demandees(args.zone)
    annee = args.annee[0]
    df_long = build_bdd(annee=annee)
    df_terr, echecs = _territorialize_zones(
        df_long, args.zone, annee, args.dju_variant,
    )
    if df_terr is None:
        raise SystemExit(f"Aucune zone territorialisée ({', '.join(echecs)}).")

    sortie = {"output_dir": Path(args.sortie)} if args.sortie else {}
    csv_path, parquet_path = to_territorialise_csv(df_terr, csv=args.csv, **sortie)
    _print_territorialize_summary(
        df_terr, args.zone, echecs, csv_path, parquet_path
    )


# ---------------------------------------------------------------------------
# Sous-commande : full
# ---------------------------------------------------------------------------

def cmd_full(args):
    """Enchaîne extract + dju + aggregate + export + territorialize + update_satool + generate_sources + document_territorialize."""
    import pandas as pd
    from pipeline.aggregate import build_bdd
    from pipeline.export import export_all, to_territorialise_csv
    from config.settings import TERR_DJU_VARIANT, zones_demandees

    log.info("=== Pipeline complet ===")
    args.zone = zones_demandees(args.zone)

    args.sources     = None
    args.methode     = "both"
    args.seuil_chaud = DJU_SEUIL_CHAUD_DEFAULT
    args.seuil_froid = DJU_SEUIL_FROID_DEFAULT
    args.epci        = None

    log.info("--- Étape 1/8 : extract ---")
    cmd_extract(args)

    log.info("--- Étape 2/8 : dju ---")
    args.force = False
    cmd_dju(args)

    log.info("--- Étape 3/8 : aggregate ---")
    all_dfs = []
    for annee in args.annee:
        log.info(f"=== Agrégation {annee} ===")
        df = build_bdd(annee=annee)
        log.info(f"BDD {annee}: {df['code_epci'].nunique()} entités, {len(df)} lignes")
        all_dfs.append(df)

    log.info("--- Étape 4/8 : export ---")
    df_all = pd.concat(all_dfs, ignore_index=True)
    parquet_path, xlsx_path = export_all(df_all, annees=args.annee)
    print(f"\n✓ Export terminé:")
    print(f"  Parquet: {parquet_path}")
    print(f"  XLSX   : {xlsx_path}")

    log.info("--- Étape 5/8 : territorialize ---")
    df_terr, echecs = _territorialize_zones(
        all_dfs[0], args.zone, args.annee[0], TERR_DJU_VARIANT,
    )
    if df_terr is None:
        raise SystemExit(f"Aucune zone territorialisée ({', '.join(echecs)}).")
    csv_path, parquet_path = to_territorialise_csv(df_terr, csv=getattr(args, "csv", False))
    _print_territorialize_summary(
        df_terr, args.zone, echecs, csv_path, parquet_path
    )

    log.info("--- Étape 6/8 : update_satool ---")
    args.traj  = None
    args.sheet = "Data_EPCI"
    cmd_update_satool(args)

    log.info("--- Étape 7/8 : generate_sources ---")
    cmd_generate_sources(args)

    log.info("--- Étape 8/8 : document_territorialize ---")
    cmd_document_territorialize(args)

    print(f"\n✓ Pipeline complet terminé pour {args.annee}")


# ---------------------------------------------------------------------------
# Sous-commande : carte
# ---------------------------------------------------------------------------

def cmd_carte(args):
    """Génère la carte interactive des stations Météo-France."""
    try:
        import scripts.carte_stations
        scripts.carte_stations.main()
    except AttributeError:
        import runpy
        runpy.run_path("scripts/carte_stations.py")
    print("\n✓ Carte générée: carte_stations.html")


# ---------------------------------------------------------------------------
# Sous-commande : generate_sources
# ---------------------------------------------------------------------------

def cmd_generate_sources(args):
    """Génère le fichier Excel de documentation des sources de données."""
    from scripts.generate_sources_doc import main as generate_main
    generate_main()


# ---------------------------------------------------------------------------
# Sous-commande : document_territorialize
# ---------------------------------------------------------------------------

def cmd_document_territorialize(args):
    """Génère le fichier Excel de doc du parquet territorialisé (colonnes + code_traj_supra)."""
    from scripts.generate_territorialize_doc import main as generate_main
    generate_main()


# ---------------------------------------------------------------------------
# Sous-commande : release
# ---------------------------------------------------------------------------

def cmd_release(args):
    """Paquet de publication figé : résultats, classeur, rapport PDF, manifeste."""
    from scripts.release import ReleaseError, publier

    try:
        dest = publier(args.version, force=args.force, pdf=not args.sans_pdf)
    except ReleaseError as e:
        raise SystemExit(f"Release impossible : {e}")
    print(f"\n✓ Release V{args.version} : {dest}")
    for f in sorted(dest.iterdir()):
        print(f"  {f.name}")


# ---------------------------------------------------------------------------
# Parseur CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Trajectoire GES territorialisée de la SNBC à l'échelle EPCI - pipeline de calcul",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Mode verbeux (DEBUG)")
    parser.add_argument(
        "--traj-nationale", dest="traj_nationale", default=None, metavar="CHEMIN_XLSX",
        help="Classeur de la trajectoire nationale à territorialiser, au format de "
             "« SNBC3 retraitée.xlsx » (défaut : SNBC3_RETRAITEE_PATH)",
    )
    parser.add_argument(
        "--onglet", action="append", default=None, metavar="ZONE=ONGLET",
        help="Onglet de la trajectoire d'une zone ou d'un groupe, répétable ; remplace "
             "toute la correspondance SNBC3_SHEETS (zones non citées : non territorialisables)",
    )
    parser.add_argument(
        "--nom-scenario", dest="nom_scenario", default=None,
        help="Nom de la trajectoire nationale dans le classeur (plage nom_scenario)",
    )

    sub = parser.add_subparsers(dest="commande", required=True)

    # --- dju ---
    p_dju = sub.add_parser("dju", help="Calcule les DJU par EPCI")
    p_dju.add_argument("--annee", type=int, nargs="+", default=[ANNEE_REF])
    p_dju.add_argument("--methode", choices=["meteo", "costic", "both"], default="both")
    p_dju.add_argument("--seuil_chaud", type=float, default=DJU_SEUIL_CHAUD_DEFAULT)
    p_dju.add_argument("--seuil_froid", type=float, default=DJU_SEUIL_FROID_DEFAULT)
    p_dju.add_argument("--epci", type=str, nargs="*", metavar="CODE_EPCI")
    p_dju.add_argument("--force", action="store_true", help="Forcer le re-téléchargement")
    p_dju.set_defaults(func=cmd_dju)

    # --- extract ---
    p_ext = sub.add_parser("extract", help="Télécharge les données brutes")
    p_ext.add_argument("--annee", type=int, nargs="+", default=[ANNEE_REF])
    p_ext.add_argument(
        "--sources", nargs="+",
        choices=[
            "geo", "meteo", "insee_rp", "flores", "mobilite_dt",
            "parc_vehicules", "energie_climat", "agreste", "aldo", "omphale",
        ],
        default=None, metavar="SOURCE",
    )
    p_ext.add_argument("--force", action="store_true")
    p_ext.set_defaults(func=cmd_extract)

    # --- aggregate ---
    p_agg = sub.add_parser("aggregate", help="Agrège commune→EPCI")
    p_agg.add_argument("--annee", type=int, nargs="+", default=[ANNEE_REF])
    p_agg.add_argument(
        "--sources", nargs="+",
        choices=["insee_rp", "flores", "agreste", "dju", "omphale", "mobilite_dt", "parc_vehicules", "energie_climat", "citepa_igt", "aldo"],
        default=None,
    )
    p_agg.set_defaults(func=cmd_aggregate)

    # --- export ---
    p_exp = sub.add_parser("export", help="Exporte Parquet long (API) + XLSX large")
    p_exp.add_argument("--annee", type=int, nargs="+", default=[ANNEE_REF])
    p_exp.set_defaults(func=cmd_export)

    # --- update_satool ---
    p_inj = sub.add_parser("update_satool", help="Met à jour le tableur Trajectoire GES avec bdd_epci_wide.xlsx")
    p_inj.add_argument(
        "--traj", type=str, default=None, metavar="CHEMIN_XLSX",
        help="Chemin vers le tableur cible (défaut : TRAJ_GES_TARGET_PATH dans config/settings.py)",
    )
    p_inj.add_argument(
        "--sheet", default="Data_EPCI",
        help="Nom de l'onglet cible (défaut : Data_EPCI)",
    )
    p_inj.add_argument(
        "--zone", nargs="+", default=None, metavar="ZONE",
        help="Zones portées par le classeur ; DROM désigne les 4 DROM produits "
             "(défaut : ZONES_TERRITORIALISEES)",
    )
    p_inj.set_defaults(func=cmd_update_satool)

    # --- territorialize ---
    from config.settings import (
        GROUPES_ZONES, TERR_DJU_VARIANT, TERR_ZONE_DEFAUT, ZONES_TERRITORIALISEES,
    )
    choix_zones = [*ZONES_TERRITORIALISEES, *GROUPES_ZONES]
    p_terr = sub.add_parser(
        "territorialize",
        help="Applique la trajectoire nationale SNBC3 aux pivots EPCI (brut + recalé OREC)",
    )
    p_terr.add_argument("--annee", type=int, nargs="+", default=[ANNEE_REF])
    p_terr.add_argument("--zone", nargs="+", default=[TERR_ZONE_DEFAUT],
                        choices=choix_zones,
                        help="Zones à territorialiser (résultats concaténés dans un seul "
                             "fichier). DROM désigne les quatre DROM produits.")
    p_terr.add_argument("--dju-variant", dest="dju_variant", default=TERR_DJU_VARIANT,
                        choices=["COSTIC", "METEO"])
    p_terr.add_argument("--sortie", default=None, metavar="DOSSIER",
                        help="Dossier des résultats territorialisés (défaut : data/output)")
    p_terr.add_argument("--csv", action="store_true",
                        help="Écrire aussi le CSV (volumineux ; le parquet suffit "
                             "aux scripts de contrôle)")
    p_terr.set_defaults(func=cmd_territorialize)

    # --- full ---
    p_full = sub.add_parser("full", help="Pipeline complet (extract+dju+aggregate+export+territorialize+update_satool+generate_sources+document_territorialize)")
    p_full.add_argument("--annee", type=int, nargs="+", default=[ANNEE_REF])
    p_full.add_argument("--zone", nargs="+", default=[TERR_ZONE_DEFAUT, "DROM"],
                        choices=choix_zones)
    p_full.add_argument("--force", action="store_true")
    p_full.set_defaults(func=cmd_full)

    # --- carte ---
    p_carte = sub.add_parser("carte", help="Génère la carte des stations MF")
    p_carte.set_defaults(func=cmd_carte)

    # --- generate_sources ---
    p_gensrc = sub.add_parser(
        "generate_sources",
        help="Génère le fichier Excel de documentation des sources (docs/Sources donnees.xlsx)",
    )
    p_gensrc.set_defaults(func=cmd_generate_sources)

    # --- document_territorialize ---
    p_docterr = sub.add_parser(
        "document_territorialize",
        help="Génère le fichier Excel de doc du parquet territorialisé (docs/Territorialisation.xlsx)",
    )
    p_docterr.set_defaults(func=cmd_document_territorialize)

    # --- release ---
    p_rel = sub.add_parser(
        "release",
        help="Paquet de publication figé dans releases/V<version>/ (résultats, classeur, "
             "rapport PDF, manifeste), sans recalcul, à joindre à une release GitHub",
    )
    p_rel.add_argument("--version", required=True,
                       help="Numéro de version publié (ex. 2.0)")
    p_rel.add_argument("--force", action="store_true",
                       help="Écraser une release existante de même version")
    p_rel.add_argument("--sans-pdf", dest="sans_pdf", action="store_true",
                       help="Publier sans le rapport PDF de docs/")
    p_rel.set_defaults(func=cmd_release)

    return parser


if __name__ == "__main__":
    # Les messages de succès utilisent des caractères hors cp1252 (✓) : sans
    # ça, une console Windows non-UTF8 plante juste après un run par ailleurs
    # réussi (fichiers déjà écrits), avec un code de sortie non nul trompeur.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = build_parser()
    args   = parser.parse_args()

    setup_logging(verbose=args.verbose)

    if args.traj_nationale or args.onglet or args.nom_scenario:
        from config.settings import utiliser_traj_nationale
        onglets = None
        if args.onglet:
            try:
                onglets = dict(o.split("=", 1) for o in args.onglet)
            except ValueError:
                parser.error("--onglet attend ZONE=ONGLET")
        utiliser_traj_nationale(args.traj_nationale, onglets, args.nom_scenario)

    args.func(args)
