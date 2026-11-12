"""
pipeline/territorialize.py
==========================
Territorialisation de la trajectoire nationale SNBC3 sur les EPCI.

Applique, pour chaque EPCI / sous-secteur / année, la valeur nationale au pivot
EPCI (part de la zone), avec corrections démographique (évolutive) et DJU
(statique, chaud/froid), puis recalage sur les données observées (énergie-climat,
ALDO) en année de référence.

Deux jeux de résultats :
  valeur_brute  = national x pivot x corrections
  valeur_recalee = valeur_brute x facteur de recalage (par secteur)

Entrées :
  - BDD long (pipeline.aggregate.build_bdd) : valeurs des pivots, DJU,
    POP_PROJ_RATIO et données de recalage par EPCI + lignes agrégées de zone
    (HEXAGONE / DROM) produites par compute_totaux.
  - config/traj_supra.yaml : correspondance sous-secteur <-> pivot <-> corrections
    <-> recalage (source de vérité).
  - data/input/SNBC3 retraitée.xlsx : valeurs nationales 2020->2050 et parts
    des DROM dans les bilans énergie (classeur publié, scripts/publier_snbc3.py).

EPT et Paris (niveau_geo EPT/COMMUNE, pipeline.aggregate) : territorialisés
comme n'importe quel EPCI (mêmes pivots, mêmes corrections, même recalage -
qui dégrade proprement vers `QUALITE_NON_RECALE_ABSENT` puisqu'aucune donnée
OREC/IGT n'existe à cette maille), à une exception près : ils sont exclus de
la somme de conservation nationale (`ecart_conservation`) pour ne jamais
compter deux fois la contribution de leur EPCI parent, la Métropole du Grand
Paris. Leur couverture de pivots est partielle (agriculture, transport,
industrie, emplois - pas de résidentiel/tertiaire/DJU/Omphale/énergie-climat) :
un sous-secteur dont le pivot n'existe pas à leur maille n'apparaît
simplement pas dans leurs lignes de sortie (pas de ligne NaN taguée
`pivot_absent` - cette dégradation existe pour d'autres cas, pas celui-ci).

Cf. la formule dans le rapport méthodologique (docs/, §3.1) et l'onglet CALCUL
du classeur (standalone_tool/README.md).
"""

import logging

import numpy as np
import pandas as pd
import yaml

from config.settings import (
    ANNEE_REF,
    DJU_VARIANTS,
    GROUPES_ZONES,
    ROOT_DIR,
    TERR_DJU_VARIANT,
    TRAJ_NATIONALE,
    TERR_ZONE_DEFAUT,
    ZONES_TERRITORIALISEES,
    groupe_de_zone,
)

log = logging.getLogger(__name__)

# La ligne agrégée d'une zone (ou d'un groupe) dans la BDD long porte son nom
# comme `code_epci` (cf. pipeline.aggregate.compute_totaux).

UNITES = {"GES": "MtCO2e", "conso": "TWh"}

# Colonnes du DataFrame de sortie de territorialize(), dans leur ordre final.
# Source de vérité pour scripts/generate_territorialize_doc.py (docs/Territorialisation.xlsx) :
# n'importe pas cette liste en dur, l'importe d'ici.
TERRITORIALISE_COLUMNS = [
    "code_epci", "nom_epci", "niveau_geo", "zone", "code_region", "code_departement",
    "code_traj_supra", "type", "secteur", "usage", "code_pivot", "unite", "annee",
    "valeur_brute", "valeur_recalee", "code_recalage", "recal_factor",
    "ecart_conservation", "qualite",
]

# Valeurs possibles de la colonne `qualite`, avec leur signification. Même
# source de vérité que ci-dessus pour la doc générée.
QUALITE_OK = "ok"
QUALITE_PASS_THROUGH = "pass_through_sans_recalage"
QUALITE_PIVOT_ABSENT = "pivot_absent"
QUALITE_NON_RECALE_ABSENT = "non_recale_absent"
QUALITE_NON_RECALE_SECTEUR_NUL = "non_recale_secteur_nul"

QUALITE_LEGENDE = {
    QUALITE_OK: "Recalage appliqué normalement (observé disponible, secteur non nul en année de référence)",
    QUALITE_PASS_THROUGH: "Pas de code_recalage défini pour ce sous-secteur : valeur_brute reprise telle quelle",
    QUALITE_PIVOT_ABSENT: "Aucun pivot disponible pour répartir la trajectoire entre EPCI : résultat indéterminé",
    QUALITE_NON_RECALE_ABSENT: "Donnée observée manquante pour le recalage : recal_factor laissé à 1",
    QUALITE_NON_RECALE_SECTEUR_NUL: "Somme brute nulle en année de référence pour ce (EPCI, secteur) : recal_factor laissé à 1",
}


def _recalage_unit_factor(unite: str) -> float:
    """
    Facteur de conversion d'une unité de recalage vers l'unité nationale.

    Energie-climat : ktCO2e/an (GES) et GWh (conso) -> x1e-3.
    ALDO : tCO2e/an -> x1e-6.
    """
    u = (unite or "").strip()
    if u.startswith("tCO2e"):
        return 1e-6
    if u.startswith("kt") or u == "GWh":
        return 1e-3
    if u.startswith("Mt") or u == "TWh" or u == "":
        return 1.0
    log.warning(f"Unite non reconnue pour le recalage : '{unite}' -> facteur 1")
    return 1.0


# ---------------------------------------------------------------------------
# Chargement de la correspondance (config/traj_supra.yaml)
# ---------------------------------------------------------------------------

