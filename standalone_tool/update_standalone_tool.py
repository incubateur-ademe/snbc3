"""
standalone_tool/update_standalone_tool.py
===================================
Met à jour le tableur Trajectoire GES de référence avec les données issues de
bdd_epci_wide.xlsx (sortie du pipeline).

Par défaut, part du template (standalone_tool/template/) et écrit l'output
(standalone_tool/output/). Si un chemin explicite est fourni, modifie le
fichier en place.

Usage :
    python -m standalone_tool.update_standalone_tool                  # template -> output
    python -m standalone_tool.update_standalone_tool <chemin.xlsx>    # modification en place
    python -m standalone_tool.update_standalone_tool --only-calcul    # Traj_supra + CALCUL uniquement
    python -m standalone_tool.update_standalone_tool --reset-template # output -> template nettoyé

Écriture par openpyxl (`standalone_tool/classeur.py`) : ni Excel ni Windows
requis. Le classeur est enregistré sans valeurs calculées, Excel recalcule à
l'ouverture. Le fichier cible doit être fermé dans Excel.
"""

import argparse
import re
import sys
from pathlib import Path

# Permet l'exécution directe : ajoute la racine du projet au path pour que
# `config` et `pipeline` soient importables.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from standalone_tool.classeur import bgr  # noqa: E402


def _ouvrir(traj_path: Path, template_path: Path | None):
    """Classeur à mettre à jour : le template si fourni (écrit ensuite vers
    traj_path), sinon traj_path lui-même."""
    from standalone_tool.classeur import Classeur
    source = template_path if template_path is not None else traj_path
    print(f"Ouverture de {source} …")
    return Classeur(source)


def _enregistrer(wb, traj_path: Path) -> None:
    """Enregistre le classeur ; message explicite s'il est ouvert dans Excel."""
    traj_path.parent.mkdir(parents=True, exist_ok=True)
    print("  Sauvegarde …")
    try:
        wb.save(traj_path)
    except PermissionError:
        raise ValueError(
            f"Impossible d'écrire {traj_path} : fichier ouvert dans Excel ? "
            f"Le fermer puis relancer.") from None


def _adjust_formula_rows(formula: str, delta: int) -> str:
    """Décale les références de lignes relatives dans une formule Excel de `delta` lignes."""
    if not isinstance(formula, str) or not formula.startswith("="):
        return formula

    def _replace(m: re.Match) -> str:
        col_ref, dollar, row_num = m.group(1), m.group(2), int(m.group(3))
        return col_ref + dollar + str(row_num if dollar == "$" else row_num + delta)

    return re.sub(r"(\$?[A-Za-z]{1,3})(\$?)(\d+)", _replace, formula)


def _update_meta_ranges(wb) -> None:
    """Met a jour les plages nommees nom_scenario et Annee_ref depuis le YAML et settings
    (nom_scenario : nom de la trajectoire nationale si elle a été remplacée)."""
    import yaml
    from config.settings import ANNEE_REF, ROOT_DIR, TRAJ_NATIONALE

    path = ROOT_DIR / "config" / "traj_supra.yaml"
    with open(path, encoding="utf-8") as f:
        meta = yaml.safe_load(f).get("meta", {})

    description = TRAJ_NATIONALE.nom or meta.get("description", "")

    for name, value in [("nom_scenario", description), ("Annee_ref", ANNEE_REF)]:
        try:
            rng = wb.names[name].refers_to_range
            rng.value = value
        except Exception:
            pass

    print(f'  → Plages nommées : nom_scenario="{description}", Annee_ref={ANNEE_REF}')


# Alerte affichée sous la mention de version (onglet INTRODUCTION) ; None la
# retire, une fois l'outil finalisé.
ALERTE_VERSION: str | None = (
    "Version de travail, non finalisée : transmise pour prise en main, "
    "la méthode et les résultats peuvent encore évoluer."
)
_CELLULE_VERSION = ("Notice", "B3")        # « Version 2 », reprise par INTRODUCTION!B3
_CELLULE_ALERTE = ("INTRODUCTION", "B4")


def _update_mention_version(wb) -> None:
    """
    Date la mention de version (« Version 2 du JJ/MM/AAAA », date de génération)
    et écrit l'alerte `ALERTE_VERSION` sous celle-ci. Une date déjà présente est
    remplacée, pour qu'une mise à jour en place ne l'empile pas.
    """
    from datetime import date

    noms = [s.name for s in wb.sheets]
    onglet, adresse = _CELLULE_VERSION
    if onglet not in noms:
        print(f"  AVERTISSEMENT : onglet « {onglet} » introuvable - mention de version ignorée.")
        return
    cellule = wb.sheets[onglet][adresse]
    base = re.sub(r"\s+du \d{2}/\d{2}/\d{4}$", "", str(cellule.value or "").strip())
    cellule.value = f"{base} du {date.today():%d/%m/%Y}"

    onglet, adresse = _CELLULE_ALERTE
    if onglet in noms:
        alerte = wb.sheets[onglet][adresse]
        alerte.value = ALERTE_VERSION
        alerte.police(gras=True, couleur=bgr(0x0000C0))   # rouge foncé (BGR)
    print(f"  → Mention de version : « {cellule.value} »"
          + (", alerte « version de travail »" if ALERTE_VERSION else ""))


_CELLULES_TITRE = (("INTRODUCTION", "B2"), ("Notice", "B2"))


def _update_mention_scenario(wb) -> None:
    """
    Trajectoire nationale autre que la SNBC3 (`--nom-scenario`) : titres des
    onglets INTRODUCTION et Notice au nom du scénario, et mention à la suite de
    l'alerte de version. Les autres textes de l'outil (contexte, méthode)
    restent ceux de la SNBC3, pour laquelle il a été conçu. À appeler après
    `_update_mention_version`, qui réécrit l'alerte.
    """
    from config.settings import TRAJ_NATIONALE

    nom = TRAJ_NATIONALE.nom
    if not nom:
        return
    noms = [s.name for s in wb.sheets]
    for onglet, adresse in _CELLULES_TITRE:
        if onglet in noms:
            titre = wb.sheets[onglet][adresse]
            titre.value = str(titre.value or "").replace("de la SNBC", f"du scénario {nom}")

    onglet, adresse = _CELLULE_ALERTE
    if onglet in noms:
        mention = (f"Trajectoire nationale territorialisée : {nom}, à la place de la "
                   f"SNBC3 ; la méthode décrite dans l'outil s'y applique à l'identique.")
        alerte = wb.sheets[onglet][adresse]
        alerte.value = " ".join(t for t in (ALERTE_VERSION, mention) if t)
        alerte.police(gras=True, couleur=bgr(0x0000C0))   # rouge foncé (BGR)
    print(f"  → Mention de la trajectoire nationale : « {nom} »")


def _update_Catalogue_data_EPCI(wb, wide_path: Path) -> None:
    """
    Met à jour le tableau Catalogue_data_EPCI du classeur wb depuis l'onglet
    Catalogue de wide_path.

    - Colonnes couvertes par la source → valeurs écrasées.
    - Colonnes hors source (formules)  → préservées sur les lignes existantes,
                                         répliquées avec décalage sur les nouvelles.
    - Si des lignes sont ajoutées, le tableau est étendu.
    """
    import pandas as pd

    df_src = pd.read_excel(wide_path, sheet_name="Catalogue", engine="openpyxl")
    src_cols: set[str] = set(df_src.columns)
    n_src = len(df_src)
    print(f"  Lecture Catalogue wide : {n_src} indicateurs.")

    # Localisation du tableau Catalogue_data_EPCI
    cat_ws, cat_table = None, None
    for sh in wb.sheets:
        for tbl in sh.tables:
            if tbl.name == "Catalogue_data_EPCI":
                cat_ws, cat_table = sh, tbl
                break
        if cat_table is not None:
            break

    if cat_table is None:
        print("  AVERTISSEMENT : tableau « Catalogue_data_EPCI » introuvable - ignoré.")
        return

    print(f"  Mise à jour du tableau « Catalogue_data_EPCI » (onglet « {cat_ws.name} ») …")

    # En-têtes et positions de colonnes
    hdr_rng = cat_table.header_row_range
    raw = hdr_rng.value
    headers: list[str] = raw if isinstance(raw, list) else [raw]
    first_col = hdr_rng.column    # 1-indexé
    header_row = hdr_rng.row      # 1-indexé
    n_cols_table = len(headers)
    data_first_row = header_row + 1

    col_pos = {h: first_col + i for i, h in enumerate(headers)}
    src_cols_present = [h for h in headers if h in src_cols]
    formula_cols = [h for h in headers if h not in src_cols]

    # Nombre de lignes actuelles
    data_rng = cat_table.data_body_range
    n_current = data_rng.rows.count if data_rng is not None else 0

    # Formules modèles depuis la dernière ligne de données existante
    formula_templates: dict[str, str] = {}
    if n_current > 0 and formula_cols:
        last_existing = data_first_row + n_current - 1
        for col in formula_cols:
            f = cat_ws.range((last_existing, col_pos[col])).formula
            if f:
                formula_templates[col] = f
    elif formula_cols and n_current == 0:
        print(f"    AVERTISSEMENT : tableau vide, impossible de répliquer les formules : {formula_cols}")

    # Écriture des valeurs source (toutes les n_src lignes d'un coup par colonne)
    last_src_row = data_first_row + n_src - 1
    for col in src_cols_present:
        c = col_pos[col]
        vals = [[v] for v in df_src[col].tolist()]
        cat_ws.range((data_first_row, c), (last_src_row, c)).value = vals

    # Réplication des formules pour les nouvelles lignes
    if n_src > n_current and formula_templates:
        template_row = data_first_row + n_current - 1
        for new_i in range(n_current, n_src):
            excel_row = data_first_row + new_i
            delta = excel_row - template_row
            for col, tmpl in formula_templates.items():
                adjusted = _adjust_formula_rows(tmpl, delta)
                cell = cat_ws.range((excel_row, col_pos[col]))
                if adjusted.startswith("="):
                    cell.formula = adjusted
                else:
                    cell.value = adjusted or None

    # Redimensionnement du tableau si le nombre de lignes a changé
    if n_src != n_current:
        from openpyxl.utils import get_column_letter
        new_ref = (
            f"{get_column_letter(first_col)}{header_row}"
            f":{get_column_letter(first_col + n_cols_table - 1)}{last_src_row}"
        )
        if n_src < n_current:
            # Effacer le contenu des lignes excédentaires avant de réduire le tableau
            excess_first = data_first_row + n_src
            excess_last = data_first_row + n_current - 1
            cat_ws.range(
                (excess_first, first_col),
                (excess_last, first_col + n_cols_table - 1),
            ).clear_contents()
        try:
            cat_table.redimensionner(last_src_row, first_col + n_cols_table - 1)
            if n_src > n_current:
                print(f"    → {n_src - n_current} ligne(s) ajoutée(s), tableau étendu ({new_ref}).")
            else:
                print(f"    → {n_current - n_src} ligne(s) supprimée(s), tableau réduit ({new_ref}).")
        except Exception as exc:
            print(f"    AVERTISSEMENT : redimensionnement du tableau échoué : {exc}")

    print(f"    → {n_src} indicateurs mis à jour dans Catalogue_data_EPCI.")


def _is_year(v) -> bool:
    try:
        return 2000 <= int(str(v).strip()) <= 2100
    except (ValueError, TypeError):
        return False


TRAJ_SUPRA_COL_ZONE = "Zone"
TRAJ_SUPRA_COL_CLE = "Clé"
# En-tête historique : la donnée de recalage vient de l'OREC dans l'hexagone mais
# de l'IGT du CITEPA aux DROM. Le nom de colonne est conservé pour ne pas
# invalider les classeurs déjà diffusés.
TRAJ_SUPRA_COL_RECALAGE = "Code OREC recalage"
# Colonne d'une version antérieure (correction de la part mahoraise), rendue
# inutile par le découpage des DROM en zones : supprimée du tableau si présente.
TRAJ_SUPRA_COL_OBSOLETE = "Facteur périmètre"


def _code(v) -> str:
    """
    Normalise un code lu dans une cellule Excel.

    Excel (et `classeur.Plage`, qui l'imite) coerce le texte "976" en nombre ;
    un classeur relu peut en outre rendre 976.0, dont str() vaut "976.0".
    Sans cette normalisation, toute comparaison à un code textuel échoue
    silencieusement.
    """
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, int):
        return str(v)
    return str(v).strip()

# Séparateur de la clé (code, zone) du tableau Traj_supra et de l'onglet CALCUL.
CLE_SEP = "|"


def _zones_par_defaut(zones: list[str] | None) -> list[str]:
    """
    Zones portées par le classeur : toutes les zones territorialisées si non
    précisé, groupes (DROM) développés en leurs zones sinon.
    """
    from config.settings import ZONES_TERRITORIALISEES, zones_demandees
    return zones_demandees(list(zones)) if zones else list(ZONES_TERRITORIALISEES)


def _ensure_table_columns(table, headers: list[str], noms: list[str]) -> list[str]:
    """
    Garantit la présence de colonnes dans un tableau structuré, en les ajoutant
    **en fin de tableau** si besoin.

    L'ajout se fait en dernière position volontairement : insérer une colonne en
    tête décalerait les cellules de l'onglet situées hors du tableau, dont
    Traj_supra!$C$1:$C$3 qui portent les plages nommées `nom_scenario`,
    `echelon_scenario` et `Annee_ref` - `Annee_ref` alimentant tout l'onglet
    CALCUL. Le script travaillant par nom de colonne, la position n'a aucune
    incidence sur les formules.

    Retourne la liste des en-têtes à jour.
    """
    manquants = [n for n in noms if n not in headers]
    if not manquants:
        return headers
    for nom in manquants:
        table.ajouter_colonne(nom)
        print(f"    → colonne « {nom} » ajoutée au tableau {table.name}")
    return table.entetes()


def totaux_long_depuis_table(headers: list, rows: list[list]):
    """
    Lignes agrégées (FRANCE, zones, groupes) d'une table au format de
    `Data_EPCI` (colonnes `CODE_AAAA`), remises au format long de la BDD :
    (code_epci, code_indicateur, annee, valeur).

    Sert à dériver dans le tableur la trajectoire des zones d'un groupe
    (`pipeline.territorialize.trajectoire_zone`) à partir de exactement les
    totaux que le tableur utilise comme dénominateurs. Une cellule vide ne
    donne pas de ligne : c'est l'absence de valeur, pas un zéro.
    """
    import pandas as pd
    from config.settings import CODES_TOTAUX

    i_code = headers.index("code_epci")
    cols = []
    for j, h in enumerate(headers):
        m = re.fullmatch(r"(.+)_(\d{4})", str(h)) if h else None
        if m:
            cols.append((j, m.group(1), int(m.group(2))))
    records = []
    for r in rows:
        code = _code(r[i_code])
        if code not in CODES_TOTAUX:
            continue
        for j, ind, annee in cols:
            v = r[j] if j < len(r) else None
            if isinstance(v, (int, float)):
                records.append((code, ind, annee, float(v)))
    return pd.DataFrame(records, columns=["code_epci", "code_indicateur", "annee", "valeur"])


def _totaux_data_epci(wb):
    """Lignes agrégées de la table `Data_EPCI` du classeur, au format long."""
    dp = wb.sheets["Data_EPCI"]
    hdr = [h for h in dp.range((1, 1), (1, 1000)).value if h]
    n = dp.tables[0].range.rows.count - 1
    data = dp.range((2, 1), (1 + n, len(hdr))).value
    if data and not isinstance(data[0], list):
        data = [data]
    return totaux_long_depuis_table(hdr, data or [])


