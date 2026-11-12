"""
scripts/publier_snbc3.py
========================
Génère la version publiée de la trajectoire SNBC3 retraitée
(`SNBC3_RETRAITEE_PATH`, data/input/SNBC3 retraitée.xlsx, versionnée) depuis
le classeur de travail (`SNBC3_TRAVAIL_PATH`, data/input/travail/, non
versionné : onglets bruts DGEC, formules et détail du retraitement, bilans
énergie par territoire).

Le classeur publié ne porte que des valeurs :
  - « Lisez-moi » : source, contenu, date de génération ;
  - un onglet par onglet retraité (`ONGLETS_RETRAITES`) : les seules lignes des
    trajectoires par pivots (sous-secteurs et totaux, GES puis conso), colonnes
    Indicateur à Périmètre géographique puis années. Les codes des
    sous-secteurs restent en colonne D : le pipeline lit les deux classeurs à
    l'identique ;
  - `onglet_parts` (config/traj_supra.yaml, `trajectoire_derivee`) : part de
    chaque zone du groupe dans la conso finale de chaque secteur, par année
    des bilans. Le pipeline n'a besoin des bilans que pour ces parts, ce qui
    évite de publier les bilans DGEC eux-mêmes.

Le classeur de travail doit avoir été enregistré dans Excel (valeurs des
formules à jour). Après génération, le script relit le classeur publié avec
le code du pipeline (`load_national_traj`, `parts_bilans`) : tous les codes de
config/traj_supra.yaml doivent y être.

À relancer après chaque modification du classeur de travail, puis versionner
le classeur publié.

Usage :
    uv run python scripts/publier_snbc3.py
    uv run python scripts/publier_snbc3.py --travail <classeur> --sortie <classeur>
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Onglets retraités publiés : ceux du pipeline (SNBC3_SHEETS) et la France
# entière (périmètre Kyoto), dont l'hexagone est la différence avec les DROM.
ONGLETS_RETRAITES = ["SNBC3 retraitée Kyoto", "SNBC3 retraitée HEX", "SNBC3 retraitée DROM"]

# Blocs publiés (colonne A) : trajectoires finales par pivots. Les autres blocs
# (format SECTEN, conversion PCAET, étapes de désagrégation, vérifications)
# restent dans le classeur de travail.
BLOCS_PUBLIES = {"Trajectoire tous GES par pivots", "Consommation finale par secteur par pivots"}

# Colonnes descriptives publiées, dans cet ordre (le code reste en colonne D).
COLONNES = ["Indicateur", "Sous-indicateur / secteur", "Usage", "Code traj supra",
            "Unité", "Périmètre géographique"]

# Écart toléré entre Σ des bilans des zones et bilan du groupe : au-delà, les
# onglets ne viennent pas du même envoi DGEC et les parts seraient fausses.
BILANS_TOLERANCE_SOMME = 1e-3


def _annees(header) -> dict[int, int]:
    """Indice de colonne -> année, pour les en-têtes entiers 2000-2100."""
    return {i: int(v) for i, v in enumerate(header)
            if isinstance(v, (int, float)) and 2000 <= int(v) <= 2100}


def lignes_retraitees(ws) -> tuple[list, list[list]]:
    """
    En-tête et lignes publiées d'un onglet retraité : lignes des blocs
    `BLOCS_PUBLIES`, colonnes `COLONNES` puis années.
    """
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    if [str(h).strip() if h else "" for h in header[:len(COLONNES)]] != COLONNES:
        raise RuntimeError(f"« {ws.title} » : en-tête inattendu {header[:len(COLONNES)]}")
    annees = _annees(header)
    out = []
    for r in rows:
        if r[0] not in BLOCS_PUBLIES:
            continue
        valeurs = [r[i] for i in annees]
        if any(not isinstance(v, (int, float)) for v in valeurs):
            raise RuntimeError(
                f"« {ws.title} », {r[1]} / {r[2]} ({r[3]}) : valeur absente ou non "
                f"numérique. Classeur de travail à ouvrir et enregistrer dans Excel ?"
            )
        out.append([r[i] for i in range(len(COLONNES))] + valeurs)
    if not out:
        raise RuntimeError(f"« {ws.title} » : aucune ligne des blocs {sorted(BLOCS_PUBLIES)}")
    return COLONNES + list(annees.values()), out


def _conso_bilan(ws) -> dict[int, dict[str, float]]:
    """
    Conso finale par secteur (colonne « Total », tous vecteurs) de chaque bloc
    annuel d'un onglet de bilan énergie DGEC : {année: {libellé: GWh}}.

    Un bloc commence par une ligne d'en-tête portant l'année en colonne B et
    « GWh » en colonne C. Seule la première occurrence d'un libellé est
    retenue dans un bloc.
    """
    out: dict[int, dict[str, float]] = {}
    bloc, i_total = None, None
    for r in ws.iter_rows(values_only=True):
        c1 = str(r[1]).strip() if len(r) > 1 and r[1] is not None else ""
        c2 = str(r[2]).strip() if len(r) > 2 and r[2] is not None else ""
        if c2 == "GWh":                           # en-tête d'un bloc annuel
            bloc = int(c1) if c1.isdigit() else None
            i_total = next(
                (i for i, v in enumerate(r)
                 if isinstance(v, str) and v.strip() == "Total"), None)
            continue
        if bloc is not None and c2 and i_total is not None and len(r) > i_total:
            v = r[i_total]
            if isinstance(v, (int, float)):
                out.setdefault(bloc, {}).setdefault(c2, float(v))
    return out


def parts_depuis_bilans(wb, groupe: str) -> tuple[list, list[list]]:
    """
    Part de chaque zone du groupe dans la conso finale de chaque secteur, par
    année des bilans : en-tête (Zone, Secteur, années) et une ligne par
    (zone, secteur). Secteurs : clés de `secteurs` dans `trajectoire_derivee`
    (les alias sont développés à la lecture, par le pipeline).

    Contrôle : Σ des zones = groupe, à `BILANS_TOLERANCE_SOMME` près, pour
    chaque secteur et chaque année. Un écart signale des onglets issus
    d'envois différents : erreur plutôt que parts silencieusement fausses.
    """
    from config.settings import GROUPES_ZONES
    from pipeline.territorialize import load_zone_config

    source = load_zone_config(groupe)["trajectoire_derivee"]
    onglets = {"_groupe": source["onglet_bilan_groupe"]}
    for zone in GROUPES_ZONES[groupe]:
        onglet = load_zone_config(zone).get("onglet_bilan")
        if not onglet:
            raise RuntimeError(f"Zone {zone} : `onglet_bilan` non déclaré dans config/traj_supra.yaml")
        onglets[zone] = onglet
    manquants = [o for o in onglets.values() if o not in wb.sheetnames]
    if manquants:
        raise RuntimeError(f"Onglet(s) de bilan absent(s) du classeur de travail : {manquants}")
    bilans = {z: _conso_bilan(wb[o]) for z, o in onglets.items()}

    annees = sorted(bilans["_groupe"])
    secteurs = dict(source["secteurs"])
    lignes = []
    for zone in GROUPES_ZONES[groupe]:
        for secteur in secteurs:
            lignes.append([zone, secteur])
    for annee in annees:
        for k, (secteur, libelle) in enumerate(secteurs.items()):
            vg = bilans["_groupe"][annee].get(libelle)
            if not vg:
                raise RuntimeError(
                    f"Bilans : secteur « {libelle} » absent ou nul dans "
                    f"« {onglets['_groupe']} » ({annee})."
                )
            somme = 0.0
            for j, zone in enumerate(GROUPES_ZONES[groupe]):
                vz = bilans[zone].get(annee, {}).get(libelle)
                if vz is None:
                    raise RuntimeError(
                        f"Bilans : secteur « {libelle} » absent de « {onglets[zone]} » ({annee})."
                    )
                somme += vz
                lignes[j * len(secteurs) + k].append(vz / vg)
            if abs(somme / vg - 1.0) > BILANS_TOLERANCE_SOMME:
                raise RuntimeError(
                    f"Bilans : Σ des zones ≠ « {onglets['_groupe']} » pour « {libelle} » "
                    f"en {annee} ({somme:.1f} contre {vg:.1f} GWh). Les onglets de "
                    f"bilans ne viennent pas du même envoi."
                )
    return ["Zone", "Secteur", *annees], lignes


def _lisez_moi(onglet_parts: str) -> list[list]:
    return [
        ["Trajectoire SNBC3 retraitée - version publiée"],
        [],
        ["Source", "SNBC3, données DGEC du scénario AMS run 3"],
        ["Retraitement", "Conversion du format SECTEN au format PCAET (réaffectation des émissions "
                         "de la production centralisée d'électricité et de chaleur), passage au "
                         "périmètre hexagone (Kyoto - DROM), désagrégation par pivots de "
                         "territorialisation. Méthode : rapport méthodologique de l'outil."],
        [],
        ["Onglet", "Contenu"],
        ["SNBC3 retraitée Kyoto", "Trajectoires par sous-secteur, France entière (périmètre Kyoto), 2020-2050."],
        ["SNBC3 retraitée HEX", "Idem, hexagone (Kyoto - DROM). Trajectoire territorialisée sur les EPCI de l'hexagone."],
        ["SNBC3 retraitée DROM", "Idem, ensemble des DROM. Trajectoire de chaque DROM dérivée à l'exécution."],
        [onglet_parts, "Part de chaque DROM dans la consommation finale de chaque secteur des DROM, "
                       "par année des bilans énergie DGEC. Sert au calage de la consommation de chaque DROM."],
        [],
        ["Colonnes", "Code traj supra (colonne D) : identifiant du sous-secteur, cf. config/traj_supra.yaml. "
                     "Unités : MtCO2eq (GES), TWh (consommation), colonne Unité. Lignes Total : sommes des sous-secteurs."],
        [],
        ["Généré", f"Le {date.today().isoformat()} par scripts/publier_snbc3.py, depuis le classeur "
                   f"de travail (données sources et détail du retraitement, non publié)."],
    ]


def publier(travail: Path, sortie: Path) -> None:
    import openpyxl
    from openpyxl.utils import get_column_letter

    from config.settings import GROUPES_ZONES, SNBC3_SHEETS
    from pipeline.territorialize import load_zone_config

    hors_publication = set(SNBC3_SHEETS.values()) - set(ONGLETS_RETRAITES)
    if hors_publication:
        raise RuntimeError(f"Onglets du pipeline absents de ONGLETS_RETRAITES : {hors_publication}")

    src = openpyxl.load_workbook(travail, read_only=True, data_only=True)
    feuilles = {o: lignes_retraitees(src[o]) for o in ONGLETS_RETRAITES}
    parts = {}
    for groupe in GROUPES_ZONES:
        onglet = load_zone_config(groupe)["trajectoire_derivee"]["onglet_parts"]
        parts[onglet] = parts_depuis_bilans(src, groupe)
    src.close()

    wb = openpyxl.Workbook()
    wb.properties.creator = "Adrien Jacob (nW)"
    wb.properties.lastModifiedBy = "Adrien Jacob (nW)"
    ws = wb.active
    ws.title = "Lisez-moi"
    for ligne in _lisez_moi(next(iter(parts))):
        ws.append(ligne)
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 120

    for titre, (header, lignes) in {**feuilles, **parts}.items():
        ws = wb.create_sheet(titre)
        ws.append(header)
        for ligne in lignes:
            ws.append(ligne)
        ws.freeze_panes = "C2" if titre in parts else "G2"
        n_desc = 2 if titre in parts else len(COLONNES)
        for i in range(1, n_desc + 1):
            ws.column_dimensions[get_column_letter(i)].width = 14 if titre in parts else 22

    sortie.parent.mkdir(parents=True, exist_ok=True)
    wb.save(sortie)
    print(f"Classeur publié : {sortie}")
    for titre, (_, lignes) in {**feuilles, **parts}.items():
        print(f"  {titre} : {len(lignes)} lignes")


def verifier(sortie: Path) -> None:
    """Relit le classeur publié avec le code du pipeline."""
    from config.settings import GROUPES_ZONES, SNBC3_SHEETS
    from pipeline.territorialize import load_correspondance, load_national_traj, parts_bilans

    for zone in SNBC3_SHEETS:
        codes = set(load_correspondance(zone)["code_traj_supra"])
        load_national_traj(codes, zone, sortie)
    for groupe in GROUPES_ZONES:
        parts_bilans(groupe, sortie)
    print("Relecture par le pipeline : OK")


def main():
    from config.settings import SNBC3_RETRAITEE_PATH, SNBC3_TRAVAIL_PATH

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--travail", type=Path, default=SNBC3_TRAVAIL_PATH,
                   help="Classeur de travail (défaut : SNBC3_TRAVAIL_PATH)")
    p.add_argument("--sortie", type=Path, default=SNBC3_RETRAITEE_PATH,
                   help="Classeur publié (défaut : SNBC3_RETRAITEE_PATH)")
    args = p.parse_args()
    if not args.travail.exists():
        sys.exit(f"Classeur de travail introuvable : {args.travail}")
    publier(args.travail, args.sortie)
    verifier(args.sortie)


if __name__ == "__main__":
    main()