def _load_yaml() -> dict:
    with open(ROOT_DIR / "config" / "traj_supra.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _pour_zone(blocs: dict | None, zone: str) -> dict:
    """
    Réglages applicables à une zone dans un dictionnaire indexé par zone ou
    groupe de zones : ceux de son groupe (config.settings.GROUPES_ZONES), puis
    ceux de la zone elle-même, qui priment clé par clé.
    """
    blocs = blocs or {}
    out = dict(blocs.get(groupe_de_zone(zone)) or {})
    out.update(blocs.get(zone) or {})
    return out


def load_zone_config(zone: str = TERR_ZONE_DEFAUT) -> dict:
    """
    Réglages valant pour toute une zone (bloc `zones` de traj_supra.yaml) :
    `corrections_dju`, `secteurs_desactives`, `trajectoire_derivee`... Les
    réglages déclarés pour un groupe (DROM) valent pour chacune de ses zones.
    Zone absente = {} (comportement de l'hexagone).
    """
    return _pour_zone(_load_yaml().get("zones"), zone)


def load_correspondance(zone: str = TERR_ZONE_DEFAUT) -> pd.DataFrame:
    """
    Charge la correspondance des sous-secteurs depuis config/traj_supra.yaml.

    Applique, dans cet ordre :
      1. les réglages de zone (`zones` au niveau racine) : retrait des secteurs
         désactivés puis extinction des corrections DJU ;
      2. les surcharges par sous-secteur (clé `zones` du sous-secteur) :
         `code_pivot` alternatif ou désactivation (`actif: false`).

    Une correction DJU éteinte au niveau de la zone l'est pour tous les
    sous-secteurs : le bloc de zone peut désactiver, jamais activer.

    Retourne un DataFrame indexé par défaut, une ligne par sous-secteur actif.
    """
    data = _load_yaml()
    zone_cfg = _pour_zone(data.get("zones"), zone)
    secteurs_off = set(zone_cfg.get("secteurs_desactives") or [])
    dju_off = {
        sens for sens, actif in (zone_cfg.get("corrections_dju") or {}).items()
        if actif is False
    }

    rows = []
    n_secteur_off = 0
    for ss in data["sous_secteurs"]:
        ss = dict(ss)
        override = _pour_zone(ss.pop("zones", {}), zone)
        if ss.get("secteur") in secteurs_off:
            n_secteur_off += 1
            continue
        if override.get("actif") is False:
            continue
        ss.update({k: v for k, v in override.items() if k != "actif"})
        for sens in dju_off:
            ss[f"correction_dju_{sens}"] = False
        ss["unite"] = UNITES.get(ss["type"], "")
        rows.append(ss)

    df = pd.DataFrame(rows)
    log.info(f"Correspondance traj_supra : {len(df)} sous-secteurs actifs (zone {zone})")
    if secteurs_off:
        log.info(
            f"  zone {zone} : secteurs désactivés {sorted(secteurs_off)} "
            f"({n_secteur_off} sous-secteurs retirés)"
        )
    if dju_off:
        log.info(f"  zone {zone} : corrections DJU désactivées {sorted(dju_off)}")
    return df


# Écart toléré entre 1 et Σ des parts des zones d'un groupe, par secteur et
# par année. scripts/publier_snbc3.py contrôle en amont, à la même tolérance,
# que Σ des bilans des zones = bilan du groupe (onglets d'un même envoi DGEC).
PARTS_TOLERANCE_SOMME = 1e-3


def parts_bilans(groupe: str, path=None) -> pd.DataFrame:
    """
    Part de chaque zone d'un groupe dans la conso finale de chaque secteur du
    groupe, par année des bilans énergie DGEC (colonne « Total », tous
    vecteurs).

    Lue dans l'onglet `onglet_parts` du bloc `trajectoire_derivee` du groupe
    (config/traj_supra.yaml) : une ligne par (zone, secteur), une colonne par
    année. scripts/publier_snbc3.py le calcule depuis les onglets de bilans du
    classeur de travail : rien à mettre à jour à la main d'une livraison SNBC
    à l'autre.

    Contrôles : chaque zone du groupe et chaque secteur de `secteurs`
    présents, Σ des zones = 1 à `PARTS_TOLERANCE_SOMME` près pour chaque
    secteur et chaque année. Aucune valeur de repli : un onglet manquant lève
    une erreur. Se rabattre silencieusement sur l'absence de calage
    produirait des résultats faux sans le signaler.

    Retourne un DataFrame long (zone, secteur, annee, part), alias de secteur
    développés.
    """
    import openpyxl

    source = load_zone_config(groupe).get("trajectoire_derivee") or {}
    onglet = source["onglet_parts"]
    path = path or TRAJ_NATIONALE.chemin
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if onglet not in wb.sheetnames:
        dispo = wb.sheetnames
        wb.close()
        raise RuntimeError(
            f"Onglet « {onglet} » absent de {path.name} : impossible de dériver "
            f"la trajectoire des zones du groupe {groupe}. Il porte la part de "
            f"chaque zone dans les bilans énergie par secteur (cf. "
            f"`trajectoire_derivee` dans config/traj_supra.yaml, généré par "
            f"scripts/publier_snbc3.py). Onglets présents : {dispo}"
        )
    rows = wb[onglet].iter_rows(values_only=True)
    header = next(rows)
    year_cols = {
        i: int(v) for i, v in enumerate(header)
        if isinstance(v, (int, float)) and 2000 <= int(v) <= 2100
    }
    records = [
        (str(r[0]).strip(), str(r[1]).strip(), annee, float(r[i]))
        for r in rows if r and r[0] is not None
        for i, annee in year_cols.items() if i < len(r) and r[i] is not None
    ]
    wb.close()
    df = pd.DataFrame(records, columns=["zone", "secteur", "annee", "part"])

    manquants = (
        [f"zone {z}" for z in GROUPES_ZONES[groupe] if z not in set(df["zone"])]
        + [f"secteur {s}" for s in (source.get("secteurs") or {}) if s not in set(df["secteur"])]
    )
    if manquants:
        raise RuntimeError(f"« {onglet} » ({path.name}) : {manquants} absent(s).")
    sommes = df.groupby(["secteur", "annee"])["part"].sum()
    ecarts = sommes[(sommes - 1.0).abs() > PARTS_TOLERANCE_SOMME]
    if not ecarts.empty:
        raise RuntimeError(
            f"« {onglet} » ({path.name}) : Σ des parts des zones ≠ 1 pour "
            f"{ecarts.round(4).to_dict()}."
        )
    for alias, ref in (source.get("alias") or {}).items():
        df = pd.concat([df, df[df["secteur"] == ref].assign(secteur=alias)],
                       ignore_index=True)
    return df


def _interpoler_parts(parts: pd.DataFrame, annees) -> pd.DataFrame:
    """
    Parts (zone, secteur, annee, part) aux années demandées : interpolation
    linéaire entre les années des bilans, valeur de l'année la plus proche
    hors de leur plage.
    """
    annees = sorted(set(int(a) for a in annees))
    out = []
    for (zone, secteur), g in parts.groupby(["zone", "secteur"]):
        g = g.sort_values("annee")
        v = np.interp(annees, g["annee"].to_numpy(float), g["part"].to_numpy(float))
        out.append(pd.DataFrame({"zone": zone, "secteur": secteur,
                                 "annee": annees, "part": v}))
    return pd.concat(out, ignore_index=True)


def load_correspondance_union(zones: list[str]) -> pd.DataFrame:
    """
    Union des sous-secteurs actifs sur plusieurs zones, pour les constructions
    qui doivent couvrir toutes les zones d'un même classeur (onglet CALCUL et
    onglets de visualisation du tableur standalone).

    Règles de fusion :
      - présence : union (un sous-secteur désactivé sur une zone reste présent
        s'il est actif sur une autre ; côté tableur, sa valeur nationale sera
        introuvable pour cette zone et vaudra 0) ;
      - `correction_dju_*`, `influence_demographie` : OU logique, de sorte que
        l'étape de calcul existe dès qu'une zone l'applique. La valeur effective
        est résolue par formule à partir des métadonnées de la zone retenue ;
      - `code_pivot` et les autres champs : valeur de la première zone qui porte
        le sous-secteur. Les variantes par zone sont résolues dynamiquement dans
        le tableur, jamais figées ici.

    L'ordre des sous-secteurs suit celui de la première zone, puis les ajouts
    des zones suivantes.
    """
    FLAGS = ("correction_dju_chaud", "correction_dju_froid", "influence_demographie")

    fusion: dict[str, dict] = {}
    for zone in zones:
        for row in load_correspondance(zone).to_dict("records"):
            code = row["code_traj_supra"]
            if code not in fusion:
                fusion[code] = row
                continue
            for f in FLAGS:
                fusion[code][f] = bool(fusion[code].get(f)) or bool(row.get(f))

    df = pd.DataFrame(list(fusion.values()))
    log.info(
        f"Correspondance traj_supra (union {', '.join(zones)}) : "
        f"{len(df)} sous-secteurs"
    )
    return df


# ---------------------------------------------------------------------------
# Chargement de la trajectoire nationale (SNBC3 retraitée.xlsx)
# ---------------------------------------------------------------------------

def load_national_traj(
    codes_attendus: set[str],
    zone: str = TERR_ZONE_DEFAUT,
    path=None,
) -> pd.DataFrame:
    """
    Lit la trajectoire nationale depuis l'onglet SNBC3 retraité de la zone.

    Code en colonne D (`Code traj supra`), années en en-tête (2020→2050).
    Filtre aux `codes_attendus`, nettoie les codes (trim) et valide la couverture.

    Retourne un DataFrame long (code_traj_supra, annee, valeur_nationale).
    """
    import openpyxl

    path = path or TRAJ_NATIONALE.chemin
    sheet = TRAJ_NATIONALE.onglets.get(zone)
    if sheet is None:
        raise ValueError(
            f"Aucun onglet de trajectoire nationale configuré pour la zone {zone} "
            f"(cf. config.settings.SNBC3_SHEETS, option --onglet de main.py)"
        )

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if sheet not in wb.sheetnames:
        raise RuntimeError(
            f"Onglet '{sheet}' absent de {path.name} : la trajectoire retraitée "
            f"de la zone {zone} n'est pas encore produite (retraitement SECTEN "
            f"-> PCAET -> pivots à faire dans le classeur).\n"
            f"Onglets présents : {wb.sheetnames}"
        )
    ws = wb[sheet]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)

    # Colonnes-années : en-têtes entiers dans la plage 2000-2100
    year_cols = {
        i: int(v) for i, v in enumerate(header)
        if isinstance(v, (int, float)) and 2000 <= int(v) <= 2100
    }
    if not year_cols:
        raise RuntimeError(f"Aucune colonne-année détectée dans l'onglet {sheet}")

    CODE_COL = 3  # colonne D
    records = []
    seen = set()
    for r in rows:
        code = r[CODE_COL]
        if code is None:
            continue
        code = str(code).strip()
        if code not in codes_attendus or code in seen:
            continue
        seen.add(code)
        for i, annee in year_cols.items():
            val = r[i] if i < len(r) else None
            if val is None:
                continue
            records.append((code, annee, float(val)))

    df = pd.DataFrame(records, columns=["code_traj_supra", "annee", "valeur_nationale"])

    manquants = codes_attendus - seen
    if manquants:
        raise RuntimeError(
            f"Codes traj_supra absents de '{sheet}' ({path.name}) : {sorted(manquants)}"
        )
    log.info(
        f"Trajectoire nationale ({sheet}) : {len(seen)} codes, "
        f"années {df['annee'].min()}–{df['annee'].max()}"
    )
    return df