def _update_traj_supra(wb, zones: list[str]) -> None:
    """
    Injecte les valeurs nationales SNBC3 (2020→2050) dans le tableau `Traj_supra`,
    pour **toutes les zones** du classeur.

    Une ligne du tableau = un couple (`Code traj supra`, `Zone`). L'appariement
    se fait sur ce couple et porte sur les **seules colonnes-années** : les
    colonnes de métadonnée (pivot, corrections, recalage) et les formules
    voisines sont préservées, l'ordre des lignes est inchangé.

    Une zone dont la trajectoire n'est pas calculable (onglet SNBC3 retraité
    ou bilan absent) est signalée et ignorée : ses lignes ne sont pas créées,
    et l'onglet CALCUL renverra 0 pour un territoire de cette zone plutôt que
    des valeurs fausses.

    Les valeurs proviennent de data/input/SNBC3 retraitée.xlsx (cf. settings),
    par le même résolveur que le pipeline (`trajectoire_zone`) : lecture
    directe pour HEXAGONE, dérivation de la trajectoire DROM pour chaque
    DROM, à partir des totaux de la table `Data_EPCI` du classeur.
    """
    from config.settings import ANNEE_REF
    from pipeline.territorialize import load_correspondance, trajectoire_zone

    totaux = _totaux_data_epci(wb)

    # {(code, zone): {annee: valeur}} et métadonnées par (code, zone)
    vals: dict[tuple[str, str], dict] = {}
    corr_par_zone: dict = {}
    zones_ok: list[str] = []
    for zone in zones:
        corr = load_correspondance(zone)
        try:
            nat = trajectoire_zone(set(corr["code_traj_supra"]), zone, totaux, ANNEE_REF)
        except Exception as exc:
            print(f"  AVERTISSEMENT : zone {zone} ignorée dans Traj_supra - {exc}")
            continue
        corr_par_zone[zone] = corr
        zones_ok.append(zone)
        for code, g in nat.groupby("code_traj_supra"):
            vals[(code, zone)] = dict(zip(g["annee"], g["valeur_nationale"]))

    if not zones_ok:
        print("  AVERTISSEMENT : aucune zone exploitable - Traj_supra inchangé.")
        return

    recal_map = {
        (r["code_traj_supra"], zone): (r.get("code_recalage") or "")
        for zone, corr in corr_par_zone.items()
        for _, r in corr.iterrows()
    }

    # Localisation du tableau Traj_supra
    ts_ws, ts_table = None, None
    for sh in wb.sheets:
        for tbl in sh.tables:
            if tbl.name == "Traj_supra":
                ts_ws, ts_table = sh, tbl
                break
        if ts_table is not None:
            break
    if ts_table is None:
        print("  AVERTISSEMENT : tableau « Traj_supra » introuvable - injection des valeurs ignorée.")
        return

    print(f"  Mise à jour des valeurs du tableau « Traj_supra » "
          f"(onglet « {ts_ws.name} », zones {', '.join(zones_ok)}) …")

    hdr_rng = ts_table.header_row_range
    raw = hdr_rng.value
    headers = raw if isinstance(raw, list) else [raw]

    if "Code traj supra" not in headers:
        print("  AVERTISSEMENT : colonne « Code traj supra » absente - injection ignorée.")
        return

    # Zone et Clé identifient la ligne : sans elles, deux zones écraseraient
    # leurs valeurs mutuellement sur le même code.
    headers = _ensure_table_columns(
        ts_table, headers, [TRAJ_SUPRA_COL_ZONE, TRAJ_SUPRA_COL_CLE],
    )
    if TRAJ_SUPRA_COL_OBSOLETE in headers:
        ts_table.supprimer_colonne(TRAJ_SUPRA_COL_OBSOLETE)
        headers = ts_table.entetes()
        print(f"    → colonne obsolète « {TRAJ_SUPRA_COL_OBSOLETE} » supprimée")
    hdr_rng = ts_table.header_row_range
    first_col = hdr_rng.column
    header_row = hdr_rng.row
    data_first_row = header_row + 1

    code_col = first_col + headers.index("Code traj supra")
    zone_col = first_col + headers.index(TRAJ_SUPRA_COL_ZONE)
    cle_col = first_col + headers.index(TRAJ_SUPRA_COL_CLE)
    recal_col = (first_col + headers.index("Code recalage")
                if "Code recalage" in headers else None)

    # Colonnes de métadonnées à synchroniser depuis traj_supra.yaml
    _META_SYNC = {
        "Code pivot": "code_pivot",
        "Type indicateur": "type",
        "Secteur": "secteur",
        "Usage / sous-secteur": "usage",
        "Unité": None,
        "Influence démographie": "influence_demographie",
        "Correction DJU chaud": "correction_dju_chaud",
        "Correction DJU froid": "correction_dju_froid",
        "Code OREC recalage": "code_recalage",
    }
    _BOOL_HEADERS = {"Influence démographie", "Correction DJU chaud", "Correction DJU froid"}
    _UNITE_MAP = {"GES": "MtCO2e", "conso": "TWh"}
    meta_cols = {}
    for hdr_name, yaml_key in _META_SYNC.items():
        if yaml_key is not None and hdr_name in headers:
            meta_cols[hdr_name] = (first_col + headers.index(hdr_name), yaml_key)
    unite_col_idx = (first_col + headers.index("Unité")) if "Unité" in headers else None

    meta_map = {}
    for zone, corr in corr_par_zone.items():
        for _, r in corr.iterrows():
            d = {yaml_key: r.get(yaml_key, "")
                 for _, yaml_key in meta_cols.values()}
            d["__unite__"] = _UNITE_MAP.get(r.get("type", ""), "")
            meta_map[(r["code_traj_supra"], zone)] = d

    # Colonnes-années (contiguës) et leur année
    year_cols = sorted(
        (first_col + i, int(str(h).strip()))
        for i, h in enumerate(headers) if _is_year(h)
    )
    if not year_cols:
        print("  AVERTISSEMENT : aucune colonne-année dans Traj_supra - injection ignorée.")
        return
    first_year_col, last_year_col = year_cols[0][0], year_cols[-1][0]
    annees_ordre = [a for _, a in year_cols]

    data_rng = ts_table.data_body_range
    n = data_rng.rows.count if data_rng is not None else 0

    def _col(idx):
        v = ts_ws.range((data_first_row, idx), (data_first_row + n - 1, idx)).value
        return v if isinstance(v, list) else [v]

    codes = _col(code_col)
    zones_lues = _col(zone_col) if n else []

    def _norm(v):
        return str(v).strip() if v is not None else ""

    # Les lignes héritées d'une version mono-zone n'ont pas de Zone : elles
    # sont rattachées à la première zone traitée plutôt que supprimées puis
    # recréées, ce qui préserverait d'éventuelles saisies manuelles.
    zone_defaut = zones_ok[0]
    cles_table = [
        (_norm(c), _norm(z) or zone_defaut)
        for c, z in zip(codes, zones_lues or [None] * len(codes))
    ]

    def _write_row_meta(row, cle):
        """Ecrit les métadonnées et valeurs d'une ligne Traj_supra."""
        code, zone = cle
        ts_ws.range((row, zone_col)).value = zone
        ts_ws.range((row, cle_col)).value = f"{code}{CLE_SEP}{zone}"
        if cle in vals:
            vecteur = [[vals[cle].get(an) for an in annees_ordre]]
            ts_ws.range((row, first_year_col), (row, last_year_col)).value = vecteur
        if recal_col is not None:
            ts_ws.range((row, recal_col)).value = recal_map.get(cle, "") or None
        if cle in meta_map:
            for hdr_name, (col_idx, yaml_key) in meta_cols.items():
                val = meta_map[cle].get(yaml_key, "")
                cell = ts_ws.range((row, col_idx))
                cell.value = val
                if hdr_name in _BOOL_HEADERS:
                    cell.color = (198, 239, 206) if val else None
            if unite_col_idx is not None:
                ts_ws.range((row, unite_col_idx)).value = meta_map[cle]["__unite__"]

    n_maj = 0
    for i, cle in enumerate(cles_table):
        if cle not in vals:
            continue
        _write_row_meta(data_first_row + i, cle)
        n_maj += 1

    # Synchronisation : ajout/suppression de lignes pour coller au YAML.
    # Seules les zones effectivement traitées sont arbitrées : les lignes d'une
    # zone ignorée (trajectoire incalculable) sont laissées telles quelles. Les
    # lignes d'une zone qui n'est plus déclarée (ancienne zone DROM unique,
    # remplacée par une zone par DROM) sont supprimées.
    desired = {
        (r["code_traj_supra"], zone)
        for zone, corr in corr_par_zone.items()
        for _, r in corr.iterrows()
    }
    in_table = {c for c in cles_table if c[0]}
    to_remove = {c for c in in_table - desired if c[1] in zones_ok or c[1] not in zones}
    # Ordre d'ajout = ordre du YAML, zone par zone : le tableau reste lisible
    # (GES puis conso, par secteur) au lieu de partir en ordre alphabétique.
    to_add = [
        cle for zone in zones_ok
        for cle in ((r["code_traj_supra"], zone) for _, r in corr_par_zone[zone].iterrows())
        if cle not in in_table
    ]

    if to_remove:
        ts_table.supprimer_lignes(
            [i + 1 for i in range(len(cles_table)) if cles_table[i] in to_remove])
        print(f"    → {len(to_remove)} ligne(s) supprimée(s) : "
              f"{sorted(f'{c}|{z}' for c, z in to_remove)[:5]}")

    if to_add:
        for cle in to_add:
            r = ts_table.ajouter_ligne()
            ts_ws.range((r, code_col)).value = cle[0]
            _write_row_meta(r, cle)
            n_maj += 1
        print(f"    → {len(to_add)} ligne(s) ajoutée(s) au tableau.")

    n_total = len(in_table) - len(to_remove) + len(to_add)
    print(f"    → {n_maj}/{n_total} lignes Traj_supra mises à jour "
          f"({len(zones_ok)} zone(s)).")
    no_vals = desired - set(vals)
    if no_vals:
        print(f"    AVERTISSEMENT : {len(no_vals)} couple(s) (code, zone) sans "
              f"valeurs SNBC3 : {sorted(no_vals)[:5]}")


# Étapes de calcul détaillées dans l'onglet CALCUL (colonne « Étape »)
CALCUL_ETAPES = [
    "Valeur traj supra",   # 0 : valeur de la zone (Traj_supra, clé code|zone)
    "Ratio pivot",         # 1 : part du territoire dans la zone
    "Correction DJU",      # 2 : DJU territoire / moyenne de zone (chaud×froid)
    "Correction démographique",     # 3 : croissance démographique relative (évolutif)
    "Résultat brut",       # 4 : produit des étapes 0-3
    "Ratio recalage",      # 5 : observé / Σ brut (par secteur, année de réf.)
    "Résultat recalé",     # 6 : brut × ratio de recalage
]
# Étapes mises en évidence (fond + gras)
_CALCUL_RESULTATS = {"Résultat brut", "Résultat recalé"}
# Étapes affichées en pourcentage (ratios)
_CALCUL_RATIOS = {"Ratio pivot", "Correction DJU", "Correction démo", "Ratio recalage"}


def _fmt_1d(rng) -> None:
    """Format 1 chiffre apres la virgule."""
    rng.number_format = "0.0"


def openpyxl_col(letter: str) -> int:
    """Indice 1-based d'une colonne depuis sa lettre (A→1)."""
    from openpyxl.utils import column_index_from_string
    return column_index_from_string(letter)


def _fmt_header(rng, fill=(68, 114, 196)) -> None:
    """Met en forme une plage d'en-tête (fond bleu, texte blanc gras)."""
    rng.color = fill
    rng.police(gras=True, couleur="FFFFFFFF")


SHEET_PIVOTS = "Territoire"   # onglet sélection territoire + override pivots/recalage

# Alerte de recalage important (bloc ③ de « Territoire ») : écart au pivot =
# ratio de recalage de la sélection / ratio de la zone. Le ratio de zone
# (écart de niveau observé / SNBC3, commun à tous les territoires) ne dit rien
# du pivot ; seul l'écart au pivot mesure ce que le pivot ne capte pas du
# territoire (rapport méthodologique, 3.6). Alerte orange au-delà d'un facteur
# 2, rouge au-delà de 3 (ou écart négatif), pour un secteur pesant au moins
# SEUIL_PART_RECALAGE du total du territoire dans son unité.
SEUIL_ECART_PIVOT_ORANGE = 2
SEUIL_ECART_PIVOT_ROUGE = 3
SEUIL_PART_RECALAGE = 0.10

SECTEUR_SHEETS = {
    "RESIDENTIEL": "Résidentiel",
    "TERTIAIRE": "Tertiaire",
    "INDUSTRIE": "Industrie",
    "AGRICULTURE": "Agriculture",
    "TRANSPORTS": "Transports",
    "DECHETS": "Déchets",
    "BRANCHE ENERGIE": "Branche énergie",
    "UTCATF": "UTCATF",
    "CSC": "CSC",
}


_NEGATIVE_SECTORS = {"UTCATF", "CSC"}

# Usages dont le signe dépend du territoire (séquestration ou émission nette)
# au sein d'un secteur : scindés de part et d'autre de l'axe des abscisses dans
# le graphique en aires empilées "par usage" (cf. `_chart_block_pos_neg`).
_NEGATIVE_USAGES = {
    "UTCATF": {
        "emi_UTCATF_for", "emi_UTCATF_cult", "emi_UTCATF_prai",
        "emi_UTCATF_zohu", "emi_UTCATF_arti", "emi_UTCATF_aut",
        "emi_UTCATF_prbo", "emi_UTCATF_bar", "emi_UTCATF_solfor",
    },
}

# Usages hors inventaire officiel : exclus du total agrégé de leur secteur
# dans la vue TOUS SECTEURS et affichés à part, en dernière position.
_HORS_INVENTAIRE_CODES = {"emi_UTCATF_solfor"}

# Secteurs dont les feuilles de référence Traj_observée/Traj_planifiée ne
# fournissent pas de ligne agrégée par secteur, mais une ligne par poste (ex.
# "Transport routier" / "Autre transport") : le graphique de total
# (TrajRef/TrajObs/TrajPlan) est alors scindé en un graphique par poste,
# chacun identifié par son ou ses codes de recalage (colonne `code_recalage`
# de traj_supra.yaml). Un « poste » ici n'est PAS un « sous-secteur » au sens
# de traj_supra.yaml (qui désigne une ligne = un usage, cf. `_write_val_block`
# et la colonne « Usage / sous-secteur ») : c'est un regroupement d'usages
# propre aux feuilles de référence obs/plan, à un niveau intermédiaire entre
# usage et secteur.
_SECTEUR_POSTES = {
    "Transports": [
        ("Transport routier", {"EC_EMIS_TRANSPORT_ROUTIER", "EC_CONSO_TRANSPORT_ROUTIER"}),
        ("Autre transport", {"EC_EMIS_AUTRE_TRANSPORT", "EC_CONSO_AUTRE_TRANSPORT"}),
    ],
}

FY_CALCUL = 11  # colonne K : 1re année dans l'onglet CALCUL

# Largeur standard des colonnes années dans les onglets sectoriels (alignée sur
# la largeur historiquement présente sur la majorité des onglets du template).
# Largeurs de colonnes de ce module : valeurs stockées dans le fichier (cf.
# `classeur.Plage.column_width`).
_YEAR_COL_WIDTH = 9


def _scan_obs_plan_rows(wb):
    """
    Parcourt Traj_observée et Traj_planifiée pour trouver les lignes de chaque
    secteur. Retourne {sheet: {"GES": {secteur: row}, "conso": {secteur: row},
    "total_GES": row|None, "total_conso": row|None, "c0": int}}.
    """
    known = set(SECTEUR_SHEETS.values())
    for postes in _SECTEUR_POSTES.values():
        known.update(label for label, _ in postes)
    known_low = {s.lower(): s for s in known}
    result = {}
    for sname, c0 in [("Traj_observée", 7), ("Traj_planifiée", 10)]:
        if sname not in [s.name for s in wb.sheets]:
            continue
        ws = wb.sheets[sname]
        data = ws.range((1, 1), (60, 6)).value
        if data is None:
            continue
        occ = {}
        totals = []
        for i, row_data in enumerate(data):
            r = i + 1
            for cell in row_data:
                if cell is None:
                    continue
                s = str(cell).strip()
                sl = s.lower()
                if sl in known_low:
                    occ.setdefault(known_low[sl], []).append(r)
                if sl in ("total", "total net", "tous secteurs"):
                    totals.append(r)
        ges_rows, cons_rows = {}, {}
        for sec, rows in occ.items():
            ges_rows[sec] = rows[0]
            if len(rows) >= 2:
                cons_rows[sec] = rows[1]
        result[sname] = {
            "GES": ges_rows, "conso": cons_rows,
            "total_GES": totals[0] if totals else None,
            "total_conso": totals[1] if len(totals) >= 2 else None,
            "c0": c0,
        }
    return result


def _build_calcul(wb, zones: list[str]) -> None:
    """
    Construit l'onglet « CALCUL » : territorialisation dynamique de la trajectoire
    nationale pour le territoire sélectionné, décomposée par étape (résultats en
    **ktCO2e / GWh**).

    - Bloc « corrections globales » en haut (lignes 2-4) : ratios DJU chaud/froid
      (statiques) et démographique (par année), calculés une seule fois et de façon
      autonome depuis la table `Data_EPCI` (onglet Data_EPCI).
    - Étapes par sous-secteur (dès la ligne 6), **les corrections DJU/démo ne sont
      affichées que si au moins une zone les applique**. Le résultat brut =
      PRODUIT des étapes présentes.
    - Ratio pivot et recalage lus depuis l'onglet « Territoire »
      (override utilisateur pris en compte).

    **Multi-zone.** L'onglet couvre l'union des sous-secteurs de toutes les zones
    et se cale sur la zone du territoire sélectionné (`terr_zone`, défini dans
    l'onglet « Territoire »). Tout ce qui dépend de la zone - valeur nationale,
    code pivot, code de recalage, activation des corrections - est résolu par
    formule via la clé `code|zone` du tableau `Traj_supra`, jamais figé ici.
    Un sous-secteur absent de la zone retenue (UTCATF hors hexagone) ou une
    sélection à cheval sur deux zones font échouer la recherche : le résultat
    vaut 0, plutôt qu'une valeur issue de la mauvaise zone.
    """
    from openpyxl.utils import get_column_letter as L
    from pipeline.territorialize import load_correspondance_union
    from config.settings import ANNEE_REF

    if "CALCUL" not in [s.name for s in wb.sheets]:
        print("  AVERTISSEMENT : onglet « CALCUL » introuvable - construction ignorée.")
        return

    corr = load_correspondance_union(zones)
    ws = wb.sheets["CALCUL"]
    # Un filtre actif (posé par l'utilisateur pour lire un sous-secteur) masque
    # des lignes, qui le resteraient après reconstruction. Ses critères sont
    # vidés et les lignes réaffichées ; les flèches de filtre restent en place.
    ws.reinitialiser_filtre()
    ws.clear()
    for _ch in list(ws.charts):
        try:
            _ch.delete()
        except Exception:
            pass

    NY, fy, hr = 30, 11, 5          # en-tête années en ligne 5 (bandeau bleu)
    ycols = [L(fy + j) for j in range(NY)]
    last_col = fy + NY - 1

    # En-têtes des colonnes brutes (pour le bloc corrections autonome)
    raw_hdr = [h for h in wb.sheets["Data_EPCI"].range((1, 1), (1, 120)).value if h]
    pop_col = _raw_col(raw_hdr, "POP")
    dju_c_col = _raw_col(raw_hdr, "DJU_CHAUD_COSTIC")
    dju_f_col = _raw_col(raw_hdr, "DJU_FROID_COSTIC")
    ppr_by_year = {}
    for h in raw_hdr:
        m = re.fullmatch(r"POP_PROJ_RATIO_(\d{4})", str(h))
        if m:
            ppr_by_year[int(m.group(1))] = str(h)

    MASK = "ISNUMBER(MATCH(Data_EPCI[code_epci],terr_num_epci,0))"

    def terr_pw(col):   # moyenne pondérée population sur les EPCI sélectionnés
        return (f'SUMPRODUCT({MASK}*Data_EPCI[{col}]*Data_EPCI[{pop_col}])'
                f'/SUMPRODUCT({MASK}*Data_EPCI[{pop_col}])')

    def nat(col):
        # La ligne agrégée de la zone porte le nom de la zone comme code_epci
        # (HEXAGONE, DROM) : `terr_zone` sert donc directement de clé.
        return f'INDEX(Data_EPCI[{col}],MATCH(terr_zone,Data_EPCI[code_epci],0))'

    def ts(col_name, key_cell):
        """Recherche d'une métadonnée Traj_supra pour le couple (code, zone)."""
        return (f'INDEX(Traj_supra[{col_name}],'
                f'MATCH({key_cell},Traj_supra[{TRAJ_SUPRA_COL_CLE}],0))')

    # ── Titre + bloc corrections globales (lignes 2-4) ────────────────────
    ws.range("A1").value = "CALCUL DE LA TERRITORIALISATION"
    ws.range("B2").value = "Correction DJU chaud (territoire / zone)"
    ws.range((2, fy)).formula = f'=IFERROR({terr_pw(dju_c_col)}/{nat(dju_c_col)},1)'
    ws.range("B3").value = "Correction DJU froid (territoire / zone)"
    ws.range((3, fy)).formula = f'=IFERROR({terr_pw(dju_f_col)}/{nat(dju_f_col)},1)'
    ws.range("B4").value = "Correction démographique (par année)"
    for j in range(NY):
        col = ppr_by_year.get(ANNEE_REF + j)
        ws.range((4, fy + j)).formula = (f'=IFERROR({terr_pw(col)}/{nat(col)},1)' if col else "=1")
    # DJU : répercuter la valeur (col K) sur toutes les années (cohérence avec démo)
    for row_ in (2, 3):
        ws.range((row_, fy + 1), (row_, last_col)).formula = [[f"=$K{row_}"] * (NY - 1)]
    ws.range((2, fy), (4, last_col)).number_format = "0%"

    # ── En-tête (ligne 5, bandeau bleu : méta + années) ───────────────────
    d0 = 6
    headers = ["Type", "Secteur", "Usage", "Code traj supra", "Code pivot",
               "Unité", "Code recalage", "Étape", "Clé", "Clé Traj_supra"]
    ws.range((hr, 1)).value = headers
    ws.range((hr, fy)).formula = "=Annee_ref"
    for j in range(1, NY):
        ws.range((hr, fy + j)).formula = f"={ycols[j-1]}{hr}+1"
    _fmt_header(ws.range((hr, 1), (hr, last_col)))

    plan = []   # une entrée par ligne : dict(r, code, etape, unite, ss_first, brut, recal)
    r = d0
    for _, row in corr.iterrows():
        code = row["code_traj_supra"]
        chaud, froid = bool(row["correction_dju_chaud"]), bool(row["correction_dju_froid"])
        demo = bool(row["influence_demographie"])
        etapes = ["Valeur traj supra", "Ratio pivot"]
        if chaud or froid:
            etapes.append("Correction DJU")
        if demo:
            etapes.append("Correction démo")
        etapes += ["Résultat brut", "Ratio recalage", "Résultat recalé"]
        ss_first = r
        brut = ss_first + etapes.index("Résultat brut")
        recal = ss_first + etapes.index("Ratio recalage")
        unite = "ktCO2e" if row["type"] == "GES" else "GWh"
        mode_add = row.get("mode_additif") is True
        for et in etapes:
            plan.append(dict(r=r, code=code, etape=et, unite=unite, chaud=chaud, froid=froid,
                             ss_first=ss_first, brut=brut, recal=recal, mode_additif=mode_add))
            r += 1
    d1 = r - 1

    def look(val_col, key_cell, key_col):   # INDEX/MATCH dans l'onglet « Territoire »
        return (f"INDEX('{SHEET_PIVOTS}'!${val_col}:${val_col},"
                f"MATCH({key_cell},'{SHEET_PIVOTS}'!${key_col}:${key_col},0))")

    def _year_formula(p, yc):
        et, rr = p["etape"], p["r"]
        if et == "Valeur traj supra":     # valeur de zone (Mt/TWh) → kt/GWh
            # Recherche sur la clé code|zone : un sous-secteur absent de la zone
            # retenue (UTCATF hors hexagone) ou une zone indéterminée donnent 0.
            return (f'=IFERROR(INDEX(Traj_supra[],'
                    f'MATCH($J{rr},Traj_supra[{TRAJ_SUPRA_COL_CLE}],0),'
                    f'MATCH(""&{yc}${hr},Traj_supra[#Headers],0))*1000,0)')
        if et == "Ratio pivot":           # bloc ② pivots : D=code pivot, I=retenu, J=zone
            g = look("I", f"$E{rr}", "D")
            h = look("J", f"$E{rr}", "D")
            return f'=IFERROR({g}/{h},0)'
        if et == "Correction DJU":
            # Activation lue dans Traj_supra : une zone peut neutraliser la
            # correction (DROM) là où une autre l'applique.
            f_chaud = ts("Correction DJU chaud", f"$J{rr}")
            f_froid = ts("Correction DJU froid", f"$J{rr}")
            return (f'=IFERROR(IF({f_chaud}=TRUE,{yc}$2,1)'
                    f'*IF({f_froid}=TRUE,{yc}$3,1),1)')
        if et == "Correction démo":
            f_demo = ts("Influence démographie", f"$J{rr}")
            return f'=IFERROR(IF({f_demo}=TRUE,{yc}$4,1),1)'
        if et == "Résultat brut":
            return f'=PRODUCT({yc}{p["ss_first"]}:{yc}{p["brut"]-1})'
        if et == "Ratio recalage":        # bloc ③ recalage : N=code, S=retenu
            o = look("S", f"$G{rr}", "N")
            # Pas de code de recalage, ou donnée observée absente (chaîne vide,
            # cf. bloc ③) : pass-through à 1, comme `_apply_recalage` côté
            # pipeline. Un zéro observé, lui, annule bien le secteur.
            # `{o}` doit être testé sous IFERROR : sans code de recalage
            # (CSC, UTCATF hors inventaire), la recherche échoue et l'erreur
            # se propagerait à travers le OR.
            return (f'=IF(OR($G{rr}="",IFERROR({o},"")=""),1,IFERROR({o}'
                    f'/SUMIFS($K${d0}:$K${d1},$H${d0}:$H${d1},"Résultat brut",$G${d0}:$G${d1},$G{rr}),1))')
        if et == "Résultat recalé":
            if p["mode_additif"] and yc != ycols[0]:
                # Le poids additif est cherché sur le code pivot, vide quand le
                # sous-secteur n'existe pas dans la zone retenue (UTCATF hors
                # hexagone) : sans IFERROR le MATCH renvoie #N/A dès la
                # deuxième année, et l'erreur remonte jusqu'aux onglets de
                # visualisation via leurs SUMIFS.
                weight = look("L", f"$E{rr}", "D")
                # Delta national mis à l'échelle du facteur de recalage (en
                # valeur absolue), cf. `territorialize` (étape 5bis).
                return (f'=${ycols[0]}{rr}'
                        f'+({yc}{p["ss_first"]}-${ycols[0]}{p["ss_first"]})'
                        f'*ABS({yc}{p["recal"]})'
                        f'*IFERROR({weight},0)')
            return f'={yc}{p["brut"]}*{yc}{p["recal"]}'
        raise ValueError(et)

    # ── Écriture méta (colonnes) ──────────────────────────────────────────
    ws.range((d0, 4), (d1, 4)).value = [[p["code"]] for p in plan]      # D
    ws.range((d0, 8), (d1, 8)).value = [[p["etape"]] for p in plan]     # H étape
    ws.range((d0, 6), (d1, 6)).value = [[p["unite"]] for p in plan]     # F unité (kt/GWh)
    ws.range((d0, 9), (d1, 9)).formula = [                              # I clé = code|étape
        [f'=$D{p["r"]}&"|"&$H{p["r"]}'] for p in plan]
    ws.range((d0, 10), (d1, 10)).formula = [                            # J clé = code|zone
        [f'=$D{p["r"]}&"{CLE_SEP}"&terr_zone'] for p in plan]

    # Métadonnées invariantes par zone : écrites en dur.
    meta_by_code = {
        row["code_traj_supra"]: {
            "Type indicateur": row["type"],
            "Secteur": row["secteur"],
            "Usage / sous-secteur": row["usage"],
        }
        for _, row in corr.iterrows()
    }
    for col, field in {"A": "Type indicateur", "B": "Secteur",
                       "C": "Usage / sous-secteur"}.items():
        c = openpyxl_col(col)
        ws.range((d0, c), (d1, c)).value = [
            [meta_by_code.get(p["code"], {}).get(field, "")] for p in plan]

    # Métadonnées susceptibles de varier par zone (le pivot de la mobilité
    # domicile-travail, par exemple, bascule sur le parc VP aux DROM) : lues
    # dans Traj_supra sur la clé code|zone plutôt que figées.
    for col, ts_col in {"E": "Code pivot", "G": TRAJ_SUPRA_COL_RECALAGE}.items():
        c = openpyxl_col(col)
        formules = []
        for p in plan:
            lookup = ts(ts_col, f'$J{p["r"]}')
            formules.append([f'=IFERROR({lookup},"")'])
        ws.range((d0, c), (d1, c)).formula = formules

    # ── Écriture des années + mise en forme ───────────────────────────────
    for p in plan:
        ws.range((p["r"], fy), (p["r"], last_col)).formula = [[_year_formula(p, yc) for yc in ycols]]
    _fmt_1d(ws.range((d0, fy), (d1, last_col)))
    for p in plan:
        if p["etape"] in _CALCUL_RATIOS:
            ws.range((p["r"], fy), (p["r"], last_col)).number_format = "0.0%"
        if p["etape"] in _CALCUL_RESULTATS:
            rr = ws.range((p["r"], 1), (p["r"], last_col))
            rr.color = (226, 239, 218) if p["etape"] == "Résultat brut" else (198, 224, 180)
            rr.police(gras=True)
    ws.range((d0, 1), (d1, 8)).bordures()
    print("    → CALCUL construit.")


def _chart_block(ws, cb_col: int, first: int, last: int, fy: int, NY: int,
                 hrow: int, label_col: int, start_row: int = 1):
    """
    Construit un bloc de données canonique pour graphique (références) et renvoie
    sa plage : coin haut-gauche vide, 1re ligne = années (catégories), 1re colonne
    = noms de séries, le reste = valeurs. Excel place ainsi les années en abscisse.
    """
    from openpyxl.utils import get_column_letter as L
    nser = last - first + 1
    # Ligne d'années (catégories)
    ws.range((start_row, cb_col + 1), (start_row, cb_col + NY)).formula = [
        [f"={L(fy + j)}{hrow}" for j in range(NY)]
    ]
    # Séries (batch : labels + valeurs)
    ws.range((start_row + 1, cb_col), (start_row + nser, cb_col)).formula = [
        [f"={L(label_col)}{r}"] for r in range(first, last + 1)
    ]
    ws.range((start_row + 1, cb_col + 1), (start_row + nser, cb_col + NY)).formula = [
        [f"={L(fy + j)}{r}" for j in range(NY)] for r in range(first, last + 1)
    ]
    return ws.range((start_row, cb_col), (start_row + nser, cb_col + NY))


def _chart_block_pos_neg(ws, cb_col: int, rows: list, codes: list,
                         neg_codes: set, fy: int, NY: int, hrow: int,
                         label_col: int, start_row: int = 1,
                         lines: list | None = None) -> tuple:
    """
    Comme `_chart_block`, mais pour un graphique en aires empilées de part et
    d'autre de l'axe des abscisses : séries positives sur l'axe primaire,
    séries négatives sur un axe secondaire synchronisé. Évite qu'une aire
    négative « remonte » au-dessus de zéro à cause de l'empilement cumulatif
    natif d'Excel.

    Les lignes dont le code est dans `neg_codes` (secteurs ou usages
    habituellement séquestrants : UTCATF, CSC...) ne sont pas supposées
    négatives : leur signe dépend du territoire (forêt émettrice nette, par
    exemple). Chacune est donc scindée en une partie MAX(x;0), empilée au-dessus
    de l'axe, et une partie MIN(x;0), empilée en dessous ; la partie nulle n'a
    aucune hauteur à l'affichage.

    `rows` (pas nécessairement contigu, ex. bloc hors inventaire séparé du
    total par la ligne TOTAL) et `codes` sont appariés terme à terme. `lines`
    = [(libellé, f(j) -> formule de l'année j)] : séries tracées en ligne sur
    l'axe primaire (ex. total net), insérées entre les deux groupes.

    Ordre des séries : positives, parties positives des séries scindées,
    lignes, parties négatives, puis 2 séries de synchronisation si au moins une
    série est scindée. Renvoie (plage_source, disposition) où disposition est
    le dict attendu par `_format_pos_neg_area_chart`.
    """
    from openpyxl.utils import get_column_letter as L
    lines = lines or []
    pos_rows = [r for r, c in zip(rows, codes) if c not in neg_codes]
    neg_rows = [r for r, c in zip(rows, codes) if c in neg_codes]
    npos, nsplit, nlines = len(pos_rows), len(neg_rows), len(lines)

    ws.range((start_row, cb_col + 1), (start_row, cb_col + NY)).formula = [
        [f"={L(fy + j)}{hrow}" for j in range(NY)]
    ]

    def _write_rows(dest_first, src_rows, wrap="{}"):
        n = len(src_rows)
        if not n:
            return
        ws.range((dest_first, cb_col), (dest_first + n - 1, cb_col)).formula = [
            [f"={L(label_col)}{r}"] for r in src_rows
        ]
        ws.range((dest_first, cb_col + 1), (dest_first + n - 1, cb_col + NY)).formula = [
            ["=" + wrap.format(f"{L(fy + j)}{r}") for j in range(NY)] for r in src_rows
        ]

    r = start_row + 1
    _write_rows(r, pos_rows)
    r += npos
    _write_rows(r, neg_rows, "MAX({},0)")
    r += nsplit
    for label, formula in lines:
        ws.range((r, cb_col)).value = label
        ws.range((r, cb_col + 1), (r, cb_col + NY)).formula = [[formula(j) for j in range(NY)]]
        r += 1
    _write_rows(r, neg_rows, "MIN({},0)")
    r += nsplit
    last = r - 1

    if nsplit:
        # Deux séries de synchronisation d'échelle (masquées, une par axe)
        # pour aligner le zéro de l'axe secondaire (négatifs) sur celui du
        # primaire : chacune porte le cumul d'un des deux groupes.
        groupes = ((start_row + 1, start_row + npos + nsplit), (r - nsplit, r - 1))
        for rr, (a, b) in zip((r, r + 1), groupes):
            ws.range((rr, cb_col)).value = "_sync"
            ws.range((rr, cb_col + 1), (rr, cb_col + NY)).formula = [
                [f"=SUM({L(cb_col + 1 + j)}{a}:{L(cb_col + 1 + j)}{b})" for j in range(NY)]
            ]
        last = r + 1

    dispo = {"npos": npos, "nsplit": nsplit, "nlines": nlines}
    return ws.range((start_row, cb_col), (last, cb_col + NY)), dispo