# ---------------------------------------------------------------------------
# Trajectoire de zone (zones d'un groupe : dérivée de celle du groupe)
# ---------------------------------------------------------------------------

def _valeur_totale(df_totaux: pd.DataFrame, indicateur: str, code: str, ref_year: int):
    """
    Valeur de la ligne agrégée `code` (zone ou groupe) pour un indicateur, à
    la même année que les ratios de pivot (cf. `_value_at_ref`). None si la
    ligne est absente ou vide.
    """
    df_ind = df_totaux[df_totaux["code_indicateur"] == indicateur]
    if df_ind.empty:
        return None
    v = _value_at_ref(df_ind, ref_year)
    v = v.loc[v["code_epci"] == code, "valeur"]
    return None if v.empty or pd.isna(v.iloc[0]) else float(v.iloc[0])


def _derive_demographique(
    df_totaux: pd.DataFrame, zone: str, groupe: str, ref_year: int,
) -> pd.Series:
    """
    Dérive démographique de la zone par rapport à son groupe, par année :
    [PPR(zone,t)/PPR(zone,ref)] / [PPR(groupe,t)/PPR(groupe,ref)], avec PPR =
    POP_PROJ_RATIO. Vaut 1 en année de référence.

    Portée par la trajectoire de zone, elle restitue ce que le facteur
    démographique par EPCI (normalisé sur la zone) ne voit plus : une zone
    qui croît plus vite que le groupe voit sa trajectoire croître d'autant.
    """
    ppr = df_totaux[
        (df_totaux["code_indicateur"] == "POP_PROJ_RATIO")
        & df_totaux["code_epci"].isin({zone, groupe})
    ].pivot_table(index="annee", columns="code_epci", values="valeur")
    if zone not in ppr or groupe not in ppr or ref_year not in ppr.index:
        log.warning(f"POP_PROJ_RATIO absent pour {zone}/{groupe} : dérive démographique = 1")
        return pd.Series(dtype=float)
    norm = ppr / ppr.loc[ref_year]
    return (norm[zone] / norm[groupe]).rename("derive_demo")