def _format_pos_neg_area_chart(ch, dispo: dict) -> None:
    """
    Met en forme un graphique dont la plage source vient de
    `_chart_block_pos_neg` : lignes en type ligne (noires : les couleurs
    automatiques des aires les rendent peu lisibles), parties négatives en
    aires empilées sur l'axe secondaire (masqué), séries de synchronisation
    invisibles. Chaque partie positive d'une série scindée reçoit la couleur
    automatique de sa partie négative et sort de la légende, comme les séries
    de synchronisation.

    Entrées de légende d'un graphique combiné, dans l'ordre d'Excel : aires
    empilées primaires en ordre inverse (les parties positives scindées, en
    dernier dans la plage, viennent donc en tête), aires secondaires, puis
    lignes (les deux séries de synchronisation en dernier).
    """
    from standalone_tool.classeur import couleur_auto
    npos, nsplit, nlines = dispo["npos"], dispo["nsplit"], dispo["nlines"]
    i_dup = npos                      # 1re partie positive scindée (0-based)
    i_line = i_dup + nsplit           # 1re ligne
    i_neg = i_line + nlines           # 1re partie négative
    i_sync = i_neg + nsplit           # séries de synchronisation
    for k in range(nlines):
        ch.series[i_line + k] = {"type": "line", "trait": "000000", "sans_marqueur": True}
    if not nsplit:
        return
    n = ch.n_series
    accents = [ch.feuille.book.couleur_theme(4 + k) or (0, 0, 0) for k in range(6)]
    for k in range(nsplit):
        couleur = couleur_auto(i_neg + k, n, accents)
        ch.series[i_neg + k] = {"axe": 2, "remplissage": couleur, "trait": "none"}
        ch.series[i_dup + k] = {"remplissage": couleur, "trait": "none"}
    ch.series[i_sync] = {"type": "line", "axe": 2, "trait": "none", "sans_marqueur": True}
    ch.series[i_sync + 1] = {"type": "line", "trait": "none", "sans_marqueur": True}
    ch.axe2_masque = True
    # Années en bas du graphique, pas sur l'axe des abscisses où elles
    # chevaucheraient les aires négatives
    ch.etiquettes_x_bas = True
    ch.legende_supprimee = list(range(nsplit)) + [n - 2, n - 1]


def _total_block(ws, cb_col, total_row, obs_row, plan_row, fy, NY, hrow,
                 obs_c0=7, plan_c0=10, conv=1.0, start_row=1):
    """
    Bloc canonique pour un graphique de total à 3 séries (années en abscisse) :
    trajectoire territorialisée (calcul), observée (Traj_observée) et planifiée
    (Traj_planifiée) du territoire. `conv` convertit obs/plan si besoin (déjà en
    kt/GWh comme le calcul → 1).

    `total_row` est soit le numéro d'une ligne TOTAL déjà calculée, soit une
    liste de numéros de lignes à sommer (sous-total ad hoc, ex. un
    sous-ensemble d'usages d'un secteur scindé en plusieurs sous-catégories).
    """
    from openpyxl.utils import get_column_letter as L
    q = chr(34)
    total_rows = [total_row] if isinstance(total_row, int) else list(total_row)
    ws.range((start_row, cb_col + 1), (start_row, cb_col + NY)).formula = [
        [f"={L(fy + j)}{hrow}" for j in range(NY)]
    ]

    def obs(j):
        c = f"'Traj_observée'!{L(obs_c0 + j)}{obs_row}"
        return f"=IF({c}={q}{q},NA(),{c}/{conv:g})"

    def plan(j):
        c = f"'Traj_planifiée'!{L(plan_c0 + j)}{plan_row}"
        return f"=IF({c}={q}{q},NA(),{c}/{conv:g})"

    def ref(j):
        yc = L(fy + j)
        return f"={'+'.join(f'{yc}{r}' for r in total_rows)}"

    # Noms de séries récupérés depuis les plages nommées (pas en dur)
    series = [
        ("=TrajRef", [ref(j) for j in range(NY)]),
        ("=TrajObs", [obs(j) for j in range(NY)]),
        ("=TrajPlan", [plan(j) for j in range(NY)]),
    ]
    for k, (name, vals) in enumerate(series):
        br = start_row + 1 + k
        ws.range((br, cb_col)).formula = name
        ws.range((br, cb_col + 1), (br, cb_col + NY)).formula = [vals]
    return ws.range((start_row, cb_col), (start_row + len(series), cb_col + NY))


def _write_val_block(ws, first_row, codes, etape, unite, fy, NY, ycols, cd0, clast, hrow):
    """Écrit un bloc de lignes val en batch (métadonnées + formules années)."""
    n = len(codes)
    if not n:
        return
    last_row = first_row + n - 1
    is_formula = isinstance(etape, str) and etape.startswith("=")
    ws.range((first_row, 3), (last_row, 3)).value = [[c] for c in codes]
    if is_formula:
        ws.range((first_row, 4), (last_row, 4)).formula = [[etape]] * n
    else:
        ws.range((first_row, 4), (last_row, 4)).value = [[etape]] * n
    ws.range((first_row, 2), (last_row, 2)).formula = [
        [f'=IFERROR(INDEX(Traj_supra[Usage / sous-secteur],MATCH($C{first_row + k},Traj_supra[Code traj supra],0))&"","")']
        for k in range(n)
    ]
    ws.range((first_row, 5), (last_row, 5)).value = [[unite]] * n
    ws.range((first_row, fy), (last_row, fy + NY - 1)).formula = [
        [f'=IFERROR(INDEX(CALCUL!$K${cd0}:$AN${clast},'
         f'MATCH($C{first_row + k}&"|"&$D{first_row + k},CALCUL!$I${cd0}:$I${clast},0),'
         f'MATCH({yc}${hrow},CALCUL!$K$5:$AN$5,0)),"")'
         for yc in ycols]
        for k in range(n)
    ]


def _rows_for_codes(codes, first_row, code_recalage_map, target_recalage):
    """Lignes (1-based, à partir de first_row) dont le code a un code_recalage dans target_recalage."""
    return [first_row + i for i, c in enumerate(codes) if code_recalage_map.get(c) in target_recalage]


def _build_viz_secteur(wb, sheet_name: str, secteur: str, zones: list[str],
                       obs_plan=None) -> None:
    """
    Construit un onglet de visualisation dynamique pour un secteur donné, sur le
    modèle des onglets sectoriels (RESIDENTIEL, …), branché sur l'onglet `CALCUL`.
    """
    from openpyxl.utils import get_column_letter as L
    from pipeline.territorialize import load_correspondance_union

    if sheet_name not in [s.name for s in wb.sheets]:
        print(f"  AVERTISSEMENT : onglet « {sheet_name} » introuvable - visualisation ignorée.")
        return

    corr = load_correspondance_union(zones)
    cd0, clast = 6, 450
    sub = corr[corr["secteur"] == secteur]
    ges = list(sub[sub["type"] == "GES"]["code_traj_supra"])
    cons = list(sub[sub["type"] == "conso"]["code_traj_supra"])
    ges_dju = list(sub[(sub["type"] == "GES") &
                       (sub["correction_dju_chaud"] | sub["correction_dju_froid"])]["code_traj_supra"])
    ges_demo = list(sub[(sub["type"] == "GES") & sub["influence_demographie"]]["code_traj_supra"])
    code_recalage_map = dict(zip(sub["code_traj_supra"], sub["code_recalage"]))
    postes = _SECTEUR_POSTES.get(secteur)

    if obs_plan is None:
        obs_plan = _scan_obs_plan_rows(wb)
    obs_info = obs_plan.get("Traj_observée", {})
    plan_info = obs_plan.get("Traj_planifiée", {})
    ges_obs_row = obs_info.get("GES", {}).get(secteur)
    ges_plan_row = plan_info.get("GES", {}).get(secteur)
    cons_obs_row = obs_info.get("conso", {}).get(secteur)
    cons_plan_row = plan_info.get("conso", {}).get(secteur)

    ws = wb.sheets[sheet_name]
    print(f"  Construction de l'onglet « {sheet_name} » (secteur {secteur}) …")
    ws.clear()

    # Couleur de l'onglet -> fond de la ligne 1
    tab_color = ws.couleur_onglet()

    NY, fy = 30, 8
    hrow = 3
    ycols = [L(fy + j) for j in range(NY)]
    last_col = fy + NY - 1

    # Largeur des colonnes années homogène entre onglets sectoriels : sans ça,
    # les colonnes héritent de la largeur figée dans le template (variable
    # d'un onglet à l'autre), ce qui fait que les graphiques - de taille fixe
    # en points - s'étalent sur un nombre de colonnes différent et paraissent
    # plus ou moins larges selon l'onglet.
    ws.range((1, fy), (1, last_col)).column_width = _YEAR_COL_WIDTH

    def _years_header(row):
        ws.range((row, fy)).formula = "=Annee_ref"
        ws.range((row, fy + 1), (row, last_col)).formula = [
            [f"={ycols[j-1]}{row}+1" for j in range(1, NY)]
        ]

    def _total_row(r, first, last):
        ws.range((r, 2)).value = "TOTAL"
        ws.range((r, fy), (r, last_col)).formula = [[f"=SUM({yc}{first}:{yc}{last})" for yc in ycols]]
        ws.range((r, 1), (r, last_col)).police(gras=True)

    def _section(r, titre):
        ws.range((r, 1)).value = titre
        ws.range((r, 1), (r, last_col)).color = (217, 225, 242)
        ws.range((r, 1), (r, last_col)).police(gras=True)

    # ── En-tête + switch brut/recalé ─────────────────────────────────────
    ws.range("A1").value = f"VISUALISATION RÉSULTATS {sheet_name}"
    r1 = ws.range((1, 1), (1, last_col))
    r1.police(gras=True)
    if tab_color:
        r1.color = tab_color
    ws.range("A2").value = "Affichage :"
    sw = ws.range("B2")
    sw.value = "Résultat recalé"
    sw.color = (255, 242, 204)
    sw.validation_liste("Résultat brut;Résultat recalé")
    SWITCH = "=$B$2"
    ws.range((hrow, 1)).value = ["Indicateur", "Usage", "Code", "Étape", "Unité"]
    _years_header(hrow)
    _fmt_header(ws.range((hrow, 1), (hrow, last_col)))

    cur = 5  # curseur de ligne courante
    e_first = e_last = e_total = None
    e_hors_first = e_hors_last = None
    c_first = c_last = c_total = None
    p_first = p_last = None

    # ── Émissions par usage (ktCO2e) ─────────────────────────────────────
    if ges:
        _section(cur, "Émissions GES par usage (ktCO2e)")
        e_first = cur + 1
        # Hors inventaire officiel (ex. sols forestiers UTCATF) : sorti du
        # bloc principal et affiché à part, sous le total (cf. TOUS SECTEURS).
        ges_hors_inv = [c for c in ges if c in _HORS_INVENTAIRE_CODES]
        ges_normal = [c for c in ges if c not in _HORS_INVENTAIRE_CODES]
        _write_val_block(ws, e_first, ges_normal, SWITCH, "ktCO2e", fy, NY, ycols, cd0, clast, hrow)
        e_last = e_first + len(ges_normal) - 1
        e_total = e_last + 1
        _total_row(e_total, e_first, e_last)
        cur = e_total
        if ges_hors_inv:
            e_hors_first = cur + 1
            _write_val_block(ws, e_hors_first, ges_hors_inv, SWITCH, "ktCO2e", fy, NY, ycols, cd0, clast, hrow)
            e_hors_last = e_hors_first + len(ges_hors_inv) - 1
            cur = e_hors_last
        _fmt_1d(ws.range((e_first, fy), (cur, last_col)))
        cur += 1

    # ── Consommation par usage (GWh) ─────────────────────────────────────
    if cons:
        _section(cur + 1, "Consommation d'énergie finale par usage (GWh)")
        c_first = cur + 2
        _write_val_block(ws, c_first, cons, SWITCH, "GWh", fy, NY, ycols, cd0, clast, hrow)
        c_last = c_first + len(cons) - 1
        c_total = c_last + 1
        _total_row(c_total, c_first, c_last)
        _fmt_1d(ws.range((c_first, fy), (c_total, last_col)))
        cur = c_total + 1

    # ── Pivots (ratio territoire/zone) par usage ─────────────────────────
    if ges:
        _section(cur + 1, "Ratio pivot par usage (part du territoire dans la zone)")
        p_first = cur + 2
        _write_val_block(ws, p_first, ges, "Ratio pivot", "%", fy, NY, ycols, cd0, clast, hrow)
        p_last = p_first + len(ges) - 1
        cur = p_last

    # ── Facteurs de correction ───────────────────────────────────────────
    last_data = cur
    if ges_dju:
        _section(last_data + 2, "Correction DJU (usages concernés - territoire / zone)")
        dju_first = last_data + 3
        _write_val_block(ws, dju_first, ges_dju, "Correction DJU", "%", fy, NY, ycols, cd0, clast, hrow)
        last_data = dju_first + len(ges_dju) - 1
    if ges_demo:
        _section(last_data + 2, "Correction démographique (usages concernés - évolutif)")
        demo_first = last_data + 3
        _write_val_block(ws, demo_first, ges_demo, "Correction démo", "%", fy, NY, ycols, cd0, clast, hrow)
        last_data = demo_first + len(ges_demo) - 1

    if p_first is not None:
        ws.range((p_first, fy), (last_data, last_col)).number_format = "0%"

    # ── Graphiques ────────────────────────────────────────────────────────
    anchor = last_data + 3
    cb_col = last_col + 2
    step = NY + 2
    base = ws.range((anchor, 1))
    L0, T0, W, H, G = base.left, base.top, 560, 300, 20
    WA = 780  # aires empilées : largeur absolue fixe, identique sur tous les onglets
    n_chart_blocks = 0

    display_name = secteur

    def _mk(src, ctype, title, left, top, zero_base=True):
        w = WA if ctype == "area_stacked" else W
        ch = ws.charts.add(left=left, top=top, width=w, height=H)
        ch.set_source_data(src)
        ch.chart_type = ctype
        ch.titre = title
        if ch.n_series > 1:
            ch.legende = "r" if ctype == "area_stacked" else "b"
        if ctype == "area_stacked" and zero_base:
            # Axe des valeurs (axe primaire) : caler le zéro en bord d'axe,
            # sinon l'auto-échelle Excel peut démarrer au-dessus de 0 (ou
            # s'arrêter en-dessous si tout est négatif, ex. CSC) et fausser
            # visuellement la lecture d'une aire empilée. zero_base="max"
            # pour un secteur entièrement négatif ; zero_base=False sur les
            # graphiques pos/neg à double axe, où le calage du zéro dépend
            # de l'auto-échelle des deux axes (cf. _format_pos_neg_area_chart).
            if zero_base == "max":
                ch.ymax = 0
            else:
                ch.ymin = 0
        return ch

    neg_usages = _NEGATIVE_USAGES.get(secteur, set())

    try:
        row_y = 0
        if ges:
            n_total_ges = 0
            if postes:
                for label, recal_codes in postes:
                    rows = _rows_for_codes(ges, e_first, code_recalage_map, recal_codes)
                    obs_r = obs_info.get("GES", {}).get(label)
                    plan_r = plan_info.get("GES", {}).get(label)
                    if rows and (obs_r or plan_r):
                        s_et = _total_block(ws, cb_col + n_chart_blocks * step, rows,
                                            obs_r or 0, plan_r or 0, fy, NY, hrow,
                                            obs_c0=obs_info.get("c0", 7), plan_c0=plan_info.get("c0", 10))
                        _mk(s_et, "line", f"Émissions {label} (ktCO2e)", L0, T0 + n_total_ges * (H + G))
                        n_chart_blocks += 1
                        n_total_ges += 1
            elif ges_obs_row or ges_plan_row:
                s_et = _total_block(ws, cb_col + n_chart_blocks * step, e_total,
                                    ges_obs_row or 0, ges_plan_row or 0, fy, NY, hrow,
                                    obs_c0=obs_info.get("c0", 7), plan_c0=plan_info.get("c0", 10))
                _mk(s_et, "line", f"Émissions {display_name} (ktCO2e)", L0, T0)
                n_chart_blocks += 1
                n_total_ges = 1
            # Aire empilée par usage : centrée verticalement sur les graphiques
            # de total à gauche (1 seul la plupart du temps, 2+ empilés si le
            # secteur est scindé en postes, ex. Transports).
            area_top = T0 + (max(n_total_ges, 1) - 1) * (H + G) / 2
            neg_here = [c for c in ges if c in neg_usages]
            if neg_here:
                # Le bloc hors inventaire (déplacé sous le total) reste inclus
                # dans le graphique, empilé côté négatif, comme sur TOUS SECTEURS.
                rows_ea = list(range(e_first, e_last + 1))
                codes_ea = list(ges_normal)
                if e_hors_first is not None:
                    rows_ea += list(range(e_hors_first, e_hors_last + 1))
                    codes_ea += ges_hors_inv
                s_ea, dispo = _chart_block_pos_neg(
                    ws, cb_col + n_chart_blocks * step, rows_ea, codes_ea,
                    neg_usages, fy, NY, hrow, label_col=2)
                ch = _mk(s_ea, "area_stacked", f"Émissions {display_name} par usage (ktCO2e)", L0 + W + G, area_top,
                         zero_base=False)
                _format_pos_neg_area_chart(ch, dispo)
            else:
                s_ea = _chart_block(ws, cb_col + n_chart_blocks * step, e_first, e_last, fy, NY, hrow, label_col=2)
                # Secteur entièrement négatif (ex. CSC, non détaillé dans
                # _NEGATIVE_USAGES) : caler le haut de l'axe à 0 plutôt que le bas.
                fully_negative = not neg_usages and secteur in _NEGATIVE_SECTORS
                _mk(s_ea, "area_stacked", f"Émissions {display_name} par usage (ktCO2e)", L0 + W + G, area_top,
                    zero_base="max" if fully_negative else True)
            n_chart_blocks += 1
            row_y += max(n_total_ges, 1)

        if cons:
            top_c = T0 + row_y * (H + G)
            n_total_cons = 0
            if postes:
                for label, recal_codes in postes:
                    rows = _rows_for_codes(cons, c_first, code_recalage_map, recal_codes)
                    obs_r = obs_info.get("conso", {}).get(label)
                    plan_r = plan_info.get("conso", {}).get(label)
                    if rows and (obs_r or plan_r):
                        s_ct = _total_block(ws, cb_col + n_chart_blocks * step, rows,
                                            obs_r or 0, plan_r or 0, fy, NY, hrow,
                                            obs_c0=obs_info.get("c0", 7), plan_c0=plan_info.get("c0", 10))
                        _mk(s_ct, "line", f"Consommation {label} (GWh)", L0, top_c + n_total_cons * (H + G))
                        n_chart_blocks += 1
                        n_total_cons += 1
            elif cons_obs_row or cons_plan_row:
                s_ct = _total_block(ws, cb_col + n_chart_blocks * step, c_total,
                                    cons_obs_row or 0, cons_plan_row or 0, fy, NY, hrow,
                                    obs_c0=obs_info.get("c0", 7), plan_c0=plan_info.get("c0", 10))
                _mk(s_ct, "line", f"Consommation {display_name} (GWh)", L0, top_c)
                n_chart_blocks += 1
                n_total_cons = 1
            area_top_c = top_c + (max(n_total_cons, 1) - 1) * (H + G) / 2
            s_ca = _chart_block(ws, cb_col + n_chart_blocks * step, c_first, c_last, fy, NY, hrow, label_col=2)
            _mk(s_ca, "area_stacked", f"Consommation {display_name} par usage (GWh)", L0 + W + G, area_top_c)
            n_chart_blocks += 1

        if n_chart_blocks:
            ws.range((1, cb_col), (1, cb_col + n_chart_blocks * step)).masquer_colonnes()
    except Exception as exc:
        print(f"    AVERTISSEMENT : création des graphiques échouée ({exc}). Tables OK.")

    print(f"    → {sheet_name} construit.")


def _build_viz_tous_secteurs(wb, sheet_name: str, zones: list[str],
                             obs_plan=None) -> None:
    """
    Construit un onglet de visualisation avec la répartition par secteur (et non
    par usage), branché sur l'onglet CALCUL via SUMIFS. Filtre sur la colonne
    Unité (F) de CALCUL plutot que sur Type indicateur (A) pour la fiabilité.
    """
    from openpyxl.utils import get_column_letter as L
    from pipeline.territorialize import load_correspondance_union

    if sheet_name not in [s.name for s in wb.sheets]:
        print(f"  AVERTISSEMENT : onglet « {sheet_name} » introuvable - visualisation ignorée.")
        return

    corr = load_correspondance_union(zones)
    cd0, clast = 6, 450

    secteurs_ges = list(dict.fromkeys(corr[corr["type"] == "GES"]["secteur"]))
    secteurs_cons = list(dict.fromkeys(corr[corr["type"] == "conso"]["secteur"]))

    if obs_plan is None:
        obs_plan = _scan_obs_plan_rows(wb)
    obs_info = obs_plan.get("Traj_observée", {})
    plan_info = obs_plan.get("Traj_planifiée", {})

    ws = wb.sheets[sheet_name]
    print(f"  Construction de l'onglet « {sheet_name} » (tous secteurs) …")
    ws.clear()

    NY, fy = 30, 7
    hrow = 3
    ycols = [L(fy + j) for j in range(NY)]
    last_col = fy + NY - 1
    SWITCH = "$B$2"

    def _section(r, titre):
        ws.range((r, 1)).value = titre
        ws.range((r, 1), (r, last_col)).color = (217, 225, 242)
        ws.range((r, 1), (r, last_col)).police(gras=True)

    # Exclusion des codes hors inventaire des totaux par secteur (affichés à
    # part, cf. bloc dédié plus bas).
    _excl_crit = "".join(f',CALCUL!$D${cd0}:$D${clast},"<>{c}"' for c in _HORS_INVENTAIRE_CODES)

    def _write_secteur_block(first_row, secteurs, unite_filter):
        """Batch : secteur en col B, unité en col C, SUMIFS ref $B/$C."""
        n = len(secteurs)
        last_row = first_row + n - 1
        ws.range((first_row, 2), (last_row, 2)).value = [[s] for s in secteurs]
        ws.range((first_row, 3), (last_row, 3)).value = [[unite_filter]] * n
        ws.range((first_row, fy), (last_row, fy + NY - 1)).formula = [
            [f'=SUMIFS(CALCUL!{L(FY_CALCUL + j)}${cd0}:{L(FY_CALCUL + j)}${clast},'
             f'CALCUL!$B${cd0}:$B${clast},$B{first_row + k},'
             f'CALCUL!$H${cd0}:$H${clast},{SWITCH},'
             f'CALCUL!$F${cd0}:$F${clast},$C{first_row + k}{_excl_crit})'
             for j in range(NY)]
            for k in range(n)
        ]

    def _write_hors_inventaire_block(first_row, codes):
        """Batch : une ligne par code hors inventaire, SUMIFS sur le code (col D)."""
        n = len(codes)
        labels = [corr.loc[corr["code_traj_supra"] == c, "usage"].iloc[0] for c in codes]
        ws.range((first_row, 2), (first_row + n - 1, 2)).value = [[lbl] for lbl in labels]
        ws.range((first_row, 3), (first_row + n - 1, 3)).value = [["ktCO2e"]] * n
        ws.range((first_row, fy), (first_row + n - 1, fy + NY - 1)).formula = [
            [f'=SUMIFS(CALCUL!{L(FY_CALCUL + j)}${cd0}:{L(FY_CALCUL + j)}${clast},'
             f'CALCUL!$D${cd0}:$D${clast},"{code}",'
             f'CALCUL!$H${cd0}:$H${clast},{SWITCH})'
             for j in range(NY)]
            for code in codes
        ]

    # Couleur de l'onglet -> fond de la ligne 1
    tab_color = ws.couleur_onglet()

    # En-tête + switch brut/recalé
    ws.range("A1").value = "VISUALISATION RÉSULTATS - TOUS SECTEURS"
    r1 = ws.range((1, 1), (1, last_col))
    r1.police(gras=True)
    if tab_color:
        r1.color = tab_color
    ws.range("A2").value = "Affichage :"
    sw = ws.range("B2")
    sw.value = "Résultat recalé"
    sw.color = (255, 242, 204)
    sw.validation_liste("Résultat brut;Résultat recalé")
    ws.range((hrow, 1)).value = ["Indicateur", "Secteur", "Unité"]
    ws.range((hrow, fy)).formula = "=Annee_ref"
    ws.range((hrow, fy + 1), (hrow, last_col)).formula = [
        [f"={ycols[j-1]}{hrow}+1" for j in range(1, NY)]
    ]
    _fmt_header(ws.range((hrow, 1), (hrow, last_col)))

    cur = 5
    e_first = e_last = e_total = None
    e_hors_first = e_hors_last = None
    c_first = c_last = c_total = None

    # Codes GES hors inventaire (présents dans la correspondance active)
    hors_inv_ges = [c for c in _HORS_INVENTAIRE_CODES
                    if c in set(corr.loc[corr["type"] == "GES", "code_traj_supra"])]

    # Émissions GES par secteur (ktCO2e)
    if secteurs_ges:
        _section(cur, "Émissions GES par secteur (ktCO2e)")
        e_first = cur + 1
        _write_secteur_block(e_first, secteurs_ges, "ktCO2e")
        e_last = e_first + len(secteurs_ges) - 1
        e_total = e_last + 1
        ws.range((e_total, 2)).value = "TOTAL NET"
        ws.range((e_total, fy), (e_total, last_col)).formula = [
            [f"=SUM({yc}{e_first}:{yc}{e_last})" for yc in ycols]
        ]
        ws.range((e_total, 1), (e_total, last_col)).police(gras=True)
        cur = e_total
        if hors_inv_ges:
            # Catégories hors inventaire officiel : à part, en dernière
            # position, non comptées dans le TOTAL NET ci-dessus.
            e_hors_first = cur + 1
            _write_hors_inventaire_block(e_hors_first, hors_inv_ges)
            e_hors_last = e_hors_first + len(hors_inv_ges) - 1
            cur = e_hors_last
        _fmt_1d(ws.range((e_first, fy), (cur, last_col)))
        cur += 1

    # Consommation par secteur (GWh)
    if secteurs_cons:
        _section(cur + 1, "Consommation d'énergie finale par secteur (GWh)")
        c_first = cur + 2
        _write_secteur_block(c_first, secteurs_cons, "GWh")
        c_last = c_first + len(secteurs_cons) - 1
        c_total = c_last + 1
        ws.range((c_total, 2)).value = "TOTAL"
        ws.range((c_total, fy), (c_total, last_col)).formula = [
            [f"=SUM({yc}{c_first}:{yc}{c_last})" for yc in ycols]
        ]
        ws.range((c_total, 1), (c_total, last_col)).police(gras=True)
        _fmt_1d(ws.range((c_first, fy), (c_total, last_col)))
        cur = c_total + 1

    # ── Graphiques ────────────────────────────────────────────────────────
    anchor = cur + 2
    cb_col = last_col + 2
    step = NY + 2
    base = ws.range((anchor, 1))
    L0, T0, W, H, G = base.left, base.top, 700, 360, 20
    WA = 950  # aires empilées : largeur absolue fixe, identique sur tous les onglets
    n_chart_blocks = 0
    obs_c0 = obs_info.get("c0", 7)
    plan_c0 = plan_info.get("c0", 10)

    def _mk(src, ctype, title, left, top, zero_base=True):
        w = WA if ctype == "area_stacked" else W
        ch = ws.charts.add(left=left, top=top, width=w, height=H)
        ch.set_source_data(src)
        ch.chart_type = ctype
        ch.titre = title
        if ch.n_series > 1:
            ch.legende = "r" if ctype == "area_stacked" else "b"
        if ctype == "area_stacked" and zero_base:
            # Axe des valeurs (axe primaire) : forcer le zéro en base, sinon
            # l'auto-échelle Excel peut démarrer au-dessus de 0 et fausser
            # visuellement la lecture d'une aire empilée. À ne pas faire sur
            # le graphique pos/neg à double axe (zero_base=False) : le
            # calage du zéro y dépend de l'auto-échelle des deux axes.
            ch.ymin = 0
        return ch

    try:
        # ── GES : aires empilées + secteurs négatifs sous l'axe X ────────
        if secteurs_ges:
            rows_g = [e_first + k for k in range(len(secteurs_ges))]
            codes_g = list(secteurs_ges)
            if e_hors_first is not None:
                # Catégories hors inventaire : en dernière position, côté négatif
                rows_g += list(range(e_hors_first, e_hors_last + 1))
                codes_g += hors_inv_ges
            neg_set = _NEGATIVE_SECTORS | set(hors_inv_ges)
            neg_rows = [r for r, c in zip(rows_g, codes_g) if c in neg_set]

            # Total net : masqué quand la trajectoire UTCATF/CSC (+ hors
            # inventaire) est vide, cas de tous les territoires DROM - le
            # total net vaut alors le total brut, et la ligne ne fait que
            # retracer le sommet de l'aire empilée sans rien apporter.
            def _total_net(j):
                if not neg_rows:
                    return "=NA()"
                yc = L(fy + j)
                return (f'=IF(SUM({"+".join(f"{yc}{r}" for r in neg_rows)})=0,'
                        f'NA(),{yc}{e_total})')

            src, dispo = _chart_block_pos_neg(
                ws, cb_col + n_chart_blocks * step, rows_g, codes_g, neg_set,
                fy, NY, hrow, label_col=2, lines=[("Total net", _total_net)])
            ch = _mk(src, "area_stacked", "Émissions GES par secteur (ktCO2e)",
                     L0, T0, zero_base=not neg_rows)
            _format_pos_neg_area_chart(ch, dispo)
            n_chart_blocks += 1

        # ── Conso : aire empilée ─────────────────────────────────────────
        if secteurs_cons:
            s_ca = _chart_block(ws, cb_col + n_chart_blocks * step,
                                c_first, c_last, fy, NY, hrow, label_col=2)
            _mk(s_ca, "area_stacked", "Consommation par secteur (GWh)",
                 L0, T0 + H + G)
            n_chart_blocks += 1

        # ── Comparaison GES net (traj / observée / planifiée) ────────────
        if secteurs_ges and e_total:
            ges_obs_r = obs_info.get("total_GES")
            ges_plan_r = plan_info.get("total_GES")
            if ges_obs_r or ges_plan_r:
                s_gt = _total_block(ws, cb_col + n_chart_blocks * step, e_total,
                                    ges_obs_r or 0, ges_plan_r or 0, fy, NY, hrow,
                                    obs_c0=obs_c0, plan_c0=plan_c0)
                _mk(s_gt, "line", "Émissions GES nettes (ktCO2e)",
                     L0 + WA + G, T0)
                n_chart_blocks += 1

        # ── Comparaison conso (traj / observée / planifiée) ──────────────
        if secteurs_cons and c_total:
            cons_obs_r = obs_info.get("total_conso")
            cons_plan_r = plan_info.get("total_conso")
            if cons_obs_r or cons_plan_r:
                s_ct = _total_block(ws, cb_col + n_chart_blocks * step, c_total,
                                    cons_obs_r or 0, cons_plan_r or 0, fy, NY, hrow,
                                    obs_c0=obs_c0, plan_c0=plan_c0)
                _mk(s_ct, "line", "Consommation totale (GWh)",
                     L0 + WA + G, T0 + H + G)
                n_chart_blocks += 1

        if n_chart_blocks:
            ws.range((1, cb_col), (1, cb_col + n_chart_blocks * step)).masquer_colonnes()
    except Exception as exc:
        print(f"    AVERTISSEMENT : création des graphiques échouée ({exc}). Tables OK.")

    print(f"    → {sheet_name} construit.")


def _raw_col(headers, code):
    """Trouve l'en-tête `CODE_AAAA` de Data_EPCI correspondant à `code`."""
    for h in headers:
        if h and re.fullmatch(rf"{re.escape(code)}_\d{{4}}", str(h)):
            return str(h)
    return None