def trajectoire_zone(
    codes_attendus: set[str],
    zone: str,
    df_totaux: pd.DataFrame,
    ref_year: int = ANNEE_REF,
    path=None,
) -> pd.DataFrame:
    """
    Trajectoire (code_traj_supra, annee, valeur_nationale) à territorialiser
    sur une zone.

    Zone portant son propre onglet SNBC3 (HEXAGONE) : lecture directe.

    Zone d'un groupe (chaque DROM) : dérivée de la trajectoire du groupe,
    seule retraitée dans le classeur. Pour chaque sous-secteur `ss` :

        N_zone(ss,t) = N_groupe(ss,t) × P(ss) × D(t) × k(secteur,t)

      - P : part de la zone dans le pivot du sous-secteur, en année de
        référence = valeur(zone) / valeur(groupe). Si le pivot ne couvre
        aucune zone non territorialisée du groupe (Mayotte, absente de MEN,
        EMP_TER, EMP_IND_*...), le groupe ne compte pas sa part : P est alors
        multiplié par (1 - part de ces zones dans la conso du secteur), prise
        dans les bilans à `annee_part_hors_perimetre`. Sans cela, les zones
        produites se partageraient 100 % d'une trajectoire qui l'inclut.
      - D : dérive démographique de la zone par rapport au groupe (sous-
        secteurs `influence_demographie`), cf. `_derive_demographique`.
      - k : calage de la conso (types `types_cales`, secteurs déclarés sous
        `secteurs`) sur les bilans DGEC par territoire. Pour chaque secteur,
        Σ_ss N_zone = N_groupe(secteur) × part de la zone dans le bilan,
        interpolée entre les années des bilans. Vaut 1 ailleurs.

    Sans k, N_zone × ratio de pivot dans la zone reproduit exactement la
    territorialisation sur le groupe entier : la branche GES, recalée sur
    l'IGT, n'est pas affectée par le découpage en zones. k corrige la
    branche conso, où le pivot unique supposait une conso par ménage ou par
    emploi homogène entre territoires (cf. docs/territorialisation_drom.md).

    `df_totaux` : BDD long (ou ses seules lignes agrégées), dont les lignes de
    la zone et de son groupe, pour les pivots et POP_PROJ_RATIO.
    """
    if zone in GROUPES_ZONES:
        raise ValueError(
            f"{zone} est un groupe de zones, pas une zone : territorialiser "
            f"chacune de ses zones ({', '.join(GROUPES_ZONES[zone])})."
        )
    if zone in TRAJ_NATIONALE.onglets:
        return load_national_traj(codes_attendus, zone, path)

    groupe = groupe_de_zone(zone)
    source = load_zone_config(zone).get("trajectoire_derivee") or {}
    if groupe is None or groupe not in TRAJ_NATIONALE.onglets or not source:
        raise ValueError(
            f"Zone {zone} : ni onglet SNBC3 propre, ni `trajectoire_derivee` "
            f"déclarée pour son groupe dans config/traj_supra.yaml."
        )

    traj = load_national_traj(codes_attendus, groupe, path).merge(
        load_correspondance(zone)[
            ["code_traj_supra", "type", "secteur", "code_pivot", "influence_demographie"]
        ],
        on="code_traj_supra", how="left",
    )
    parts = parts_bilans(groupe, path)

    # P : part de la zone dans chaque pivot, corrigée des zones hors périmètre
    hors_perimetre = [z for z in GROUPES_ZONES[groupe] if z not in ZONES_TERRITORIALISEES]
    annee_hp = source.get("annee_part_hors_perimetre")
    part_hp = (
        parts[parts["zone"].isin(hors_perimetre) & (parts["annee"] == annee_hp)]
        .groupby("secteur")["part"].sum().to_dict()
    )
    part_pivot = {}
    for ss in traj.drop_duplicates("code_traj_supra").itertuples():
        v_zone = _valeur_totale(df_totaux, ss.code_pivot, zone, ref_year)
        v_groupe = _valeur_totale(df_totaux, ss.code_pivot, groupe, ref_year)
        if v_zone is None or not v_groupe:
            log.warning(f"Zone {zone} : pivot {ss.code_pivot} indisponible pour {ss.code_traj_supra}")
            continue
        p = v_zone / v_groupe
        hp_couverts = any(
            _valeur_totale(df_totaux, ss.code_pivot, z, ref_year) is not None
            for z in hors_perimetre
        )
        if hors_perimetre and not hp_couverts:
            p *= 1.0 - part_hp.get(ss.secteur, 0.0)
        part_pivot[ss.code_traj_supra] = p
    traj["part_pivot"] = traj["code_traj_supra"].map(part_pivot)

    # D : dérive démographique de la zone par rapport au groupe
    derive = _derive_demographique(df_totaux, zone, groupe, ref_year)
    traj["derive_demo"] = np.where(
        traj["influence_demographie"].fillna(False).astype(bool),
        traj["annee"].map(derive).fillna(1.0), 1.0,
    )
    traj["valeur_zone"] = traj["valeur_nationale"] * traj["part_pivot"] * traj["derive_demo"]

    # k : calage de la conso sur les bilans par territoire
    types_cales = set(source.get("types_cales") or [])
    a_caler = traj["type"].isin(types_cales) & traj["secteur"].isin(set(parts["secteur"]))
    if a_caler.any():
        cibles = traj[a_caler].groupby(["secteur", "annee"]).agg(
            n_groupe=("valeur_nationale", "sum"), n_zone=("valeur_zone", "sum"),
        ).reset_index().merge(
            _interpoler_parts(parts[parts["zone"] == zone], traj["annee"].unique())
            .drop(columns="zone"),
            on=["secteur", "annee"], how="left",
        )
        sans_base = cibles[(cibles["n_zone"] == 0) & (cibles["n_groupe"] * cibles["part"] != 0)]
        if not sans_base.empty:
            log.warning(
                f"Zone {zone} : trajectoire nulle avant calage mais cible non nulle "
                f"pour {sorted(sans_base['secteur'].unique())} - calage impossible"
            )
        cibles["k"] = (
            cibles["n_groupe"] * cibles["part"] / cibles["n_zone"].replace(0, np.nan)
        ).fillna(1.0)
        traj = traj.merge(cibles[["secteur", "annee", "k"]], on=["secteur", "annee"], how="left")
        traj["k"] = np.where(a_caler, traj["k"].fillna(1.0), 1.0)
        traj["valeur_zone"] *= traj["k"]

    n_cales = traj.loc[a_caler, "code_traj_supra"].nunique()
    traj = traj.dropna(subset=["valeur_zone"])
    log.info(
        f"Trajectoire {zone} dérivée de {groupe} : "
        f"{traj['code_traj_supra'].nunique()} codes, dont {n_cales} calés sur les bilans"
    )
    return (
        traj[["code_traj_supra", "annee", "valeur_zone"]]
        .rename(columns={"valeur_zone": "valeur_nationale"})
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# Pivots : ratio EPCI / total de zone
# ---------------------------------------------------------------------------

def epci_de_zone(df_long: pd.DataFrame, zone: str) -> set[str]:
    """EPCI d'une zone (hors lignes agrégées, qui n'ont pas de zone)."""
    return set(df_long.loc[df_long["zone"] == zone, "code_epci"].unique())


def _value_at_ref(df_ind: pd.DataFrame, ref_year: int) -> pd.DataFrame:
    """
    Pour un indicateur donné, retourne une valeur par code_epci à l'année de
    référence si disponible, sinon à l'année la plus récente disponible.
    """
    annees = set(df_ind["annee"].unique())
    yr = ref_year if ref_year in annees else max(annees)
    return df_ind[df_ind["annee"] == yr][["code_epci", "valeur"]]


def compute_pivot_ratios(
    df_long: pd.DataFrame,
    pivots: set[str],
    zone: str,
    ref_year: int = ANNEE_REF,
    epci_zone: set[str] | None = None,
) -> pd.DataFrame:
    """
    Calcule pivot_ratio(e, p) = valeur(e, p) / valeur(total_zone, p) en année
    de référence, pour chaque pivot `p` et chaque EPCI de la zone.

    `epci_zone` restreint les EPCI produits (défaut : tous ceux de la zone).
    Le dénominateur reste la ligne agrégée de zone dans tous les cas.

    Retourne un DataFrame long (code_epci, code_pivot, pivot_ratio, abs_weight).
    abs_weight = |valeur(e)| / Σ|valeur(zone)| : poids pour le mode additif.
    """
    total_row = zone
    if epci_zone is None:
        epci_zone = epci_de_zone(df_long, zone)

    # Somme des poids additifs sur la seule partition (EPCI) : les EPT et
    # Paris, imbriqués dans la MGP, y doublonneraient sa contribution et les
    # poids des EPCI ne sommeraient plus à 1 (delta de zone incomplètement
    # réparti). Même règle que `NIVEAU_GEO_PARTITION` dans `territorialize`.
    if "niveau_geo" in df_long.columns:
        partition = set(df_long.loc[df_long["niveau_geo"] == "EPCI", "code_epci"])
    else:
        partition = None

    out = []
    for pivot in sorted(pivots):
        df_ind = df_long[df_long["code_indicateur"] == pivot]
        if df_ind.empty:
            log.warning(f"Pivot {pivot} absent de la BDD : ratios non calculés")
            continue
        vals = _value_at_ref(df_ind, ref_year)

        denom_rows = vals.loc[vals["code_epci"] == total_row, "valeur"]
        if denom_rows.empty or pd.isna(denom_rows.iloc[0]):
            log.warning(f"Total de zone {total_row} indisponible pour {pivot}")
            continue
        denom = float(denom_rows.iloc[0])

        v = vals[vals["code_epci"].isin(epci_zone)].copy()
        if denom == 0:
            # Total nul : la zone n'a pas l'activité (branche industrielle
            # absente d'un DROM, par exemple). Ses EPCI en reçoivent 0, comme
            # avec le dénominateur d'un périmètre plus large, plutôt que de
            # disparaître du résultat. Un total nul fait de valeurs non nulles
            # (compensations de signe) reste indéterminé.
            if not (v["valeur"].fillna(0) == 0).all():
                log.warning(f"Total de zone {total_row} nul pour {pivot}, valeurs EPCI non nulles")
                continue
            denom = np.inf
        v["code_pivot"] = pivot
        v["pivot_ratio"] = v["valeur"] / denom
        v_part = v if partition is None else v[v["code_epci"].isin(partition)]
        abs_sum = v_part["valeur"].abs().sum()
        v["abs_weight"] = v["valeur"].abs() / abs_sum if abs_sum else 0.0
        out.append(v[["code_epci", "code_pivot", "pivot_ratio", "abs_weight"]])

    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(
        columns=["code_epci", "code_pivot", "pivot_ratio", "abs_weight"]
    )


# ---------------------------------------------------------------------------
# Facteurs correctifs : DJU (statique) et démographie (évolutif)
# ---------------------------------------------------------------------------

def compute_dju_factors(
    df_long: pd.DataFrame,
    zone: str,
    dju_variant: str = TERR_DJU_VARIANT,
    ref_year: int = ANNEE_REF,
    epci_zone: set[str] | None = None,
) -> pd.DataFrame:
    """
    Facteurs DJU chaud/froid = DJU(territoire) / DJU(moyenne de zone),
    statiques dans le temps. Retourne (code_epci, corr_dju_chaud, corr_dju_froid).
    """
    if dju_variant not in DJU_VARIANTS:
        raise ValueError(f"variante de DJU inconnue (attendu : {', '.join(DJU_VARIANTS)})")
    total_row = zone
    if epci_zone is None:
        epci_zone = epci_de_zone(df_long, zone)
    res = {}
    for sens, col in (("chaud", "corr_dju_chaud"), ("froid", "corr_dju_froid")):
        ind = f"DJU_{sens.upper()}_{dju_variant}"
        df_ind = df_long[(df_long["code_indicateur"] == ind) & (df_long["annee"] == ref_year)]
        if df_ind.empty:
            log.warning(f"DJU {ind} absent : facteur {col} = 1")
            continue
        denom_rows = df_ind.loc[df_ind["code_epci"] == total_row, "valeur"]
        if denom_rows.empty or not denom_rows.iloc[0]:
            log.warning(f"Moyenne de zone {ind} indisponible : facteur {col} = 1")
            continue
        denom = float(denom_rows.iloc[0])
        s = df_ind[df_ind["code_epci"].isin(epci_zone)].set_index("code_epci")["valeur"] / denom
        res[col] = s

    df = pd.DataFrame(res)
    df.index.name = "code_epci"
    return df.reset_index()


def compute_demo_factors(
    df_long: pd.DataFrame,
    zone: str,
    ref_year: int = ANNEE_REF,
    epci_zone: set[str] | None = None,
) -> pd.DataFrame:
    """
    Facteur démographique évolutif :
      demo_factor(e,t) = [PPR(e,t)/PPR(e,ref)] / [PPR(nat,t)/PPR(nat,ref)]
    avec PPR = POP_PROJ_RATIO (par EPCI + total de zone). Vaut 1 en année de réf.

    Retourne un DataFrame long (code_epci, annee, demo_factor).
    """
    total_row = zone
    ppr = df_long[df_long["code_indicateur"] == "POP_PROJ_RATIO"][
        ["code_epci", "annee", "valeur"]
    ].copy()
    if ppr.empty:
        log.warning("POP_PROJ_RATIO absent : facteur démographique = 1")
        return pd.DataFrame(columns=["code_epci", "annee", "demo_factor"])

    # Normalisation par l'année de référence de chaque entité
    ref_vals = ppr[ppr["annee"] == ref_year].set_index("code_epci")["valeur"]
    ppr["ppr_norm"] = ppr["valeur"] / ppr["code_epci"].map(ref_vals)

    nat = ppr[ppr["code_epci"] == total_row].set_index("annee")["ppr_norm"]
    if epci_zone is None:
        epci_zone = epci_de_zone(df_long, zone)

    df = ppr[ppr["code_epci"].isin(epci_zone)].copy()
    df["nat_norm"] = df["annee"].map(nat)
    df["demo_factor"] = df["ppr_norm"] / df["nat_norm"]
    return df[["code_epci", "annee", "demo_factor"]]


# ---------------------------------------------------------------------------
# Recalage
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Territorialisation complète
# ---------------------------------------------------------------------------

def territorialize(
    df_long: pd.DataFrame,
    zone: str = TERR_ZONE_DEFAUT,
    ref_year: int = ANNEE_REF,
    dju_variant: str = TERR_DJU_VARIANT,
    epci_unique: str | None = None,
) -> pd.DataFrame:
    """
    Produit les résultats territorialisés (brut + recalé) au format long.

    Colonnes de sortie :
      code_epci, nom_epci, niveau_geo, zone, code_region, code_departement,
      code_traj_supra, type, secteur, usage, code_pivot, unite, annee,
      valeur_brute, valeur_recalee, code_recalage, recal_factor,
      ecart_conservation, qualite

    `epci_unique` : optimisation pour un appel à la demande par territoire
    (cf. api/), sans effet sur les valeurs produites - seulement sur leur
    périmètre. Le ratio pivot a structurellement besoin de la zone entière
    (dénominateur), mais une fois ce ratio connu, tout le reste (jointure
    trajectoire nationale, corrections, recalage, tri) ne dépend plus que du
    territoire demandé : les ~2,5M lignes de la zone entière n'ont pas besoin
    d'être matérialisées pour n'en garder qu'une poignée à la fin (facteur
    ~10 sur le temps de calcul, mesuré sur HEXAGONE). Sans effet sur le mode
    batch (défaut `None` : comportement, y compris de performance, inchangé).
    `ecart_conservation` n'est pas calculable sur un seul territoire et vaut
    `NaN` dans ce chemin rapide (diagnostic zone entière disponible via le
    calcul batch complet).

    Le facteur de recalage est constant dans le temps et les résultats bruts
    ne sont pas renormalisés sur la trajectoire de zone : un profil convergeant
    vers 1 et une renormalisation Σ EPCI = zone ont été explorés puis
    abandonnés (cf. rapport méthodologique V2, docs/).
    """
    # Chemin rapide "un seul territoire" : les étapes après le calcul des
    # ratios pivot (jointure cartésienne, recalage, tri) ne portent que sur
    # ce territoire, jamais sur la zone entière - cf. docstring.
    filtrer_un_territoire = epci_unique is not None
    # Les lignes EPT et COMMUNE (Paris - pipeline.aggregate) sont des
    # subdivisions imbriquées dans un seul EPCI (la Métropole du Grand Paris).
    # Elles reçoivent, comme n'importe quel EPCI, une trajectoire calculée là
    # où leurs pivots existent (cf. README, notes techniques : couverture partielle -
    # agriculture/transport/industrie/emplois seulement) - mais ne doivent
    # JAMAIS entrer dans Σ(valeur_brute) du contrôle de conservation
    # nationale (`ecart_conservation`) : les compter à côté de la MGP y
    # doublonnerait sa contribution. `NIVEAU_GEO_PARTITION` isole la vraie
    # partition du territoire pour ce seul usage, plus bas.
    NIVEAU_GEO_PARTITION = {"EPCI", "TOTAL"}

    corr = load_correspondance(zone)
    # Trajectoire de la zone : lue dans son onglet (HEXAGONE) ou dérivée de
    # celle de son groupe (chaque DROM), cf. `trajectoire_zone`.
    national = trajectoire_zone(set(corr["code_traj_supra"]), zone, df_long, ref_year)

    epci_zone = epci_de_zone(df_long, zone)

    # Sous-ensemble "vraie partition" (EPCI, pas EPT/Paris) pour le
    # dénominateur de conservation - cf. commentaire d'en-tête.
    if "niveau_geo" in df_long.columns:
        epci_partition_tous = set(
            df_long.loc[df_long["niveau_geo"].isin(NIVEAU_GEO_PARTITION), "code_epci"]
        )
        epci_zone_partition = epci_zone & epci_partition_tous
    else:
        epci_zone_partition = epci_zone

    # 1) Trajectoire de zone enrichie de la correspondance (57 × n_années)
    traj = national.merge(corr, on="code_traj_supra", how="left")

    # 2) Pivots et corrections (zone entière : le ratio a besoin du total de
    # zone au dénominateur, cf. compute_pivot_ratios)
    ratios = compute_pivot_ratios(df_long, set(corr["code_pivot"]), zone, ref_year, epci_zone)
    dju_f = compute_dju_factors(df_long, zone, dju_variant, ref_year, epci_zone)
    demo_f = compute_demo_factors(df_long, zone, ref_year, epci_zone)

    # Chemin rapide : le ratio pivot est connu, tout le reste ne dépend plus
    # que du territoire demandé - cf. docstring.
    if filtrer_un_territoire:
        ratios = ratios[ratios["code_epci"] == epci_unique]
        dju_f = dju_f[dju_f["code_epci"] == epci_unique]
        demo_f = demo_f[demo_f["code_epci"] == epci_unique]

    # 3) Produit cartésien EPCI × sous-secteur × année via le pivot partagé
    df = traj.merge(ratios, on="code_pivot", how="inner")

    # Facteurs DJU (statiques) - appliqués seulement si le flag est vrai
    df = df.merge(dju_f, on="code_epci", how="left")
    for col in ("corr_dju_chaud", "corr_dju_froid"):
        if col not in df.columns:
            df[col] = 1.0
    df["f_dju_chaud"] = np.where(df["correction_dju_chaud"], df["corr_dju_chaud"].fillna(1.0), 1.0)
    df["f_dju_froid"] = np.where(df["correction_dju_froid"], df["corr_dju_froid"].fillna(1.0), 1.0)

    # Facteur démographique (évolutif)
    df = df.merge(demo_f, on=["code_epci", "annee"], how="left")
    df["f_demo"] = np.where(df["influence_demographie"], df["demo_factor"].fillna(1.0), 1.0)

    # 4) Résultat brut
    df["valeur_brute"] = (
        df["valeur_nationale"] * df["pivot_ratio"]
        * df["f_dju_chaud"] * df["f_dju_froid"] * df["f_demo"]
    )

    # 4bis) Diagnostic de conservation (Σ EPCI vs zone par sous-secteur/année)
    # Somme restreinte à epci_zone_partition (EPCI hors EPT/Paris) : sinon la
    # contribution de la MGP serait comptée deux fois (elle-même + ses
    # EPT/Paris, imbriqués dedans).
    if filtrer_un_territoire:
        # Diagnostic zone entière non calculable sur un seul territoire
        # (cf. docstring) - disponible via le calcul batch complet.
        df["ecart_conservation"] = np.nan
    else:
        ecart = (
            df[df["code_epci"].isin(epci_zone_partition)]
            .groupby(["code_traj_supra", "annee"])
            .agg(somme_brute=("valeur_brute", "sum"), nat=("valeur_nationale", "first"))
            .reset_index()
        )
        ecart["ecart_conservation"] = (ecart["somme_brute"] - ecart["nat"]) / ecart["nat"].replace(0, np.nan)
        df = df.merge(
            ecart[["code_traj_supra", "annee", "ecart_conservation"]],
            on=["code_traj_supra", "annee"], how="left",
        )

    # 5) Recalage (ancré en année de référence)
    df = _apply_recalage(df, df_long, ref_year)

    # 5bis) Mode additif (UTCATF) : pour les années != ref, la valeur recalée
    # suit le delta national réparti par poids absolu au lieu du multiplicatif.
    # recalé(t) = obs(ref) + (national(t) - national(ref)) × |recal_factor| × abs_weight
    # Le delta national est mis à l'échelle de la base observée (ALDO), comme
    # le recalage multiplicatif l'est pour les autres secteurs : pour f > 0,
    # Σ recalé(t) = f × national(t). Sans ce facteur, un delta calibré sur
    # l'inventaire s'applique en absolu à une base ALDO plus petite (forêts :
    # 69 %) et écrase les puits. La valeur absolue conserve le sens de
    # variation du national quand ALDO et inventaire sont de signes opposés
    # (f < 0 : Zones humides, Autres terres). Sans recalage ou avec donnée
    # observée absente, recal_factor = 1.
    if "mode_additif" in df.columns:
        mask_add = df["mode_additif"].fillna(False).astype(bool)
        if mask_add.any():
            ref_recal = (
                df.loc[mask_add & (df["annee"] == ref_year),
                       ["code_epci", "code_traj_supra", "valeur_recalee"]]
                .drop_duplicates(["code_epci", "code_traj_supra"])
                .rename(columns={"valeur_recalee": "_recalee_ref"})
            )
            ref_nat = (
                df.loc[mask_add & (df["annee"] == ref_year),
                       ["code_traj_supra", "valeur_nationale"]]
                .drop_duplicates("code_traj_supra")
                .rename(columns={"valeur_nationale": "_nat_ref"})
            )
            df = df.merge(ref_recal, on=["code_epci", "code_traj_supra"], how="left")
            df = df.merge(ref_nat, on="code_traj_supra", how="left")

            mask_future = mask_add & (df["annee"] != ref_year)
            df.loc[mask_future, "valeur_recalee"] = (
                df.loc[mask_future, "_recalee_ref"]
                + (df.loc[mask_future, "valeur_nationale"] - df.loc[mask_future, "_nat_ref"])
                * df.loc[mask_future, "recal_factor"].abs()
                * df.loc[mask_future, "abs_weight"]
            )
            df = df.drop(columns=["_recalee_ref", "_nat_ref"])

    # 6) Métadonnées géographiques des EPCI
    geo_scope = [epci_unique] if filtrer_un_territoire else epci_zone
    geo_cols = ["code_epci", "nom_epci", "niveau_geo", "zone", "code_region", "code_departement"]
    geo = (
        df_long[df_long["code_epci"].isin(geo_scope)]
        .reindex(columns=geo_cols)  # niveau_geo absent d'une BDD de test : vide
        .drop_duplicates("code_epci")
    )
    df = df.merge(geo, on="code_epci", how="left")

    df = df[TERRITORIALISE_COLUMNS].sort_values(["code_epci", "type", "code_traj_supra", "annee"])
    log.info(
        f"Territorialisation {zone} : {df['code_epci'].nunique()} EPCI × "
        f"{df['code_traj_supra'].nunique()} sous-secteurs → {len(df)} lignes"
    )
    return df.reset_index(drop=True)


def _apply_recalage(
    df: pd.DataFrame,
    df_long: pd.DataFrame,
    ref_year: int,
) -> pd.DataFrame:
    """
    Calcule et applique le facteur de recalage par (EPCI, code_recalage),
    constant sur toutes les années de la trajectoire.

    facteur = observé(ref) / Σ_sous-secteurs valeur_brute(ref), après
    conversion d'unité (kt/GWh -> Mt/TWh, tCO2e -> MtCO2e).
    Dégradation propre : donnée absente ou somme nulle -> facteur 1 + flag qualité.
    """
    from pipeline.aggregate import load_catalogue
    unite_map = {ind["code"]: ind.get("unite", "") for ind in load_catalogue()}
    mask_recal = df["code_recalage"].notna()
    base = (
        df[mask_recal & (df["annee"] == ref_year)]
        .groupby(["code_epci", "code_recalage"])["valeur_brute"].sum()
        .rename("secteur_brut_ref").reset_index()
    )

    recal_codes = set(df["code_recalage"].dropna().unique())
    obs = (
        df_long[(df_long["code_indicateur"].isin(recal_codes)) & (df_long["annee"] == ref_year)]
        [["code_epci", "code_indicateur", "valeur"]]
        .rename(columns={"code_indicateur": "code_recalage", "valeur": "obs"})
    )
    obs["obs"] = obs["obs"] * obs["code_recalage"].map(
        lambda c: _recalage_unit_factor(unite_map.get(c, ""))
    )

    base = base.merge(obs, on=["code_epci", "code_recalage"], how="left")

    # Vectorisé (np.select) plutôt qu'un .apply(axis=1) : sur la BDD réelle
    # (~27 600 lignes code_epci x code_recalage), l'apply row-wise dominait le
    # temps de territorialize() (~12s sur ~14s), incompatible avec un appel à
    # la demande. Logique strictement équivalente à l'ancienne _factor().
    secteur_nul = base["secteur_brut_ref"].isna() | (base["secteur_brut_ref"] == 0)
    base["recal_factor"] = np.select(
        [base["obs"].isna(), secteur_nul],
        [1.0, 1.0],
        default=base["obs"] / base["secteur_brut_ref"].replace(0, np.nan),
    )
    base["qualite"] = np.select(
        [base["obs"].isna(), secteur_nul],
        [QUALITE_NON_RECALE_ABSENT, QUALITE_NON_RECALE_SECTEUR_NUL],
        default=QUALITE_OK,
    )

    df = df.merge(
        base[["code_epci", "code_recalage", "recal_factor", "qualite"]],
        on=["code_epci", "code_recalage"], how="left",
    )
    df["recal_factor"] = df["recal_factor"].fillna(1.0)
    df["qualite"] = df["qualite"].fillna(QUALITE_PASS_THROUGH)
    # Pivot manquant → résultat indéterminé
    df.loc[df["pivot_ratio"].isna(), "qualite"] = QUALITE_PIVOT_ABSENT

    df["valeur_recalee"] = df["valeur_brute"] * df["recal_factor"]
    return df


def build_territorialise(
    annee: int = ANNEE_REF,
    zone: str = TERR_ZONE_DEFAUT,
    **kwargs,
) -> pd.DataFrame:
    """
    Point d'entrée haut niveau : construit la BDD long puis territorialise.
    `**kwargs` transmis à `territorialize` (dju_variant, epci_unique).
    """
    from pipeline.aggregate import build_bdd

    df_long = build_bdd(annee=annee)
    return territorialize(df_long, zone=zone, ref_year=annee, **kwargs)