def _warn_valeur_territoire_absente(
    ws, r_data: int, n_lignes: int, col_territoire: int, col_correction: int,
    col_utilise: int, col_fin: int,
    color_fond: int = 0xCCCCFF, color_police: int = 0x0000C0,
) -> None:
    """
    Met en évidence (fond + police grasse, couleurs paramétrables - rouge par
    défaut, même style que l'alerte de zone du bloc ⓪) la cellule
    « Territoire » elle-même d'une ligne de pivot/recalage vide - cas typique
    d'une sélection EPT/Paris sur un indicateur qui n'existe qu'à la maille
    EPCI (résidentiel, tertiaire, DJU, énergie-climat...). L'alerte s'éteint
    dès que l'utilisateur renseigne sa correction manuelle : elle ne signale
    que ce qui reste à trancher, pas ce qui a déjà été corrigé.

    `color_fond`/`color_police` distinguent le bloc ② (pivots, rouge -
    défaut) du bloc ③ (recalage, orange - cf. appelants) : deux natures de
    donnée manquante différentes, pas la même urgence de correction.

    Seules les lignes que la zone utilise (`col_utilise` = 1) sont signalées :
    les autres (UTCATF aux DROM, pivot remplacé par une alternative...) sont
    grisées du code jusqu'à `col_fin`, et leur correction manuelle est refusée
    (elle serait sans effet sur le calcul).

    Paramètres : colonnes 1-indexées (comme `ws.range((row, col))`), couleurs
    au format BGR des constantes COM historiques (converties par `bgr`).
    """
    if n_lignes <= 0:
        return
    from openpyxl.utils import get_column_letter as L

    col_terr_letter = L(col_territoire)
    col_corr_letter = L(col_correction)
    col_util_letter = L(col_utilise)
    cible = ws.range((r_data, col_territoire), (r_data - 1 + n_lignes, col_territoire))
    cible.supprimer_mfc()
    cible.mfc(f'AND(${col_terr_letter}{r_data}="",${col_corr_letter}{r_data}="",'
              f'${col_util_letter}{r_data}=1)',
              fond=bgr(color_fond), police=bgr(color_police), gras=True)
    # Ligne grisée quand la zone n'utilise pas le code : elle reste lisible
    # (valeurs éventuelles, motif dans le libellé) sans appeler de saisie.
    cible = ws.range((r_data, col_territoire - 3), (r_data - 1 + n_lignes, col_fin))
    cible.mfc(f'${col_util_letter}{r_data}=0', police=bgr(0x808080), italique=True)

    # Correction manuelle d'un code non utilisé : CALCUL ne la lit jamais
    # (aucune ligne Traj_supra pour la zone), une saisie y serait ignorée sans
    # rien dire. Cellule grisée (masque son fond jaune de saisie) et refusée.
    # Une valeur saisie avant un changement de zone reste en place, grisée.
    corr = ws.range((r_data, col_correction), (r_data - 1 + n_lignes, col_correction))
    # Priorité sur la règle de ligne (police seule) qui couvre aussi cette
    # colonne : sans cela le fond gris ne s'afficherait pas.
    corr.mfc(f'${col_util_letter}{r_data}=0', fond=bgr(0xD9D9D9), police=bgr(0x808080),
             italique=True, premier=True)
    corr.validation_formule(
        f'${col_util_letter}{r_data}<>0',
        titre="Code non utilisé pour cette zone",
        message=("Ce code n'intervient pas dans le calcul de la zone "
                 "sélectionnée (voir le motif dans le libellé) : une "
                 "saisie serait sans effet."),
    )


def _motifs_codes_non_utilises(zones: list[str], champ: str) -> dict[str, dict[str, str]]:
    """
    Pour chaque code (`champ` = "code_pivot" ou "code_recalage") de la
    configuration de base qu'une zone n'utilise pas : {code: {zone: motif}}.
    Le motif explique à l'utilisateur pourquoi aucune saisie n'est attendue
    (secteur désactivé, pivot de substitution...), là où une simple cellule
    vide ressemblerait à une donnée manquante.
    """
    from pipeline.territorialize import (
        _load_yaml, load_correspondance, load_zone_config,
    )
    base = _load_yaml()["sous_secteurs"]
    motifs: dict[str, dict[str, list[str]]] = {}
    for zone in zones:
        zc = load_correspondance(zone)
        par_ss = dict(zip(zc["code_traj_supra"], zc[champ]))
        utilises = {c for c in zc[champ] if isinstance(c, str) and c}
        off = set(load_zone_config(zone).get("secteurs_desactives") or [])
        for ss in base:
            code = ss.get(champ)
            if not code or code in utilises:
                continue
            # Motifs courts : ils s'ajoutent au libellé, dans une colonne de
            # largeur fixe (le grisé et le message de la ligne 4 disent déjà
            # que la ligne n'est pas utilisée).
            if ss.get("secteur") in off:
                m = "non modélisé"          # le libellé porte déjà le secteur
            elif ss["code_traj_supra"] in par_ss:
                remplacant = par_ss[ss["code_traj_supra"]]
                m = (f"→ {remplacant}"
                     if isinstance(remplacant, str) and remplacant
                     else "sans objet")
            else:
                m = "sous-secteur désactivé"
            lst = motifs.setdefault(code, {}).setdefault(zone, [])
            if m not in lst:
                lst.append(m)
    return {c: {z: " ; ".join(ms) for z, ms in mz.items()} for c, mz in motifs.items()}


def _formule_motif(motifs_zone: dict[str, str]) -> str:
    """
    Expression Excel du motif de non-utilisation selon `terr_zone`, zones
    regroupées par motif identique. Repli générique pour un code qui n'est
    propre qu'à d'autres zones (pivot de substitution DROM vu dans l'hexagone).
    """
    par_motif: dict[str, list[str]] = {}
    for z, m in motifs_zone.items():
        par_motif.setdefault(m, []).append(z)
    expr = '"propre à d\'autres zones"'
    for m, zs in reversed(list(par_motif.items())):
        liste = ",".join(f'"{z}"' for z in zs)
        expr = f'IF(ISNUMBER(MATCH(terr_zone,{{{liste}}},0)),"{m}",{expr})'
    return expr


def _build_caract_territoire(wb, sheet_name: str, zones: list[str]) -> None:
    """
    Construit l'onglet « Territoire » : 4 blocs autonomes (calculés depuis la table
    `Data_EPCI` de l'onglet Data_EPCI, sans dépendre de l'onglet Data_EPCI).

      ⓪ Zone : déduite des EPCI sélectionnés. Définit `terr_zone`, dont dépend
         tout l'onglet CALCUL. Porte le garde-fou de sélection multi-zone.
      ① Sélection des EPCI : liste manuelle via dropdown du libellé (traduit en code).
         Les libellés en doublon sont désambiguïsés « Nom (code) ». Définit terr_num_epci.
      ② Pivots : valeur territoire / override / retenu / zone / part.
      ③ Données de recalage : idem.

    **Multi-zone.** Les blocs ② et ③ couvrent l'union des pivots et des codes de
    recalage de toutes les zones ; le total de zone servant de référence suit
    `terr_zone`. Mélanger des territoires de zones différentes n'a aucun sens
    (les ratios se calculent au sein d'une zone) : la sélection est alors
    déclarée invalide, `terr_zone` vaut "" et les résultats sont neutralisés
    plutôt que faux.
    """
    from openpyxl.utils import get_column_letter as L
    from pipeline.territorialize import (
        load_correspondance, load_correspondance_union,
    )

    corr = load_correspondance_union(zones)
    names = [s.name for s in wb.sheets]
    old = next((nm for nm in (sheet_name, "Correction pivots", "Caract_territoire TEST") if nm in names), None)
    if old:
        ws = wb.sheets[old]
        if old != sheet_name:
            ws.name = sheet_name
    else:
        after = wb.sheets["Caract_territoire"] if "Caract_territoire" in names else None
        ws = wb.sheets.add(sheet_name, after=after)
    print(f"  Construction de l'onglet « {sheet_name} » …")
    ws.clear()

    raw_hdr = [h for h in wb.sheets["Data_EPCI"].range((1, 1), (1, 120)).value if h]
    MASK = "ISNUMBER(MATCH(Data_EPCI[code_epci],terr_num_epci,0))"

    def terr(col):
        return f'SUMPRODUCT({MASK}*Data_EPCI[{col}])'

    def nat(col):
        # La ligne agrégée de la zone porte son nom comme code_epci.
        return f'INDEX(Data_EPCI[{col}],MATCH(terr_zone,Data_EPCI[code_epci],0))'

    # Union des pivots et codes de recalage : un même onglet sert toutes les zones.
    pivots = list(dict.fromkeys(corr["code_pivot"]))
    recal_codes = list(dict.fromkeys([o for o in corr["code_recalage"] if isinstance(o, str) and o]))
    for zone in zones:
        zc = load_correspondance(zone)
        pivots += [p for p in zc["code_pivot"] if p not in pivots]
        recal_codes += [o for o in zc["code_recalage"]
                        if isinstance(o, str) and o and o not in recal_codes]

    ws.range("A1").value = "Caractérisation du territoire : ① sélection des EPCI, ② visualisation/correction des pivots, ③ visualisation/correction des valeurs énergie-climat"
    ws.range("A1").police(gras=True)

    # Décalage de 3 lignes des blocs ①②③ pour loger le bloc ⓪ en tête.
    r_titre, r_hdr, r_data = 5, 6, 7
    # Colonnes masquées : code / nom / libellé désambiguïsé, après le bloc ③
    # (N à V) et une colonne de séparation (W).
    HC, HN, HL = 24, 25, 26
    # Colonnes masquées des blocs ② et ③ : code utilisé par la zone retenue
    # (1/0, numérique pour une règle de mise en forme indépendante de la
    # langue d'Excel) et motif sinon. Placées après les compteurs de zone de
    # la ligne 2, qui occupent HC, HC+1... une colonne par zone. Puis, pour
    # l'alerte de recalage important du bloc ③ : code compté dans le total du
    # territoire (HSR, 0 pour l'UTCATF), valeur attendue d'après le pivot (HBR),
    # part du secteur dans le total (HPR) et niveau d'alerte 0/1/2 (HAR).
    HUP = max(HL, HC + len(zones) - 1) + 1
    HMP, HUR, HMR = HUP + 1, HUP + 2, HUP + 3
    HSR, HBR, HPR, HAR = HMR + 1, HMR + 2, HMR + 3, HMR + 4
    motifs_pivot = _motifs_codes_non_utilises(zones, "code_pivot")
    motifs_recal = _motifs_codes_non_utilises(zones, "code_recalage")

    def utilise(ts_col, code_cell, si_utilise="1"):
        # Traj_supra ne porte, par zone, que les sous-secteurs actifs avec leur
        # pivot effectif : c'est la source que lit CALCUL. Zone indéterminée
        # (sélection vide ou multi-zone) : tout est considéré utilisé, comme
        # avant la distinction.
        return (f'=IF(terr_zone="",1,IF(COUNTIFS(Traj_supra[{ts_col}],{code_cell},'
                f'Traj_supra[{TRAJ_SUPRA_COL_ZONE}],terr_zone)>0,{si_utilise},0))')

    # ── ⓪ Zone du périmètre sélectionné ───────────────────────────────────
    # Un compteur d'EPCI sélectionnés par zone, dans les colonnes masquées.
    for i, z in enumerate(zones):
        ws.range((2, HC + i)).formula = f'=SUMPRODUCT({MASK}*(Data_EPCI[zone]="{z}"))'
    cnt = [f"${L(HC + i)}$2" for i in range(len(zones))]
    # « +0 » : avec une seule zone, la somme se réduit à un booléen, et VRAI=1
    # est FAUX dans Excel (la sélection serait toujours déclarée invalide)
    n_zones = "(" + "+".join(f"({c}>0)" for c in cnt) + "+0)"
    total_sel = "+".join(cnt)

    # Première zone dont le compteur est non nul ; "" si 0 ou plusieurs zones.
    expr = '""'
    for i in range(len(zones) - 1, -1, -1):
        expr = f'IF({cnt[i]}>0,"{zones[i]}",{expr})'

    ws.range("A2").value = "⓪ Zone du périmètre sélectionné"
    ws.range("A2").police(gras=True)
    ws.range((3, 1)).value = "Zone détectée"
    ws.range((3, 2)).formula = f'=IF({n_zones}=1,{expr},"")'
    ws.range((3, 2)).police(gras=True)
    # Le message est long : il est logé dans une plage fusionnée qui couvre
    # les blocs ② et ③, sinon il déborde en dehors du fond coloré et se fait
    # tronquer par la première cellule non vide à sa droite. Démarre en D
    # (pas de libellé "Contrôle" séparé, peu utile).
    COL_MSG_FIN = 19          # jusqu'à la colonne S, avant les colonnes masquées
    cible_msg = ws.range((3, 4), (3, COL_MSG_FIN))
    cible_msg.fusionner()
    cible_msg.alignement(horizontal="left", vertical="center")
    msg_erreur = (
        f"ERREUR : la sélection mélange plusieurs zones "
        f"({', '.join(zones)}). Les ratios se calculent au sein d'une seule "
        f"zone : les résultats sont neutralisés tant que la sélection n'est "
        f"pas corrigée."
    )
    ws.range((3, 4)).formula = (
        f'=IF({n_zones}=0,"Aucun territoire sélectionné.",'
        f'IF({n_zones}=1,"OK - "&({total_sel})&" territoire(s).",'
        f'"{msg_erreur}"))'
    )
    # Rouge tant que la sélection n'est pas exploitable (zone indéterminée).
    # Règle sur formule (type « expression ») : une règle sur la valeur de la
    # cellule comparerait le texte du message au résultat du test, et ne se
    # déclencherait jamais.
    cible = ws.range((3, 1), (3, COL_MSG_FIN))
    cible.supprimer_mfc()
    cible.mfc('$B$3=""', police=bgr(0x0000C0), gras=True,      # BGR : rouge
              fond=bgr(0xCCCCFF))                              # fond rosé

    wb.names.add("terr_zone", f"='{sheet_name}'!$B$3")

    # ── ① Sélection des EPCI ───────────────────────────────────────────────
    ws.range((r_titre, 1)).value = "① Sélection des EPCI"
    ws.range((r_titre, 1)).police(gras=True)
    ws.range((r_hdr, 1)).value = ["Libellé EPCI", "Code EPCI"]
    _fmt_header(ws.range((r_hdr, 1), (r_hdr, 2)))
    # Lecture des EPCI depuis Data_EPCI (hors agrégats)
    from collections import Counter
    dp_ws = wb.sheets["Data_EPCI"]
    idx_code = raw_hdr.index("code_epci") + 1
    idx_nom = raw_hdr.index("nom_epci") + 1
    idx_zone = raw_hdr.index("zone") + 1
    idx_dep = raw_hdr.index("code_departement") + 1
    idx_niveau = raw_hdr.index("niveau_geo") + 1 if "niveau_geo" in raw_hdr else None
    tbl = dp_ws.tables[0]
    n_data = tbl.range.rows.count - 1
    raw_codes = dp_ws.range((2, idx_code), (1 + n_data, idx_code)).value
    raw_noms = dp_ws.range((2, idx_nom), (1 + n_data, idx_nom)).value
    raw_zones = dp_ws.range((2, idx_zone), (1 + n_data, idx_zone)).value
    raw_deps = dp_ws.range((2, idx_dep), (1 + n_data, idx_dep)).value
    # niveau_geo peut être absent d'un bdd_epci_wide.xlsx généré avant son
    # ajout : repli sur "EPCI" pour tout le monde (comportement historique).
    raw_niveaux = (
        dp_ws.range((2, idx_niveau), (1 + n_data, idx_niveau)).value
        if idx_niveau else ["EPCI"] * n_data
    )
    if not isinstance(raw_codes, list):
        raw_codes, raw_noms = [raw_codes], [raw_noms]
        raw_zones, raw_deps = [raw_zones], [raw_deps]
        raw_niveaux = [raw_niveaux]

    # EPCI d'une zone non portée par le classeur (Mayotte, hors périmètre, cf.
    # config.settings.ZONES_TERRITORIALISEES) : non proposés à la sélection.
    # Le pipeline ne les produit pas ; sans ce filtre le tableur les laisserait
    # choisir et produirait des résultats appuyés sur des pivots absents.
    from config.settings import CODES_TOTAUX as _AGGREGATES

    def _str(v):
        return str(int(v)) if isinstance(v, float) else str(v)

    epci_data, exclus = [], Counter()
    for c, n, z, d, niveau in zip(raw_codes, raw_noms, raw_zones, raw_deps, raw_niveaux):
        if not c or _str(c) in _AGGREGATES:
            continue
        if _code(z) not in zones:
            exclus[_code(z)] += 1
            continue
        epci_data.append((_str(c), n, z, niveau or "EPCI"))
    if exclus:
        print(f"    → {sum(exclus.values())} EPCI hors des zones du classeur écartés "
              f"de la sélection ({', '.join(f'{z} : {k}' for z, k in sorted(exclus.items()))})")
    name_counts = Counter(n for _, n, _, _ in epci_data)
    epci_labels = [
        f"{n} ({c})" if name_counts[n] > 1 else n
        for c, n, _, _ in epci_data
    ]
    n_epci = len(epci_data)

    # Taille de la plage de sélection dérivée du nombre réel d'entités
    # (EPCI + EPT + Paris) plutôt qu'une constante figée : un nombre codé en
    # dur ici a déjà silencieusement tronqué la liste (et avec elle "Paris",
    # trié en dernier par MATCH textuel) le jour où les EPT/Paris ont porté
    # le total au-delà de l'ancienne valeur.
    NSEL = n_epci
    s0, s1 = r_data, r_data - 1 + NSEL         # lignes de sélection

    # Liste de référence (masquée) : code, nom, libellé désambiguïsé
    ws.range((s0, HC)).options(transpose=True).value = [c for c, _, _, _ in epci_data]
    ws.range((s0, HN)).options(transpose=True).value = [n for _, n, _, _ in epci_data]
    ws.range((s0, HL)).options(transpose=True).value = epci_labels
    # Code = recherche du libellé désambiguïsé sélectionné
    ws.range((s0, 2), (s1, 2)).formula = (
        f'=IFERROR(INDEX(${L(HC)}${s0}:${L(HC)}${s1},MATCH($A{s0},${L(HL)}${s0}:${L(HL)}${s1},0)),"")'
    )
    # Dropdown sur la colonne A (liste = libellés désambiguïsés)
    sel = ws.range((s0, 1), (s1, 1))
    sel.validation_liste(f"=${L(HL)}${s0}:${L(HL)}${s1}")
    ws.range((s0, 1), (s1, 1)).color = (255, 242, 204)
    # Colonnes du bloc ③ qui étaient masquées avant son élargissement (U à W) :
    # ws.clear() ne les réaffiche pas.
    ws.range((1, 20), (1, HC - 1)).masquer_colonnes(False)
    ws.range((1, HC), (1, HAR)).masquer_colonnes()

    # Pré-remplir avec tous les EPCI (hors EPT/Paris - subdivisions de la
    # MGP, jamais dans le pré-remplissage par défaut : les inclure
    # doublerait la contribution de la MGP dans une sélection "tout le
    # monde") de la première zone : le classeur doit s'ouvrir sur une
    # sélection valide, donc mono-zone.
    zone_defaut = zones[0]
    pre_fill = [
        lbl for lbl, (_, _, z, niveau) in zip(epci_labels, epci_data)
        if z == zone_defaut and niveau == "EPCI"
    ]
    if pre_fill:
        ws.range((s0, 1)).options(transpose=True).value = pre_fill

    # terr_num_epci → colonne des codes sélectionnés
    wb.names.add("terr_num_epci", f"='{sheet_name}'!$B${s0}:$B${s1}")

    # ── ② Pivots ─────────────────────────────────────────────────
    pc = 4                                      # 1re colonne du bloc pivots (D)
    ws.range((r_titre, pc)).value = "② Pivots"
    ws.range((r_titre, pc)).police(gras=True)
    ws.range((r_hdr, pc)).value = ["Code pivot", "Libellé", "Unité", "Territoire", "Correction manuelle", "Retenu", "Zone", "Part", "Poids additif"]
    _fmt_header(ws.range((r_hdr, pc), (r_hdr, pc + 8)))
    INCL_ZONE = '(Data_EPCI[zone]=terr_zone)'
    UP, MP = L(HUP), L(HMP)
    for i, p in enumerate(pivots):
        r = r_data + i
        col = _raw_col(raw_hdr, p)
        ws.range((r, pc)).value = p
        ws.range((r, HUP)).formula = utilise("Code pivot", f"$D{r}")
        ws.range((r, HMP)).formula = f'={_formule_motif(motifs_pivot.get(p, {}))}'
        # Marqueur "⚠ " devant le libellé si Territoire ET Correction manuelle
        # sont vides : signal garanti (texte, pas une mise en forme
        # conditionnelle dont le rendu s'est révélé peu fiable) pour
        # inciter à une correction manuelle. S'éteint dès que l'une des deux
        # cellules est renseignée. Un pivot que la zone n'utilise pas n'appelle
        # aucune saisie : pas de marqueur, mais le motif en suffixe.
        ws.range((r, pc + 1)).formula = (
            f'=IF(AND($G{r}="",$H{r}="",${UP}{r}=1),"⚠ ","")'
            f'&IFERROR(INDEX(Catalogue_data_EPCI[libelle],MATCH($D{r},Catalogue_data_EPCI[code],0)),"")'
            f'&IF(${UP}{r}=1,""," ("&${MP}{r}&")")'
        )
        ws.range((r, pc + 2)).formula = f'=IFERROR(INDEX(Catalogue_data_EPCI[unite],MATCH($D{r},Catalogue_data_EPCI[code],0)),"")'
        # Même garde que le bloc ③ (dispo=0 → "") : un pivot absent à la maille
        # sélectionnée (ex. EPT/Paris sur résidentiel, tertiaire, DJU...) doit
        # remonter vide, pas un 0 indiscernable d'une vraie valeur nulle -
        # sinon "Retenu" adopte silencieusement ce faux zéro.
        if col:
            dispo = f'SUMPRODUCT({MASK}*ISNUMBER(Data_EPCI[{col}]))'
            ws.range((r, pc + 3)).formula = f'=IFERROR(IF({dispo}=0,"",{terr(col)}),"")'
        else:
            ws.range((r, pc + 3)).formula = ""
        ws.range((r, pc + 5)).formula = f'=IF($H{r}="",$G{r},$H{r})'
        ws.range((r, pc + 6)).formula = f'=IFERROR({nat(col)},"")' if col else ""
        # Part et poids vides pour un pivot non utilisé : un 0 % laisserait
        # croire à un territoire absent du pivot. CALCUL ne les cherche que
        # sur les pivots de la zone.
        ws.range((r, pc + 7)).formula = f'=IF(${UP}{r}=1,IFERROR($I{r}/$J{r},""),"")'
        if col:
            abs_sum = f'SUMPRODUCT({INCL_ZONE}*ABS(Data_EPCI[{col}]))'
            ws.range((r, pc + 8)).formula = f'=IF(${UP}{r}=1,IFERROR(ABS($I{r})/{abs_sum},0),"")'
    np_ = len(pivots)
    ws.range((r_data, pc + 4), (r_data - 1 + np_, pc + 4)).color = (255, 242, 204)
    _fmt_1d(ws.range((r_data, pc + 3), (r_data - 1 + np_, pc + 6)))
    # 3 décimales : la part d'un EPCI dans la zone est le plus souvent < 1 %
    # et s'afficherait "0 %" sinon.
    ws.range((r_data, pc + 7), (r_data - 1 + np_, pc + 8)).number_format = "0.000%"
    # Colonne Part (8,1 dans le template) trop étroite pour "100,000%" : "####".
    ws.range((1, pc + 7), (1, pc + 8)).column_width = 12.36328125
    # Libellé suffixé d'un motif selon la zone : la police se réduit plutôt que
    # le texte soit tronqué par la colonne Unité (largeur fixée par le template).
    ws.range((r_data, pc + 1), (r_data - 1 + np_, pc + 1)).alignement(shrink=True)
    ws.range((r_hdr, pc), (r_data - 1 + np_, pc + 8)).bordures()
    _warn_valeur_territoire_absente(ws, r_data, np_, col_territoire=pc + 3, col_correction=pc + 4,
                                    col_utilise=HUP, col_fin=pc + 8)

    # ── ③ Données de recalage ──────────────────────────────
    oc = 14                                     # 1re colonne du bloc recalage (N)
    OC = L(oc)                                  # lettre de la colonne code recalage
    OC3, OC4, OC5 = L(oc + 3), L(oc + 4), L(oc + 5)
    ws.range((r_titre, oc)).value = "③ Données de recalage (OREC dans l'hexagone, IGT CITEPA aux DROM)"
    ws.range((r_titre, oc)).police(gras=True)
    ws.range((r_hdr, oc)).value = ["Code recalage", "Libellé", "Unité", "Territoire", "Correction manuelle", "Retenu",
                                   "Ratio recalage", "Ratio zone", "Écart pivot"]
    _fmt_header(ws.range((r_hdr, oc), (r_hdr, oc + 8)))
    UR, MR = L(HUR), L(HMR)
    OC2, OC6, OC7, OC8 = L(oc + 2), L(oc + 6), L(oc + 7), L(oc + 8)
    SR, BR, PR, AR = L(HSR), L(HBR), L(HPR), L(HAR)
    r_fin_rec = r_data - 1 + len(recal_codes)
    codes_utcatf = set(corr.loc[corr["secteur"] == "UTCATF", "code_recalage"].dropna())

    def plage_rec(c):
        return f'${c}${r_data}:${c}${r_fin_rec}'

    def somme_calcul(etape, r):
        # Σ des lignes de CALCUL de l'étape pour ce code de recalage, en année
        # de référence (colonne K) : même convention que la formule « Ratio
        # recalage » de CALCUL, dont la colonne « Ratio recalage » reprend la valeur.
        return f'SUMIFS(CALCUL!$K:$K,CALCUL!$H:$H,"{etape}",CALCUL!$G:$G,${OC}{r})'
    for i, o in enumerate(recal_codes):
        r = r_data + i
        col = _raw_col(raw_hdr, o)
        ws.range((r, oc)).value = o
        # Statut 2 = utilisé mais facultatif : aucun EPCI de la zone n'a de
        # donnée observée (toute la branche conso aux DROM). Le recalage est
        # alors sans objet par défaut (pass-through), mais reste ouvert à une
        # saisie locale (observatoire de l'énergie...) : ni alerte ni grisage.
        dispo_zone = f'SUMPRODUCT({INCL_ZONE}*ISNUMBER(Data_EPCI[{col}]))' if col else "0"
        ws.range((r, HUR)).formula = utilise(
            TRAJ_SUPRA_COL_RECALAGE, f"${OC}{r}", si_utilise=f"IF({dispo_zone}=0,2,1)")
        ws.range((r, HMR)).formula = f'={_formule_motif(motifs_recal.get(o, {}))}'
        # Cf. commentaire équivalent du bloc ② pivots.
        ws.range((r, oc + 1)).formula = (
            f'=IF(AND(${OC3}{r}="",${OC4}{r}="",${UR}{r}=1),"⚠ ","")'
            f'&IFERROR(INDEX(Catalogue_data_EPCI[libelle],MATCH(${OC}{r},Catalogue_data_EPCI[code],0)),"")'
            f'&IF(${UR}{r}=0," ("&${MR}{r}&")",'
            f'IF(${UR}{r}=2," (facultatif)",""))'
        )
        ws.range((r, oc + 2)).formula = f'=IFERROR(INDEX(Catalogue_data_EPCI[unite],MATCH(${OC}{r},Catalogue_data_EPCI[code],0)),"")'
        # SUMPRODUCT lit une cellule vide comme un zéro : sans le compteur
        # ISNUMBER, un secteur sans donnée observée (cas de toute la branche
        # conso aux DROM) serait indiscernable d'un zéro réel, et le ratio de
        # recalage vaudrait 0 au lieu de 1 - annulant le résultat.
        if col:
            dispo = f'SUMPRODUCT({MASK}*ISNUMBER(Data_EPCI[{col}]))'
            ws.range((r, oc + 3)).formula = (
                f'=IFERROR(IF({dispo}=0,"",{terr(col)}),"")')
        else:
            ws.range((r, oc + 3)).formula = ""
        ws.range((r, oc + 5)).formula = f'=IF(${OC4}{r}="",${OC3}{r},${OC4}{r})'
        # Alerte de recalage important (cf. SEUIL_ECART_PIVOT_*) : ratio de la
        # sélection, ratio de la zone (Σ observé de zone / Σ trajectoire de
        # zone), et leur rapport.
        ws.range((r, oc + 6)).formula = (
            f'=IF(OR(${OC5}{r}="",${UR}{r}=0),"",'
            f'IFERROR(${OC5}{r}/{somme_calcul("Résultat brut", r)},""))')
        ws.range((r, oc + 7)).formula = (
            f'=IF(${UR}{r}=0,"",IFERROR({nat(col)}/{somme_calcul("Valeur traj supra", r)},""))'
            if col else "")
        ws.range((r, oc + 8)).formula = (
            f'=IF(OR(${OC6}{r}="",${OC7}{r}=""),"",IF(${OC7}{r}=0,"",${OC6}{r}/${OC7}{r}))')
        # Part du secteur dans le total du territoire, parmi les codes de même
        # unité utilisés par la zone, hors UTCATF (puits et sources dont la
        # masse écraserait les émissions d'un territoire forestier ; son écart
        # au pivot vaut 1 par construction, le pivot étant l'observé ALDO).
        # Part la plus forte entre l'observé et l'attendu d'après le pivot
        # (brut ramené au niveau de la zone) : un secteur que l'observé réduit
        # à presque rien (donnée incomplète) reste signalé.
        ws.range((r, HSR)).value = 0 if o in codes_utcatf else 1
        ws.range((r, HBR)).formula = (
            f'=IF(OR(${OC6}{r}="",${OC7}{r}=""),"",IFERROR({somme_calcul("Résultat brut", r)}*${OC7}{r},""))')

        def part(c):
            crit = f'{plage_rec(OC2)},${OC2}{r},{plage_rec(UR)},1,{plage_rec(SR)},1'
            return (f'ABS(N(${c}{r}))/(SUMIFS({plage_rec(c)},{crit},{plage_rec(c)},">0")'
                    f'-SUMIFS({plage_rec(c)},{crit},{plage_rec(c)},"<0"))')
        ws.range((r, HPR)).formula = (
            f'=IF(OR(${OC5}{r}="",${SR}{r}=0),"",IFERROR(MAX({part(OC5)},IFERROR({part(BR)},0)),""))')
        o_, r_ = SEUIL_ECART_PIVOT_ORANGE, SEUIL_ECART_PIVOT_ROUGE
        e = f'${OC8}{r}'
        ws.range((r, HAR)).formula = (
            f'=IF(OR({e}="",${PR}{r}=""),0,IF(${PR}{r}<{SEUIL_PART_RECALAGE},0,'
            f'IF(OR({e}<=0,{e}>{r_},{e}<1/{r_}),2,IF(OR({e}>{o_},{e}<1/{o_}),1,0))))')
    no_ = len(recal_codes)
    ws.range((r_data, oc + 4), (r_data - 1 + no_, oc + 4)).color = (255, 242, 204)
    _fmt_1d(ws.range((r_data, oc + 3), (r_data - 1 + no_, oc + 5)))
    ws.range((r_data, oc + 1), (r_data - 1 + no_, oc + 1)).alignement(shrink=True)   # cf. bloc ②
    ws.range((r_data, oc + 6), (r_data - 1 + no_, oc + 8)).number_format = "0.00"
    ws.range((1, oc + 6), (1, oc + 8)).column_width = 13.6328125
    ws.range((r_hdr, oc), (r_data - 1 + no_, oc + 8)).bordures()
    _warn_valeur_territoire_absente(
        ws, r_data, no_, col_territoire=oc + 3, col_correction=oc + 4,
        col_utilise=HUR, col_fin=oc + 8,
        color_fond=0x99CCFF, color_police=0x0066CC,   # BGR -> orange (distingue du rouge des pivots)
    )
    # Recalage important : orange / rouge selon le niveau calculé en HAR
    # (comparaison de cellule seule dans la règle, cf. compteurs de la ligne 4).
    cible = ws.range((r_data, oc + 6), (r_data - 1 + no_, oc + 8))
    for niveau, fond, police in ((2, 0xCCCCFF, 0x0000C0), (1, 0x99CCFF, 0x0066CC)):
        cible.mfc(f'${AR}{r_data}={niveau}', fond=bgr(fond), police=bgr(police), gras=True)
    entete = ws.range((r_hdr, oc + 8))
    entete.note(
        "Écart pivot = ratio de recalage de la sélection / ratio de la zone.\n"
        "Le ratio de zone traduit l'écart entre données observées et SNBC3, commun à "
        "tous les territoires. L'écart pivot mesure ce que le pivot ne capte pas du "
        f"territoire : au-delà d'un facteur {SEUIL_ECART_PIVOT_ORANGE} (orange) ou "
        f"{SEUIL_ECART_PIVOT_ROUGE} (rouge), pour un secteur pesant au moins "
        f"{SEUIL_PART_RECALAGE:.0%} du total, le niveau de l'année de référence reste "
        "juste, mais la répartition entre sous-secteurs, et donc la dynamique du "
        "secteur, est à considérer avec prudence.")

    # ⓪bis Résumé "données manquantes" pour la sélection courante - complète
    # les alertes ligne par ligne des blocs ② et ③ par un signal global,
    # visible sans avoir à parcourir les ~50 lignes de pivots/recalage.
    # Un message par bloc (pas un seul message combiné) : chacun aligné en
    # colonne sous SON bloc (D-L sous ②, N-S sous ③, qui s'arrête pile à
    # COL_MSG_FIN) et coloré comme les alertes ligne par ligne du bloc
    # correspondant (rouge pour les pivots, orange pour le recalage) - cf.
    # discussion : aligner par colonne (sous le bloc concerné) plutôt que par
    # ligne (empiler une ligne ⓪ter) évite de décaler tous les indices de
    # lignes des blocs ①②③ qui suivent.
    #
    # Porte sur "Retenu" (pas "Territoire") : une correction manuelle déjà
    # renseignée doit faire baisser le compteur - "Retenu" est déjà l'un ou
    # l'autre (IF(Correction="",Territoire,Correction)), donc vide seulement
    # si aucun des deux ne l'est. Condition à un seul argument (pas de
    # ET()/OU()) : pas concerné par le piège de localisation des noms de
    # fonction rencontré sur l'alerte de zone.
    #
    # Seuls les codes utilisés par la zone comptent : les autres (UTCATF aux
    # DROM, pivots remplacés par une alternative) sont dénombrés à part, dans
    # un complément d'information qui ne déclenche pas l'alerte.
    def plage(col, n):
        return f'${L(col)}${r_data}:${L(col)}${r_data - 1 + n}'

    n_manquants_pivots_formula = (
        f'SUMPRODUCT(({plage(pc + 5, np_)}="")*({plage(HUP, np_)}=1))'
    )
    n_manquants_recalage_formula = (
        f'SUMPRODUCT(({plage(oc + 5, no_)}="")*({plage(HUR, no_)}=1))'
    )

    def info_non_utilises(col_util, n, nature, precision='""'):
        n_nu = f'SUMPRODUCT(({plage(col_util, n)}=0)*1)'
        return (f'IF({n_nu}=0,""," "&{n_nu}&" {nature} grisé(s) : non utilisé(s) pour "'
                f'&terr_zone&{precision}&", aucune saisie requise.")')

    # Légende du "→" seulement si un pivot grisé est effectivement remplacé
    # (DROM) : dans l'hexagone, le seul pivot grisé est propre aux DROM.
    precision_remplacement = (
        f'IF(SUMPRODUCT(({plage(HUP, np_)}=0)*(LEFT({plage(HMP, np_)},1)="→"))>0,'
        f'" (→ : pivot de remplacement)","")'
    )

    # Recalage facultatif (statut 2) : sans donnée, la trajectoire est conservée.
    n_facultatifs = f'SUMPRODUCT(({plage(HUR, no_)}=2)*({plage(oc + 5, no_)}=""))'
    info_facultatifs = (
        f'IF({n_facultatifs}=0,""," "&{n_facultatifs}&" code(s) facultatif(s) : pas de '
        f'donnée dans la zone, saisie possible (sinon pas de recalage).")'
    )

    ws.range((4, 1)).value = "Données manquantes"
    ws.range((4, 2)).formula = f'={n_manquants_pivots_formula}'
    ws.range((4, 2)).police(gras=True)
    # Compteur recalage dans une cellule séparée (C4), comme B4 pour les
    # pivots : un SUMPRODUCT écrit directement dans Formula1 d'une règle de
    # mise en forme conditionnelle s'évalue en erreur (constaté en direct -
    # fonctionne pourtant comme formule de cellule normale), contrairement à
    # une simple comparaison de cellule (`=$B$4>0`, qui fonctionne).
    ws.range((4, 3)).formula = f'={n_manquants_recalage_formula}'
    ws.range((4, 3)).police(gras=True)
    # Niveau d'alerte maximal et nombre de secteurs fortement recalés (colonne
    # masquée HAR) : cellules séparées pour la même raison.
    ws.range((3, HAR)).formula = f'=MAX({plage(HAR, no_)})'
    ws.range((4, HAR)).formula = f'=COUNTIF({plage(HAR, no_)},">0")'
    # Messages sur deux lignes au besoin (codes facultatifs et non utilisés en
    # complément) : une cellule fusionnée ne s'ajuste pas seule en hauteur.
    ws.range("A4").hauteur_ligne(30)

    # -- Message pivots (rouge), colonnes D à L (sous le bloc ②) --
    cible_piv = ws.range((4, pc), (4, pc + 8))
    cible_piv.fusionner()
    cible_piv.alignement(horizontal="left", vertical="center", wrap=True)
    ws.range((4, pc)).formula = (
        f'=IF($B$4=0,"OK - pivots utilisés tous disponibles.",'
        f'"⚠ "&$B$4&" pivot(s) sans valeur définie pour la sélection, à '
        f'renseigner dans la colonne ""correction manuelle"".")'
        f'&{info_non_utilises(HUP, np_, "pivot(s)", precision_remplacement)}'
    )
    cible_piv.supprimer_mfc()
    cible_piv.mfc('$B$4>0', police=bgr(0x0000C0), gras=True,    # BGR : rouge (identique au bloc ②)
                  fond=bgr(0xCCCCFF))                            # fond rosé

    # -- Message recalage (orange), colonnes N à S (sous le bloc ③) --
    cible_rec = ws.range((4, oc), (4, oc + 5))
    cible_rec.fusionner()
    cible_rec.alignement(horizontal="left", vertical="center", wrap=True)
    ws.range((4, oc)).formula = (
        f'=IF($C$4=0,"OK - données de recalage utilisées toutes disponibles.",'
        f'"⚠ "&$C$4&" donnée(s) de recalage sans valeur définie pour la sélection, à '
        f'renseigner dans la colonne ""correction manuelle"".")'
        f'&{info_facultatifs}&{info_non_utilises(HUR, no_, "code(s)")}'
    )
    cible_rec.supprimer_mfc()
    cible_rec.mfc('$C$4>0', police=bgr(0x0066CC), gras=True,    # BGR : orange (identique au bloc ③)
                  fond=bgr(0x99CCFF))                            # fond orange clair

    # -- Message recalage important, colonnes T à V (sous l'écart pivot) --
    AR3, AR4 = f"${L(HAR)}$3", f"${L(HAR)}$4"
    cible_ecart = ws.range((4, oc + 6), (4, oc + 8))
    cible_ecart.fusionner()
    cible_ecart.alignement(horizontal="left", vertical="center", wrap=True)
    ws.range((4, oc + 6)).formula = (
        f'=IF({AR4}=0,"OK - pas de recalage important.",'
        f'"⚠ "&{AR4}&" secteur(s) fortement recalé(s) : voir « Écart pivot ».")'
    )
    cible_ecart.supprimer_mfc()
    for niveau, fond, police in ((2, 0xCCCCFF, 0x0000C0), (1, 0x99CCFF, 0x0066CC)):
        cible_ecart.mfc(f'{AR3}={niveau}', fond=bgr(fond), police=bgr(police), gras=True)

    print(f"    -> {sheet_name} : {n_epci} EPCI (dont {len(pre_fill)} pré-remplis, "
          f"zone {zone_defaut}) + {np_} pivots + {no_} recalage, "
          f"zones {', '.join(zones)}.")


def _truncate_table(wb, table_name: str, keep: int = 1) -> None:
    """Réduit un tableau à `keep` ligne(s) de données (supprime le reste)."""
    tbl = None
    for sh in wb.sheets:
        for t in sh.tables:
            if t.name == table_name:
                tbl = t
                break
        if tbl is not None:
            break
    if tbl is None:
        print(f"    Tableau « {table_name} » introuvable - ignoré.")
        return
    data = tbl.data_body_range
    if data is None:
        return
    n = data.rows.count
    if n <= keep:
        return
    tbl.supprimer_lignes(range(keep + 1, n + 1))
    print(f"    {table_name} : {n} -> {keep} ligne(s).")


def reset_template(output_path: Path, template_path: Path) -> None:
    """
    Remet le template a zéro : part de l'output et en vide les parties
    dynamiques pour en faire un squelette minimal, écrit à la place du template.

    Conserve :
      - Les définitions de tables (headers + 1 ligne de données)
      - Les formules dans Catalogue_data_EPCI (ligne template)
      - Traj_observée, Traj_planifiée (données de référence)
      - Les named ranges (nom_scenario, Annee_ref, terr_num_epci, terr_zone)
        ainsi que les colonnes Zone et Clé de Traj_supra, réutilisées telles
        quelles au prochain build (le tableau est réduit, pas restructuré)
    """
    if not output_path.exists():
        raise FileNotFoundError(f"Output introuvable : {output_path}")

    wb = _ouvrir(output_path, None)
    sheet_names = [s.name for s in wb.sheets]

    # 1. Vider les onglets entièrement reconstruits par le script
    sheets_to_clear = (
        ["CALCUL", SHEET_PIVOTS, "TOUS SECTEURS"]
        + list(SECTEUR_SHEETS.keys())
    )
    for name in sheets_to_clear:
        if name in sheet_names:
            wb.sheets[name].clear()
            print(f"    Onglet « {name} » vidé.")

    # 2. Réduire les tables à 1 ligne de données
    _truncate_table(wb, "Data_EPCI")
    _truncate_table(wb, "Traj_supra")
    _truncate_table(wb, "Catalogue_data_EPCI")

    _enregistrer(wb, template_path)
    print(f"Template nettoyé : {template_path}")


def _construire_onglets(wb, zones: list[str], maj_traj_supra: bool,
                        build_calcul: bool = True) -> None:
    """Traj_supra puis onglets calculés (Territoire, CALCUL, visualisations)."""
    if maj_traj_supra:
        _update_traj_supra(wb, zones)
    if not build_calcul:
        return
    _build_caract_territoire(wb, SHEET_PIVOTS, zones)
    _build_calcul(wb, zones)
    sheet_names = [s.name for s in wb.sheets]
    op = _scan_obs_plan_rows(wb)
    for sheet_nm, secteur in SECTEUR_SHEETS.items():
        if sheet_nm in sheet_names:
            _build_viz_secteur(wb, sheet_nm, secteur, zones, obs_plan=op)
    if "TOUS SECTEURS" in sheet_names:
        _build_viz_tous_secteurs(wb, "TOUS SECTEURS", zones, obs_plan=op)
    else:
        print("  AVERTISSEMENT : onglet « TOUS SECTEURS » introuvable - visualisation ignorée.")


def run_calcul(traj_path: Path, zones: list[str] | None = None,
               maj_traj_supra: bool = True,
               template_path: Path | None = None) -> None:
    """
    Peuple le tableau `Traj_supra` (valeurs SNBC3 + colonne recalage) puis construit
    l'onglet `CALCUL` et les onglets qui en dépendent, sans toucher à
    l'injection des pivots EPCI.
    """
    zones = _zones_par_defaut(zones)
    wb = _ouvrir(traj_path, template_path)
    _update_meta_ranges(wb)
    _update_mention_version(wb)
    _update_mention_scenario(wb)
    _construire_onglets(wb, zones, maj_traj_supra)
    _enregistrer(wb, traj_path)
    print(f"Terminé. Onglet CALCUL mis à jour : {traj_path}")


def run(
    traj_path: Path,
    wide_path: Path,
    sheet: str = "Data_EPCI",
    maj_traj_supra: bool = True,
    zones: list[str] | None = None,
    build_calcul: bool = True,
    template_path: Path | None = None,
) -> None:
    """
    Injecte le contenu de wide_path (onglet EPCI) dans l'onglet <sheet>
    du classeur traj_path, puis reconstruit les onglets calculés.

    Si template_path est fourni, le classeur part du template et est écrit
    vers traj_path ; sinon traj_path est modifié en place. Il doit être fermé
    dans Excel.
    """
    import pandas as pd

    zones = _zones_par_defaut(zones)

    # ── Lecture de la source ───────────────────────────────────────────────────
    print(f"Lecture de {wide_path} …")
    df: pd.DataFrame = pd.read_excel(wide_path, sheet_name="EPCI", engine="openpyxl")
    print(f"  → {len(df)} lignes × {len(df.columns)} colonnes chargées.")

    wb = _ouvrir(traj_path, template_path)
    sheet_names = [sh.name for sh in wb.sheets]
    if sheet not in sheet_names:
        raise ValueError(
            f"L'onglet « {sheet} » est absent du classeur.\n"
            f"  Onglets disponibles : {sheet_names}"
        )

    ws = wb.sheets[sheet]
    tables = ws.tables
    if tables:
        table = tables[0]
        print(f"  Mise à jour du tableau « {table.name} » (onglet « {sheet} ») …")
        table.update(df)
    else:
        print(f"  Aucun tableau trouvé - écriture directe en A1 (onglet « {sheet} ») …")
        ws.range("A1").value = [list(df.columns)] + df.astype(object).where(
            df.notna(), None).values.tolist()

    _update_Catalogue_data_EPCI(wb, wide_path)
    _update_meta_ranges(wb)
    _update_mention_version(wb)
    _update_mention_scenario(wb)
    _construire_onglets(wb, zones, maj_traj_supra, build_calcul)

    _enregistrer(wb, traj_path)
    print(f"Terminé. Fichier mis à jour : {traj_path}")


def main() -> None:
    from config.settings import GROUPES_ZONES, ZONES_TERRITORIALISEES

    parser = argparse.ArgumentParser(
        description="Met à jour l'onglet Data_EPCI du tableur Trajectoire GES."
    )
    parser.add_argument(
        "traj_path",
        type=Path,
        nargs="?",
        default=None,
        help=(
            "Chemin vers le fichier Trajectoire GES de référence-V2.X.xlsx "
            "(défaut : TRAJ_GES_TARGET_PATH dans config/settings.py)"
        ),
    )
    parser.add_argument(
        "--wide",
        type=Path,
        default=None,
        help="Chemin vers bdd_epci_wide.xlsx (défaut : data/output/bdd_epci_wide.xlsx)",
    )
    parser.add_argument(
        "--sheet",
        default="Data_EPCI",
        help="Nom de l'onglet cible (défaut : Data_EPCI)",
    )
    parser.add_argument(
        "--no-traj-supra",
        dest="maj_traj_supra",
        action="store_false",
        help="Ne pas injecter les valeurs nationales SNBC3 dans le tableau Traj_supra",
    )
    parser.add_argument(
        "--zone",
        dest="zones",
        nargs="+",
        default=None,
        choices=[*ZONES_TERRITORIALISEES, *GROUPES_ZONES],
        help=(
            "Zones portées par le classeur (Traj_supra / CALCUL). DROM désigne "
            "les quatre DROM produits. Défaut : toutes les zones de "
            "config.settings.ZONES_TERRITORIALISEES. Une zone dont la "
            "trajectoire est incalculable est signalée et produit des "
            "résultats nuls pour ses territoires."
        ),
    )
    parser.add_argument(
        "--no-calcul",
        dest="build_calcul",
        action="store_false",
        help="Ne pas (re)construire l'onglet CALCUL",
    )
    parser.add_argument(
        "--only-calcul",
        action="store_true",
        help="Construire uniquement Traj_supra + CALCUL (pas d'injection des pivots EPCI)",
    )
    parser.add_argument(
        "--reset-template",
        action="store_true",
        help="Remplace le template par l'output, parties dynamiques vidées",
    )
    args = parser.parse_args()

    from config.settings import OUTPUT_DIR, TRAJ_GES_TARGET_PATH, TRAJ_GES_TEMPLATE

    if args.reset_template:
        output = (args.traj_path or TRAJ_GES_TARGET_PATH).resolve()
        template = TRAJ_GES_TEMPLATE.resolve()
        try:
            reset_template(output, template)
        except (ValueError, FileNotFoundError) as e:
            print(f"ERREUR : {e}", file=sys.stderr)
            sys.exit(1)
        return

    traj_path = (args.traj_path or TRAJ_GES_TARGET_PATH).resolve()
    template = None if args.traj_path else TRAJ_GES_TEMPLATE.resolve()

    if template is not None:
        if not template.exists():
            print(f"ERREUR : template introuvable : {template}", file=sys.stderr)
            sys.exit(1)
    elif not traj_path.exists():
        print(f"ERREUR : fichier introuvable : {traj_path}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.only_calcul:
            run_calcul(traj_path, args.zones, maj_traj_supra=args.maj_traj_supra,
                       template_path=template)
            return
        wide_path = (args.wide or OUTPUT_DIR / "bdd_epci_wide.xlsx").resolve()
        if not wide_path.exists():
            print(f"ERREUR : bdd_epci_wide.xlsx introuvable : {wide_path}", file=sys.stderr)
            sys.exit(1)
        run(traj_path, wide_path, args.sheet, maj_traj_supra=args.maj_traj_supra,
            zones=args.zones, build_calcul=args.build_calcul, template_path=template)
    except ValueError as e:
        print(f"ERREUR : {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
