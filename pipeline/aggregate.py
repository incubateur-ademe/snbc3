"""
pipeline/aggregate.py
=====================
Agrégation des données commune→EPCI (+ commune→EPT) et calcul des totaux
France/Hexagone/DROM.

Flux :
  1. Charge les données source par commune (sources/insee_rp.py, etc.)
  2. Joint la correspondance commune→EPCI, et pour les sources qui le
     permettent commune→EPT (sources/geo_epci.py)
  3. Agrège selon la méthode définie dans le catalogue (somme ou moyenne pond.)
  4. Ajoute les lignes agrégées FRANCE, HEXAGONE et DROM
  5. Retourne un DataFrame au format long (code_epci, niveau_geo,
     code_indicateur, annee, valeur, ...)

Règles d'agrégation (issues du catalogue indicateurs.yaml) :
  - somme_communes      : somme directe des valeurs communales (calculée à la
                          fois en EPCI et, quand la source le permet, en EPT)
  - moyenne_pond_pop    : utilisé pour les totaux France/Hexagone/DROM uniquement
                          (les DJU EPCI sont calculés par IDW, pas par agrégation)
  - donnee_directe_epci : données déjà à l'échelle EPCI (Omphale, énergie-climat),
                          pas d'agrégation, pas de déclinaison EPT

EPT (établissements publics territoriaux de la Métropole du Grand Paris) :
  Un EPT est une subdivision imbriquée dans un seul EPCI (la MGP), pas une
  partition du territoire comme l'EPCI. Paris (75056) n'appartient à aucun
  EPT (statut particulier) : pour que les 11 EPT + Paris partitionnent
  exactement la MGP, Paris est ajoutée comme sa propre ligne, niveau_geo
  `COMMUNE` (distinct d'`EPT` : ce n'est pas un EPT au sens juridique).
  La colonne `niveau_geo` (EPCI / EPT / COMMUNE / TOTAL) distingue ces mailles
  dans la même table longue - `compute_totaux` et
  `pipeline.territorialize.territorialize` ne gardent que EPCI (+ TOTAL),
  sous peine de compter deux fois la contribution de la MGP (elle-même + ses
  11 EPT + Paris). Voir check_couverture_ept et check_conservation_ept pour
  les contrôles de cohérence associés.
"""

import logging
from datetime import date

import numpy as np
import pandas as pd
import yaml

from config.settings import (
    ANNEE_REF, GROUPES_ZONES, INTERIM_DIR, ZONE_PAR_REGION_DROM,
)

log = logging.getLogger(__name__)

# Codes région DROM (geo.api.gouv.fr) : 01 Guadeloupe, 02 Martinique, 03 Guyane,
# 04 Réunion, 06 Mayotte.
DROM_REGIONS: set[str] = set(ZONE_PAR_REGION_DROM)

# Niveaux géographiques de la colonne `niveau_geo`. EPT et COMMUNE (Paris) sont
# des subdivisions imbriquées dans un seul EPCI (la Métropole du Grand Paris) :
# ce n'est PAS une partition du territoire comme l'EPCI. Toute somme "totaux
# nationaux" ou "territorialisation" doit se limiter à NIVEAU_GEO_EPCI (+
# NIVEAU_GEO_TOTAL pour les dénominateurs de zone), sous peine de compter deux
# fois la contribution de la MGP (elle-même + ses 11 EPT + Paris).
NIVEAU_GEO_EPCI    = "EPCI"
NIVEAU_GEO_EPT     = "EPT"
NIVEAU_GEO_COMMUNE = "COMMUNE"  # Paris uniquement à ce jour : seule commune hors EPT
NIVEAU_GEO_TOTAL   = "TOTAL"

# Niveaux qui participent à la partition du territoire (donc valides pour les
# totaux nationaux et la territorialisation) : tout le reste (EPT, COMMUNE)
# est une subdivision imbriquée, à exclure de ces calculs.
NIVEAUX_GEO_PARTITION = {NIVEAU_GEO_EPCI, NIVEAU_GEO_TOTAL}

CODE_EPCI_MGP = "200054781"
# CODE_COMMUNE_PARIS / NOM_COMMUNE_PARIS : source de vérité dans
# sources.geo_epci (réutilisées telles quelles pour les centroïdes DJU).
from sources.geo_epci import CODE_COMMUNE_PARIS, NOM_COMMUNE_PARIS  # noqa: E402


class SourceEnEchec(RuntimeError):
    """Une ou plusieurs sources n'ont pas pu être chargées."""


def _echec(source: str, e: Exception) -> str:
    """Journalise l'échec d'une source (avec la trace) et renvoie son libellé."""
    log.error(f"{source} : {e}", exc_info=e)
    return f"{source} : {type(e).__name__}: {e}"


def zone_geo_lookup(epci_ref: pd.DataFrame) -> pd.DataFrame:
    """
    Construit la table (code_epci, zone, code_region, code_departement) depuis
    le référentiel EPCI (load_epci_centroids, `codes_regions`/`codes_departements`
    en listes - un EPCI peut être rattaché à plusieurs régions/départements).

    Un EPCI à cheval sur plusieurs régions reçoit un `code_region` joint
    (ex. "84,27"). Chaque DROM est sa propre zone (cf.
    config.settings.ZONE_PAR_REGION_DROM) ; toute autre région relève de
    HEXAGONE. Aucun EPCI ne chevauche en pratique deux zones ; si cela
    arrivait, la région DROM de plus petit code l'emporte, de façon
    déterministe.
    """
    def _zone(regions) -> str:
        drom = sorted(set(regions or []) & DROM_REGIONS)
        return ZONE_PAR_REGION_DROM[drom[0]] if drom else "HEXAGONE"

    geo_lookup = epci_ref[["code_epci", "codes_regions", "codes_departements"]].copy()
    geo_lookup["zone"] = geo_lookup["codes_regions"].apply(_zone)
    geo_lookup["code_region"] = geo_lookup["codes_regions"].apply(
        lambda r: ",".join(r) if r else ""
    )
    geo_lookup["code_departement"] = geo_lookup["codes_departements"].apply(
        lambda d: ",".join(d) if d else ""
    )
    return geo_lookup[["code_epci", "zone", "code_region", "code_departement"]]


def ept_geo_lookup(correspondance_ept_brute: pd.DataFrame) -> pd.DataFrame:
    """
    Table (code_epci, zone, code_region, code_departement) pour les EPT, sur
    le même schéma que zone_geo_lookup. Tous les EPT sont en Île-de-France
    (région 11) ; le département dépend des communes membres - deux EPT sont
    à cheval sur deux départements (Grand-Orly Seine Bièvre : 91/94, Boucle
    Nord de Seine : 92/95).

    Paramètre : sortie brute de sources.geo_epci.load_commune_ept_cog
                (colonnes code_ept, code_dep - avant renommage code_epci/nom_epci).
    """
    g = (
        correspondance_ept_brute.groupby("code_ept")["code_dep"]
        .apply(lambda deps: ",".join(sorted(set(deps))))
        .reset_index()
        .rename(columns={"code_ept": "code_epci", "code_dep": "code_departement"})
    )
    g["zone"] = "HEXAGONE"
    g["code_region"] = "11"
    return g[["code_epci", "zone", "code_region", "code_departement"]]


def load_catalogue() -> list[dict]:
    """Charge le catalogue d'indicateurs depuis config/indicateurs.yaml."""
    from config.settings import ROOT_DIR
    path = ROOT_DIR / "config" / "indicateurs.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["indicateurs"]


def _to_long(
    df_wide: pd.DataFrame,
    code_epci_col: str,
    nom_epci_col: str | None,
    annee: int,
    source: str,
    qualite: str = "ok",
) -> pd.DataFrame:
    """
    Pivote un DataFrame large (EPCI en lignes, indicateurs en colonnes)
    vers le format long de la BDD.
    """
    id_cols = [code_epci_col]
    if nom_epci_col and nom_epci_col in df_wide.columns:
        id_cols.append(nom_epci_col)

    df = df_wide.reset_index() if df_wide.index.name not in (None, "RangeIndex") else df_wide

    value_cols = [c for c in df.columns if c not in id_cols]
    df_long = df[id_cols + value_cols].melt(
        id_vars=id_cols, var_name="code_indicateur", value_name="valeur"
    )
    df_long = df_long.rename(columns={code_epci_col: "code_epci"})
    if nom_epci_col and nom_epci_col in df_long.columns:
        df_long = df_long.rename(columns={nom_epci_col: "nom_epci"})
    else:
        df_long["nom_epci"] = ""

    df_long["annee"]          = annee
    df_long["source"]         = source
    df_long["date_extraction"] = str(date.today())
    df_long["qualite"]        = qualite

    return df_long[["code_epci", "nom_epci", "code_indicateur", "annee",
                    "valeur", "source", "date_extraction", "qualite"]]


def aggregate_communes_to_epci(
    df_communes: pd.DataFrame,
    correspondance: pd.DataFrame,
    indicateurs: list[str],
    annee: int,
    source: str,
    qualite: str = "ok",
    df_secret_communes: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Agrège des données communales vers EPCI par somme.

    Paramètres :
      df_communes   : DataFrame indexé sur CODGEO, colonnes = indicateurs
      correspondance: DataFrame avec codgeo, code_epci, nom_epci
      indicateurs   : liste des colonnes à agréger
      annee         : année des données
      df_secret_communes : DataFrame booléen indexé sur CODGEO, même colonnes
                     (sous-ensemble) que df_communes - True si la valeur
                     communale est masquée par le secret statistique. Si au
                     moins une commune d'un EPCI est masquée pour un
                     indicateur, la somme EPCI est marquée qualite=secret_stat
                     (elle sous-estime potentiellement le total réel).

    Retourne un DataFrame au format long (bdd_epci).
    """
    # Garder uniquement les colonnes disponibles
    cols_ok = [c for c in indicateurs if c in df_communes.columns]
    if not cols_ok:
        log.warning(f"Aucun indicateur disponible parmi {indicateurs} pour {source} {annee}")
        return pd.DataFrame()

    df = df_communes[cols_ok].copy()
    df.index.name = "codgeo"
    df = df.reset_index()

    # Jointure commune→EPCI
    corr = correspondance[["codgeo", "code_epci", "nom_epci"]].copy() if "nom_epci" in correspondance.columns \
        else correspondance[["codgeo", "code_epci"]].copy()
    df_joined = df.merge(corr, on="codgeo", how="inner")

    # Couverture mesurée par rapport au périmètre de `correspondance` (pas au
    # total de communes de la source) : `correspondance` peut être un
    # sous-ensemble volontairement restreint (EPT de la MGP, Paris seule),
    # auquel cas comparer au total national ferait remonter un faux positif
    # systématique (ex. Paris seule : 1 commune trouvée sur un total source
    # de ~35000, alors que la couverture du périmètre est complète).
    n_communes_perimetre = corr["codgeo"].nunique()
    n_communes_matchees = df_joined["codgeo"].nunique()
    if n_communes_matchees < n_communes_perimetre * 0.9:
        log.warning(
            f"{source} {annee}: seulement {n_communes_matchees}/{n_communes_perimetre} communes "
            f"du périmètre trouvées dans la source"
        )

    # Agrégation par EPCI
    grp_cols = ["code_epci"] + (["nom_epci"] if "nom_epci" in df_joined.columns else [])
    df_epci = df_joined.groupby(grp_cols, as_index=False)[cols_ok].sum(min_count=1)

    # Mise au format long
    df_long = _to_long(df_epci, "code_epci", "nom_epci", annee, source, qualite)

    # Propagation du secret statistique : EPCI marqué secret_stat dès qu'une
    # commune membre a une valeur masquée pour l'indicateur concerné.
    if df_secret_communes is not None:
        secret_cols = [c for c in cols_ok if c in df_secret_communes.columns]
        if secret_cols:
            df_secret = df_secret_communes[secret_cols].copy()
            df_secret.index.name = "codgeo"
            df_secret = df_secret.reset_index()
            df_secret_joined = df_secret.merge(corr[["codgeo", "code_epci"]], on="codgeo", how="inner")
            df_secret_epci = df_secret_joined.groupby("code_epci", as_index=False)[secret_cols].any()
            df_secret_long = df_secret_epci.melt(
                id_vars="code_epci", var_name="code_indicateur", value_name="is_secret"
            )
            df_secret_long = df_secret_long[df_secret_long["is_secret"]]
            if not df_secret_long.empty:
                df_long = df_long.merge(
                    df_secret_long[["code_epci", "code_indicateur"]],
                    on=["code_epci", "code_indicateur"], how="left", indicator="_secret_match",
                )
                df_long.loc[df_long["_secret_match"] == "both", "qualite"] = "secret_stat"
                df_long = df_long.drop(columns=["_secret_match"])

    log.info(f"{source} {annee}: {df_epci['code_epci'].nunique()} EPCI agrégés")
    return df_long


def _warn_coverage(source: str, source_epcis: set, ref_epcis: set) -> None:
    """Émet des warnings de couverture EPCI d'une source par rapport à la liste de référence."""
    missing = ref_epcis - source_epcis
    extra   = source_epcis - ref_epcis
    if missing and extra:
        log.warning(
            f"{source} : {len(missing)} EPCI de référence absents, "
            f"{len(extra)} hors liste de référence "
            f"({len(source_epcis & ref_epcis)}/{len(ref_epcis)} couverts)"
        )
    elif missing:
        log.warning(
            f"{source} : {len(missing)} EPCI de référence absents "
            f"({len(source_epcis & ref_epcis)}/{len(ref_epcis)} couverts)"
        )
    elif extra:
        log.warning(
            f"{source} : {len(extra)} EPCI hors liste de référence "
            f"(couverture complète à {len(ref_epcis)} EPCI)"
        )
    else:
        log.info(f"{source} : couverture EPCI complète ({len(source_epcis)}/{len(ref_epcis)} EPCI)")


def check_couverture_ept(
    correspondance_epci: pd.DataFrame,
    correspondance_ept: pd.DataFrame,
    code_epci_mgp: str = CODE_EPCI_MGP,
    code_commune_paris: str = CODE_COMMUNE_PARIS,
) -> None:
    """
    Vérifie que les communes de la Métropole du Grand Paris (EPCI 200054781)
    se répartissent exactement entre les 11 EPT et Paris, sans fuite ni
    doublon - sinon les agrégations EPCI et EPT divergent silencieusement.

    Paramètres :
      correspondance_epci : table commune→EPCI complète (sources.geo_epci)
      correspondance_ept  : table commune→EPT (sources.geo_epci), Paris et le
                             hors-MGP déjà absents

    Lève une ValueError en cas d'écart (fuite, doublon).
    """
    communes_mgp = set(
        correspondance_epci.loc[correspondance_epci["code_epci"] == code_epci_mgp, "codgeo"]
    )
    communes_ept = set(correspondance_ept["codgeo"])
    attendu = communes_ept | {code_commune_paris}

    manquantes = communes_mgp - attendu
    en_trop    = attendu - communes_mgp
    doublons   = correspondance_ept["codgeo"][correspondance_ept["codgeo"].duplicated()].unique()

    erreurs = []
    if manquantes:
        erreurs.append(
            f"{len(manquantes)} commune(s) de la MGP absente(s) des EPT+Paris : "
            f"{sorted(manquantes)[:10]}"
        )
    if en_trop:
        erreurs.append(
            f"{len(en_trop)} commune(s) EPT/Paris hors MGP : {sorted(en_trop)[:10]}"
        )
    if len(doublons):
        erreurs.append(
            f"{len(doublons)} commune(s) en double dans la table EPT : {sorted(doublons)[:10]}"
        )

    if erreurs:
        raise ValueError("Incohérence commune↔EPT : " + " ; ".join(erreurs))

    log.info(
        f"Couverture EPT vérifiée : {len(communes_ept)} communes en EPT + Paris "
        f"= {len(communes_mgp)} communes MGP"
    )


def check_conservation_ept(
    df_all: pd.DataFrame,
    code_epci_mgp: str = CODE_EPCI_MGP,
    code_commune_paris: str = CODE_COMMUNE_PARIS,
    n_ept_attendu: int = 11,
    tol: float = 1e-6,
) -> pd.DataFrame:
    """
    Les 11 EPT + Paris partitionnent exactement les communes de la MGP
    (garanti par check_couverture_ept) : pour un indicateur "somme_communes",
    Σ(11 EPT) + Paris doit donc être **exactement égal** (aux arrondis
    flottants près) à la valeur MGP - ni excédent (fuite/doublon), ni déficit.

    Ne compare que les (indicateur, année) à couverture complète : les
    `n_ept_attendu` EPT ET Paris présents (non NaN). Une couverture
    incomplète (secret statistique, donnée absente pour un EPT - qui peut se
    traduire par une ligne manquante, pas seulement par une valeur NaN) n'est
    **pas** un écart de conservation - elle est reportée séparément (colonne
    `couverture_incomplete`) plutôt que jugée comme une fuite, pour ne pas
    noyer les vrais écarts sous des absences de données attendues.
    `n_ept_attendu` est un paramètre (pas déduit de df_all) précisément pour
    détecter ce cas de ligne manquante plutôt que de valeur NaN.

    Restreint aux indicateurs "somme_communes"/"calcul_derive" (catalogue) :
    ce sont les seuls dont la définition même est une somme sur les communes,
    donc les seuls pour qui Σ(EPT)+Paris=MGP est un invariant attendu. DJU
    (moyenne climatique) et POP_PROJ_RATIO (ratio) sont des grandeurs
    intensives, pas des stocks - les sommer entre territoires n'a pas de
    sens, ce n'est pas un « écart de conservation » mais un non-sens
    dimensionnel : les inclure ici ne ferait que produire de fausses alertes.

    Non bloquant (log.warning / log.info) : retourne les lignes à couverture
    complète et écart hors tolérance, pour inspection.
    """
    if "niveau_geo" not in df_all.columns:
        return pd.DataFrame()

    catalogue = load_catalogue()
    indicateurs_additifs = {
        ind["code"] for ind in catalogue
        if ind.get("methode", "somme_communes") in ("somme_communes", "calcul_derive")
    }

    ept_rows = df_all[
        (df_all["niveau_geo"] == NIVEAU_GEO_EPT)
        & (df_all["code_indicateur"].isin(indicateurs_additifs))
    ]
    if ept_rows.empty:
        return pd.DataFrame()

    grp = ept_rows.groupby(["code_indicateur", "annee"])["valeur"]
    somme_ept = grp.sum(min_count=1).rename("somme_ept")
    n_ept_dispo = grp.count().rename("n_ept_dispo")

    paris = (
        df_all[
            (df_all["niveau_geo"] == NIVEAU_GEO_COMMUNE)
            & (df_all["code_epci"] == code_commune_paris)
        ]
        .set_index(["code_indicateur", "annee"])["valeur"]
        .rename("valeur_paris")
    )
    mgp = (
        df_all[df_all["code_epci"] == code_epci_mgp]
        .set_index(["code_indicateur", "annee"])["valeur"]
        .rename("valeur_mgp")
    )

    comp = pd.concat([somme_ept, n_ept_dispo, paris, mgp], axis=1)
    comp = comp.dropna(subset=["valeur_mgp"])
    if comp.empty:
        return pd.DataFrame()

    comp["couverture_incomplete"] = (comp["n_ept_dispo"] < n_ept_attendu) | comp["valeur_paris"].isna()

    incomplets = comp[comp["couverture_incomplete"]]
    if not incomplets.empty:
        log.info(
            f"{len(incomplets)} couple(s) indicateur×année à couverture EPT/Paris "
            f"incomplète, non comparés (secret statistique ou donnée absente probable)"
        )

    complet = comp[~comp["couverture_incomplete"]].copy()
    if complet.empty:
        return pd.DataFrame()

    complet["ecart"] = (complet["somme_ept"] + complet["valeur_paris"]) - complet["valeur_mgp"]
    seuil = tol * complet["valeur_mgp"].abs().clip(lower=1)
    ecarts = complet[complet["ecart"].abs() > seuil]

    if not ecarts.empty:
        log.warning(
            f"{len(ecarts)} couple(s) indicateur×année où Σ(EPT)+Paris ≠ MGP à "
            f"couverture complète (fuite/duplication/perte dans commune↔EPT) :\n"
            f"{ecarts.to_string()}"
        )
    return ecarts.reset_index()


def compute_totaux(
    df_long: pd.DataFrame,
    zone_par_epci: dict[str, str] | None = None,
    pop_ref: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Calcule les lignes agrégées : FRANCE, une par zone (HEXAGONE, GUADELOUPE,
    ...) et une par groupe de zones (DROM, cf. config.settings.GROUPES_ZONES).
    Chaque ligne porte le nom de la zone ou du groupe comme `code_epci`.

    Pour les indicateurs à somme : somme directe.
    Pour les indicateurs à moyenne pondérée (DJU, POP_PROJ_RATIO) :
      moyenne pondérée par la population de référence.

    Paramètres :
      df_long       : BDD au format long (sans les totaux)
      zone_par_epci : code EPCI -> zone. Si None, seule la ligne FRANCE est
                      produite. Un EPCI absent de la table n'entre que dans
                      FRANCE.
      pop_ref       : DataFrame (code_epci, POP_REF) pour la pondération.
                      Si None, tous les EPCI ont le même poids (moyenne simple).

    Retourne un DataFrame long avec seulement les lignes agrégées.
    """
    catalogue = load_catalogue()
    methode_map = {ind["code"]: ind.get("methode", "somme_communes") for ind in catalogue}

    # Indicateurs à moyenne pondérée pour les totaux
    pond_codes = {c for c, m in methode_map.items() if m == "moyenne_pond_pop"}

    # L'EPT est imbriqué dans un seul EPCI (la MGP) : le laisser entrer ici
    # sommerait deux fois la contribution de la MGP aux totaux de zone. Seule
    # la maille EPCI est une vraie partition du territoire.
    if "niveau_geo" in df_long.columns:
        df_long = df_long[df_long["niveau_geo"] == NIVEAU_GEO_EPCI]

    # Périmètres : FRANCE (tout), chaque zone, chaque groupe de zones.
    zone_par_epci = dict(zone_par_epci or {})
    libelles = {"FRANCE": "France entière", "HEXAGONE": "France hexagonale"}
    perimetres: dict[str, set[str] | None] = {"FRANCE": None}
    for zone in dict.fromkeys(zone_par_epci.values()):
        perimetres[zone] = {e for e, z in zone_par_epci.items() if z == zone}
    for groupe, membres in GROUPES_ZONES.items():
        epci_groupe = {e for e, z in zone_par_epci.items() if z in membres}
        if epci_groupe:
            perimetres[groupe] = epci_groupe

    rows = []
    annees = df_long["annee"].unique()

    for annee in annees:
        df_an = df_long[df_long["annee"] == annee].copy()

        for scope, epci_scope in perimetres.items():
            label = libelles.get(scope, scope)
            df_scope = df_an if epci_scope is None else df_an[df_an["code_epci"].isin(epci_scope)]

            if df_scope.empty:
                continue

            for ind_code, grp in df_scope.groupby("code_indicateur"):
                grp = grp.dropna(subset=["valeur"])
                if grp.empty:
                    continue

                source = grp["source"].iloc[0]
                date_ext = grp["date_extraction"].iloc[0]

                if ind_code in pond_codes and pop_ref is not None:
                    # Moyenne pondérée par population
                    merged = grp.merge(pop_ref, on="code_epci", how="left")
                    merged = merged.dropna(subset=["valeur", "POP_REF"])
                    if merged.empty:
                        valeur = grp["valeur"].mean()
                        qualite = "moyenne_simple"
                    else:
                        w = merged["POP_REF"].values
                        valeur = float((merged["valeur"].values * w).sum() / w.sum())
                        qualite = "moyenne_pond_pop"
                else:
                    valeur  = float(grp["valeur"].sum())
                    qualite = "ok"

                rows.append({
                    "code_epci":       scope,
                    "nom_epci":        label,
                    "code_indicateur": str(ind_code),
                    "annee":           int(annee),
                    "valeur":          round(valeur, 4),
                    "source":          source,
                    "date_extraction": date_ext,
                    "qualite":         qualite,
                    "niveau_geo":      NIVEAU_GEO_TOTAL,
                })

    return pd.DataFrame(rows)


def add_unite(df_long: pd.DataFrame) -> pd.DataFrame:
    """Enrichit la BDD long avec les unités issues du catalogue."""
    catalogue = load_catalogue()
    unite_map = {ind["code"]: ind.get("unite", "") for ind in catalogue}
    df_long["unite"] = df_long["code_indicateur"].map(unite_map).fillna("")
    return df_long


def compute_derived_indicators(df_long: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule les indicateurs dérivés définis avec methode: calcul_derive dans le catalogue.

    Supporte les formules binaires de la forme "A + B" ou "A - B".

    Stratégie de jointure :
      1. Jointure sur (code_epci, annee) - cas normal (ex: EMP_CTS + EMP_APESAS).
      2. Si vide, repli sur (code_epci) seul avec la valeur la plus récente de chaque
         opérande - cas inter-années.
         L'année retenue est celle de l'opérande ayant le moins d'années distinctes
         (la donnée la plus "figée").

    Retourne uniquement les nouvelles lignes.
    """
    catalogue = load_catalogue()
    derives = [ind for ind in catalogue if ind.get("methode") == "calcul_derive"]
    if not derives:
        return pd.DataFrame()

    new_rows = []
    for ind in derives:
        code    = ind["code"]
        formule = ind.get("formule", "")

        # --- Détection opérateur et opérandes ---
        if " - " in formule:
            # Soustraction binaire uniquement
            parts = [t.strip() for t in formule.split(" - ", 1)]
            if len(parts) != 2:
                log.warning(f"Formule non reconnue pour {code}: '{formule}'")
                continue
            operandes = parts
            op = "-"
        elif " + " in formule:
            operandes = [t.strip() for t in formule.split(" + ")]
            op = "+"
        else:
            log.warning(f"Formule non reconnue pour {code}: '{formule}'")
            continue

        # --- Vérification présence de chaque opérande ---
        absents = [o for o in operandes if df_long[df_long["code_indicateur"] == o].empty]
        if absents:
            log.warning(f"{code}: opérande(s) absent(s) ({', '.join(absents)})")
            continue

        if op == "+" and len(operandes) > 2:
            # Somme N-aire : pivot → somme ligne → long
            df_sub = df_long[df_long["code_indicateur"].isin(operandes)][
                ["code_epci", "nom_epci", "annee", "date_extraction", "code_indicateur", "valeur", "qualite"]
            ].copy()
            df_wide = df_sub.pivot_table(
                index=["code_epci", "nom_epci", "annee", "date_extraction"],
                columns="code_indicateur",
                values="valeur",
                aggfunc="first",
            )
            df_qual_wide = df_sub.pivot_table(
                index=["code_epci", "nom_epci", "annee", "date_extraction"],
                columns="code_indicateur",
                values="qualite",
                aggfunc="first",
            )
            # Un opérande masqué par le secret statistique rend la somme
            # potentiellement sous-estimée : à propager sur l'indicateur dérivé.
            has_secret = (df_qual_wide.reindex(columns=operandes) == "secret_stat").any(axis=1)
            df_wide["valeur"] = df_wide[operandes].sum(axis=1, min_count=1)
            df_wide = df_wide[["valeur"]].reset_index()
            has_secret = has_secret.reset_index(drop=True)
            df_wide = df_wide.dropna(subset=["valeur"])
            df_wide["code_indicateur"] = code
            df_wide["source"]          = ind.get("source", "calcul_derive")
            df_wide["qualite"]         = np.where(
                has_secret.loc[df_wide.index], "secret_stat", "calcul_derive"
            )
            new_rows.append(df_wide[["code_epci", "nom_epci", "code_indicateur", "annee",
                                     "valeur", "source", "date_extraction", "qualite"]])
            log.info(f"Indicateur dérivé {code} calculé ({formule}): {len(df_wide)} lignes")
            continue

        # --- Cas binaire (+ ou -) avec repli inter-années ---
        left, right = operandes[0], operandes[1]
        df_left  = df_long[df_long["code_indicateur"] == left][
            ["code_epci", "nom_epci", "annee", "date_extraction", "valeur", "qualite"]
        ].rename(columns={"valeur": "val_left", "qualite": "qual_left"})
        df_right = df_long[df_long["code_indicateur"] == right][
            ["code_epci", "annee", "valeur", "qualite"]
        ].rename(columns={"valeur": "val_right", "qualite": "qual_right"})

        # 1. Tentative de jointure sur (code_epci, annee)
        df_merged = df_left.merge(df_right, on=["code_epci", "annee"], how="inner")

        if df_merged.empty:
            # 2. Repli inter-années : valeur la plus récente par EPCI
            df_l = (df_left.sort_values("annee", ascending=False)
                           .drop_duplicates("code_epci"))
            df_r = (df_right.sort_values("annee", ascending=False)
                            .drop_duplicates("code_epci")
                            .rename(columns={"annee": "annee_right"}))
            df_merged = df_l.merge(df_r, on="code_epci", how="inner")
            # L'année retenue est celle de l'opérande le plus figé (moins d'années distinctes)
            if df_right["annee"].nunique() <= df_left["annee"].nunique():
                df_merged["annee"] = df_merged["annee_right"]
            df_merged = df_merged.drop(columns=["annee_right"])
            log.info(f"{code}: jointure inter-années utilisée ({left} / {right})")

        if op == "+":
            df_merged["valeur"] = df_merged["val_left"] + df_merged["val_right"]
        else:
            df_merged["valeur"] = df_merged["val_left"] - df_merged["val_right"]

        # Un opérande masqué par le secret statistique rend le résultat
        # potentiellement erroné : à propager sur l'indicateur dérivé.
        has_secret = (df_merged["qual_left"] == "secret_stat") | (df_merged["qual_right"] == "secret_stat")

        df_merged = df_merged.dropna(subset=["valeur"])
        df_merged["code_indicateur"] = code
        df_merged["source"]          = ind.get("source", "calcul_derive")
        df_merged["qualite"]         = np.where(
            has_secret.loc[df_merged.index], "secret_stat", "calcul_derive"
        )
        new_rows.append(df_merged[["code_epci", "nom_epci", "code_indicateur", "annee",
                                   "valeur", "source", "date_extraction", "qualite"]])
        log.info(f"Indicateur dérivé {code} calculé ({formule}): {len(df_merged)} lignes")

    if not new_rows:
        return pd.DataFrame()

    return pd.concat(new_rows, ignore_index=True)


def build_bdd(
    annee: int = ANNEE_REF,
    sources: list[str] | None = None,
) -> pd.DataFrame:
    """
    Construit la BDD complète au format long pour une année donnée.

    Orchestre l'appel à tous les extracteurs disponibles, l'agrégation
    commune→EPCI (et commune→EPT pour les sources qui le permettent), et
    l'ajout des totaux France/Hexagone/DROM.

    Paramètres :
      annee   : année à traiter
      sources : liste de sources à inclure (None = toutes)
                ['insee_rp', 'flores', 'agreste', 'dju', 'omphale', 'mobilite_dt',
                 'parc_vehicules', 'energie_climat', 'citepa_igt', 'aldo']

    Retourne un DataFrame long prêt pour l'export, colonne `niveau_geo`
    (EPCI / EPT / TOTAL) comprise.
    """
    from sources.geo_epci import (
        load_commune_epci_cog, load_epci_centroids,
        load_commune_ept_cog, load_ept_list,
        regrouper_arrondissements_plm,
    )

    if sources is None:
        sources = ["insee_rp", "flores", "agreste", "dju", "omphale", "mobilite_dt", "parc_vehicules", "energie_climat", "citepa_igt", "aldo"]

    # Correspondance commune→EPCI
    log.info("Chargement correspondance commune→EPCI...")
    correspondance = load_commune_epci_cog()

    # Correspondance commune→EPT (Métropole du Grand Paris uniquement).
    # Réutilisée telle quelle par aggregate_communes_to_epci (colonnes
    # renommées code_epci/nom_epci) : les lignes résultantes portent un code
    # EPT dans la colonne code_epci, distinguées ensuite par niveau_geo.
    correspondance_ept_brute = load_commune_ept_cog()
    check_couverture_ept(correspondance, correspondance_ept_brute)
    correspondance_ept = correspondance_ept_brute.rename(
        columns={"code_ept": "code_epci", "nom_ept": "nom_epci"}
    )[["codgeo", "code_epci", "nom_epci"]]

    # Paris (75056) n'appartient à aucun EPT (statut particulier) : pour que
    # les 11 EPT + Paris partitionnent exactement la MGP, on l'agrège comme sa
    # propre "commune-territoire" via le même mécanisme que l'EPT (table à une
    # ligne), taguée niveau_geo COMMUNE plus bas - jamais EPT.
    correspondance_paris = pd.DataFrame([
        {"codgeo": CODE_COMMUNE_PARIS, "code_epci": CODE_COMMUNE_PARIS, "nom_epci": NOM_COMMUNE_PARIS}
    ])

    # Référentiel EPCI (noms)
    epci_ref = load_epci_centroids()
    epci_names = epci_ref.set_index("code_epci")["nom_epci"].to_dict()

    # --------------------------------------------------------------------------
    # Liste de référence EPCI : millésime Insee le plus récent (load_epci_centroids)
    # --------------------------------------------------------------------------
    geo_epcis = set(epci_ref["code_epci"])
    ref_epcis = geo_epcis
    ref_epts  = set(load_ept_list()["code_ept"])
    log.info(f"Liste de référence EPCI : {len(ref_epcis)} EPCI (Insee)")
    log.info(f"Liste de référence EPT : {len(ref_epts)} EPT (Insee, MGP uniquement)")

    # Une source en échec arrête la construction de la BDD (après avoir tenté
    # toutes les autres, pour lister tous les échecs d'un coup) : une BDD privée
    # d'une source produirait des résultats faux sans le signaler.
    echecs: list[str] = []

    # Pré-chargement énergie-climat (évite un double appel plus bas + cross-validation)
    _df_ec_preload: pd.DataFrame | None = None
    _df_ec_qualite_preload: pd.DataFrame | None = None

    if "energie_climat" in sources:
        try:
            from sources.energie_climat import extract_indicators as _extract_ec_ref
            _df_ec_preload, _df_ec_qualite_preload = _extract_ec_ref(annee)
            ec_epcis = set(_df_ec_preload.index)
            in_ec_not_geo = ec_epcis - geo_epcis
            in_geo_not_ec = geo_epcis - ec_epcis
            if in_ec_not_geo:
                log.warning(
                    f"Énergie-climat : {len(in_ec_not_geo)} codes EPCI absents de la liste de référence "
                    f"(codes non reconnus)"
                )
            if in_geo_not_ec:
                log.warning(
                    f"Énergie-climat : {len(in_geo_not_ec)} EPCI de référence sans données OREC"
                )
        except Exception as e:
            echecs.append(_echec("Énergie-climat (pré-chargement)", e))

    all_frames = []
    df_rp = None

    # --- INSEE RP ---
    if "insee_rp" in sources:
        try:
            from sources.insee_rp import extract_indicators
            catalogue = load_catalogue()
            ind_rp = [
                ind["code"] for ind in catalogue
                if ind.get("source") == "INSEE_RP"
            ]
            df_rp, df_rp_secret = extract_indicators(annee)
            df_long_rp = aggregate_communes_to_epci(
                df_rp, correspondance, ind_rp, annee, "INSEE_RP", df_secret_communes=df_rp_secret
            )
            all_frames.append(df_long_rp)
            _warn_coverage("INSEE_RP", set(df_long_rp["code_epci"].unique()), ref_epcis)

            df_long_rp_ept = aggregate_communes_to_epci(
                df_rp, correspondance_ept, ind_rp, annee, "INSEE_RP", df_secret_communes=df_rp_secret
            )
            all_frames.append(df_long_rp_ept)
            _warn_coverage("INSEE_RP (EPT)", set(df_long_rp_ept["code_epci"].unique()), ref_epts)

            all_frames.append(aggregate_communes_to_epci(
                df_rp, correspondance_paris, ind_rp, annee, "INSEE_RP", df_secret_communes=df_rp_secret
            ))
        except Exception as e:
            echecs.append(_echec(f"INSEE RP {annee}", e))

    # --- FLORES ---
    if "flores" in sources:
        try:
            from sources.flores import load_flores
            catalogue = load_catalogue()
            ind_flores = [ind["code"] for ind in catalogue if ind.get("source") == "FLORES"]
            df_fl, df_fl_secret = load_flores(annee)
            # Paris/Lyon/Marseille publiés par arrondissement dans FLORES : à
            # regrouper sous leur commune, sinon ces trois villes sont
            # absentes de l'agrégation (75056/69123/13055 introuvables).
            df_fl = regrouper_arrondissements_plm(df_fl)
            # Regroupement booléen (True si au moins un arrondissement masqué) :
            # sum() sur bool compte les True, > 0 revient à un "any" par groupe.
            df_fl_secret = regrouper_arrondissements_plm(df_fl_secret.astype(int)) > 0
            df_long_fl = aggregate_communes_to_epci(
                df_fl, correspondance, ind_flores, annee, "FLORES", df_secret_communes=df_fl_secret
            )
            all_frames.append(df_long_fl)
            _warn_coverage("FLORES", set(df_long_fl["code_epci"].unique()), ref_epcis)

            df_long_fl_ept = aggregate_communes_to_epci(
                df_fl, correspondance_ept, ind_flores, annee, "FLORES", df_secret_communes=df_fl_secret
            )
            all_frames.append(df_long_fl_ept)
            _warn_coverage("FLORES (EPT)", set(df_long_fl_ept["code_epci"].unique()), ref_epts)

            all_frames.append(aggregate_communes_to_epci(
                df_fl, correspondance_paris, ind_flores, annee, "FLORES", df_secret_communes=df_fl_secret
            ))
        except Exception as e:
            echecs.append(_echec(f"FLORES {annee}", e))

    # --- MOBILITE DT (émissions domicile-travail, source officielle INSEE × SDES) ---
    if "mobilite_dt" in sources:
        try:
            from sources.mobilite_dt import extract_indicators as extract_mobilite_dt
            catalogue = load_catalogue()
            ind_mob_dt = [ind["code"] for ind in catalogue if ind.get("source") == "MOBILITE_DT"]
            df_mob_dt = extract_mobilite_dt(annee)
            # Idem FLORES : Paris/Lyon/Marseille publiés par arrondissement.
            df_mob_dt = regrouper_arrondissements_plm(df_mob_dt)
            df_long_mob_dt = aggregate_communes_to_epci(
                df_mob_dt, correspondance, ind_mob_dt, annee, "MOBILITE_DT"
            )
            all_frames.append(df_long_mob_dt)
            _warn_coverage("MOBILITE_DT", set(df_long_mob_dt["code_epci"].unique()), ref_epcis)

            df_long_mob_dt_ept = aggregate_communes_to_epci(
                df_mob_dt, correspondance_ept, ind_mob_dt, annee, "MOBILITE_DT"
            )
            all_frames.append(df_long_mob_dt_ept)
            _warn_coverage("MOBILITE_DT (EPT)", set(df_long_mob_dt_ept["code_epci"].unique()), ref_epts)

            all_frames.append(aggregate_communes_to_epci(
                df_mob_dt, correspondance_paris, ind_mob_dt, annee, "MOBILITE_DT"
            ))
        except Exception as e:
            echecs.append(_echec(f"Mobilité DT {annee}", e))

    # --- PARC VEHICULES (SDES - RSVERO, données communales publiques) ---
    if "parc_vehicules" in sources:
        try:
            from sources.parc_vehicules import extract_indicators as extract_parc
            catalogue = load_catalogue()
            ind_parc = [ind["code"] for ind in catalogue if ind.get("source") == "PARC_VEHICULES"]
            df_parc = extract_parc(annee)
            df_long_parc = aggregate_communes_to_epci(
                df_parc, correspondance, ind_parc, annee, "PARC_VEHICULES"
            )
            all_frames.append(df_long_parc)
            _warn_coverage("PARC_VEHICULES", set(df_long_parc["code_epci"].unique()), ref_epcis)

            df_long_parc_ept = aggregate_communes_to_epci(
                df_parc, correspondance_ept, ind_parc, annee, "PARC_VEHICULES"
            )
            all_frames.append(df_long_parc_ept)
            _warn_coverage("PARC_VEHICULES (EPT)", set(df_long_parc_ept["code_epci"].unique()), ref_epts)

            all_frames.append(aggregate_communes_to_epci(
                df_parc, correspondance_paris, ind_parc, annee, "PARC_VEHICULES"
            ))
        except Exception as e:
            echecs.append(_echec(f"Parc véhicules {annee}", e))

    # --- ÉNERGIE-CLIMAT (données directement EPCI, OREC/BOM) ---
    if "energie_climat" in sources:
        try:
            df_ec         = _df_ec_preload
            df_ec_qualite = _df_ec_qualite_preload
            if df_ec is None:
                from sources.energie_climat import extract_indicators as extract_ec
                df_ec, df_ec_qualite = extract_ec(annee)
            if not df_ec.empty:
                df_ec.index.name = "code_epci"
                df_long_ec = _to_long(df_ec, "code_epci", None, annee, "ENERGIE_CLIMAT")
                # Propager la qualité par valeur (ok / interpolé / extrapolé)
                df_q_long = (
                    df_ec_qualite.reset_index()
                    .melt(id_vars="code_epci", var_name="code_indicateur", value_name="qualite")
                    .dropna(subset=["qualite"])
                )
                df_long_ec = df_long_ec.drop(columns=["qualite"]).merge(
                    df_q_long, on=["code_epci", "code_indicateur"], how="left"
                )
                df_long_ec["qualite"]  = df_long_ec["qualite"].fillna("ok")
                df_long_ec["nom_epci"] = df_long_ec["code_epci"].map(epci_names).fillna("")
                all_frames.append(df_long_ec)
                _warn_coverage("ENERGIE_CLIMAT", set(df_long_ec["code_epci"].unique()), ref_epcis)
        except Exception as e:
            echecs.append(_echec(f"Énergie-climat {annee}", e))

    # --- CITEPA IGT (repli aux données OREC sur les zones sans observatoire) ---
    # Alimente les mêmes codes EC_EMIS_* que l'OREC, restreint aux EPCI des
    # zones listées dans IGT_ZONES et aux couples (EPCI, indicateur) que l'OREC
    # ne couvre pas : l'IGT complète, il ne remplace jamais une donnée OREC.
    if "citepa_igt" in sources:
        try:
            from config.settings import IGT_ZONES
            from sources.citepa_igt import (
                IGT_ANNEE, IGT_CODES, PERIMETRES_DROM,
                extract_indicators as extract_igt,
            )

            zone_lookup = zone_geo_lookup(epci_ref).set_index("code_epci")["zone"]
            epci_cibles = set(zone_lookup[zone_lookup.isin(IGT_ZONES)].index)
            if not epci_cibles:
                log.warning(f"CITEPA IGT : aucun EPCI dans les zones {sorted(IGT_ZONES)}")
            else:
                from config.settings import ZONES_DROM
                perimetres = PERIMETRES_DROM if IGT_ZONES <= set(ZONES_DROM) else None
                df_igt = extract_igt(perimetres)
                qualite = "ok" if IGT_ANNEE == annee else f"millesime_igt_{IGT_ANNEE}"
                if qualite != "ok":
                    log.warning(
                        f"CITEPA IGT : millésime {IGT_ANNEE} reporté sur l'année {annee} "
                        f"(le recalage est ancré sur l'année de référence)"
                    )
                # Périmètre restreint aux EPCI cibles : la source IGT n'est
                # chargée que pour ces zones, donc la couverture doit être
                # mesurée contre leurs communes et non contre la France entière.
                correspondance_igt = correspondance[correspondance["code_epci"].isin(epci_cibles)]
                df_long_igt = aggregate_communes_to_epci(
                    df_igt, correspondance_igt, IGT_CODES, annee, "CITEPA_IGT", qualite
                )

                # Garde-fou : ne jamais écraser une valeur déjà fournie par l'OREC
                if all_frames:
                    deja = pd.concat(
                        [f[["code_epci", "code_indicateur"]] for f in all_frames],
                        ignore_index=True,
                    )
                    deja = set(map(tuple, deja.drop_duplicates().to_numpy()))
                    collision = df_long_igt.apply(
                        lambda r: (r["code_epci"], r["code_indicateur"]) in deja, axis=1
                    ) if not df_long_igt.empty else pd.Series(dtype=bool)
                    if collision.any():
                        log.warning(
                            f"CITEPA IGT : {int(collision.sum())} couples (EPCI, indicateur) "
                            f"déjà couverts par une autre source → écartés"
                        )
                        df_long_igt = df_long_igt[~collision]

                if not df_long_igt.empty:
                    all_frames.append(df_long_igt)
                    log.info(
                        f"CITEPA IGT : {df_long_igt['code_epci'].nunique()} EPCI "
                        f"(zones {sorted(IGT_ZONES)}), {len(df_long_igt)} lignes"
                    )
        except Exception as e:
            echecs.append(_echec(f"CITEPA IGT {annee}", e))

    # --- AGRESTE (données communales RA 2020, agrégées par EPCI) ---
    if "agreste" in sources:
        try:
            from sources.agreste import load_ra_epci, _load_communes as _load_agreste_communes
            annee_ra = 2020
            df_agr = load_ra_epci(annee_ra)
            df_agr.index.name = "code_epci"
            df_long_agr = _to_long(df_agr, "code_epci", None, annee_ra, "AGRESTE_RA")
            df_long_agr["nom_epci"] = df_long_agr["code_epci"].map(epci_names).fillna("")
            all_frames.append(df_long_agr)
            _warn_coverage("AGRESTE_RA", set(df_long_agr["code_epci"].unique()), ref_epcis)

            # EPT : ré-agrégation directe depuis les mêmes données communales
            # RPG/RA (load_ra_epci ne fait l'agrégation qu'au niveau EPCI).
            catalogue = load_catalogue()
            ind_agr = [ind["code"] for ind in catalogue if ind.get("source") == "AGRESTE_RA"]
            df_agr_com = _load_agreste_communes().set_index("codgeo")
            df_long_agr_ept = aggregate_communes_to_epci(
                df_agr_com, correspondance_ept, ind_agr, annee_ra, "AGRESTE_RA"
            )
            all_frames.append(df_long_agr_ept)
            _warn_coverage("AGRESTE_RA (EPT)", set(df_long_agr_ept["code_epci"].unique()), ref_epts)

            all_frames.append(aggregate_communes_to_epci(
                df_agr_com, correspondance_paris, ind_agr, annee_ra, "AGRESTE_RA"
            ))
        except Exception as e:
            echecs.append(_echec("Agreste RA", e))

    # --- ALDO (données communales UTCATF, statiques comme le RA) ---
    if "aldo" in sources:
        try:
            from sources.aldo import extract_indicators as extract_aldo, ALDO_CODES
            df_aldo = extract_aldo()
            df_long_aldo = aggregate_communes_to_epci(
                df_aldo, correspondance, ALDO_CODES, annee, "ALDO"
            )
            all_frames.append(df_long_aldo)
            _warn_coverage("ALDO", set(df_long_aldo["code_epci"].unique()), ref_epcis)

            df_long_aldo_ept = aggregate_communes_to_epci(
                df_aldo, correspondance_ept, ALDO_CODES, annee, "ALDO"
            )
            all_frames.append(df_long_aldo_ept)
            _warn_coverage("ALDO (EPT)", set(df_long_aldo_ept["code_epci"].unique()), ref_epts)

            all_frames.append(aggregate_communes_to_epci(
                df_aldo, correspondance_paris, ALDO_CODES, annee, "ALDO"
            ))
        except Exception as e:
            echecs.append(_echec("ALDO UTCATF", e))

    # --- OMPHALE (données directement EPCI, + EPT/Paris par réutilisation du
    # même mécanisme de pondération départementale) ---
    # Les projections couvrent [2020, 2025, …, 2070] ; on les inclut toutes
    # en une seule passe, quel que soit l'annee demandé pour le RP.
    if "omphale" in sources:
        try:
            from sources.insee_omphale import load_omphale
            # Pondération territoire→département par population communale de référence
            pop_communes = None
            if df_rp is not None and "POP" in df_rp.columns:
                pop_communes = (
                    df_rp[["POP"]]
                    .reset_index()
                    .rename(columns={"CODGEO": "codgeo", "POP": "population"})
                )

            noms_infra = {
                **correspondance_ept_brute.drop_duplicates("code_ept").set_index("code_ept")["nom_ept"].to_dict(),
                CODE_COMMUNE_PARIS: NOM_COMMUNE_PARIS,
            }

            def _melt_omphale(df_om, noms):
                om_value_cols = [c for c in ("POP_PROJ", "POP_PROJ_RATIO") if c in df_om.columns]
                df_long = df_om.melt(
                    id_vars=["code_epci", "annee"],
                    value_vars=om_value_cols,
                    var_name="code_indicateur", value_name="valeur"
                )
                df_long["nom_epci"]        = df_long["code_epci"].map(noms).fillna("")
                df_long["source"]          = "INSEE_OMPHALE"
                df_long["date_extraction"] = str(date.today())
                df_long["qualite"]         = "ok"
                return df_long[["code_epci", "nom_epci", "code_indicateur",
                                 "annee", "valeur", "source", "date_extraction", "qualite"]]

            df_om = load_omphale(pop_communes=pop_communes, annee_ref=annee)
            if not df_om.empty:
                all_frames.append(_melt_omphale(df_om, epci_names))
                log.info(f"Omphale : {df_om['annee'].nunique()} années × {df_om['code_epci'].nunique()} EPCI")
                _warn_coverage("INSEE_OMPHALE", set(df_om["code_epci"].unique()), ref_epcis)

            df_om_ept = load_omphale(
                pop_communes=pop_communes, annee_ref=annee,
                correspondance=correspondance_ept, cache_suffix="ept",
            )
            if not df_om_ept.empty:
                all_frames.append(_melt_omphale(df_om_ept, noms_infra))
                _warn_coverage("INSEE_OMPHALE (EPT)", set(df_om_ept["code_epci"].unique()), ref_epts)

            df_om_paris = load_omphale(
                pop_communes=pop_communes, annee_ref=annee,
                correspondance=correspondance_paris, cache_suffix="paris",
            )
            if not df_om_paris.empty:
                all_frames.append(_melt_omphale(df_om_paris, noms_infra))
        except Exception as e:
            echecs.append(_echec("Omphale", e))

    # --- DJU (résultats déjà en format large EPCI) ---
    if "dju" in sources:
        try:
            dju_cache = INTERIM_DIR / f"dju_epci_{annee}.parquet"
            if dju_cache.exists():
                df_dju_wide = pd.read_parquet(dju_cache)
                dju_ind_map = {
                    "dju_chaud_meteo":  "DJU_CHAUD_METEO",
                    "dju_froid_meteo":  "DJU_FROID_METEO",
                    "dju_chaud_costic": "DJU_CHAUD_COSTIC",
                    "dju_froid_costic": "DJU_FROID_COSTIC",
                }
                df_dju_wide = df_dju_wide.rename(columns=dju_ind_map)
                dju_ind_cols = [v for v in dju_ind_map.values() if v in df_dju_wide.columns]
                df_long_dju = _to_long(
                    df_dju_wide[["code_epci", "nom_epci"] + dju_ind_cols],
                    "code_epci", "nom_epci", annee, "METEO_FRANCE", "interpolé"
                )
                all_frames.append(df_long_dju)
                _warn_coverage("DJU", set(df_long_dju["code_epci"].unique()), ref_epcis)
            else:
                log.warning(f"DJU {annee}: cache absent ({dju_cache.name}). Lancer main.py dju --annee {annee}")
        except Exception as e:
            echecs.append(_echec(f"DJU {annee}", e))

    if echecs:
        raise SourceEnEchec(
            f"BDD {annee} non produite, {len(echecs)} source(s) en échec :\n  - "
            + "\n  - ".join(echecs)
        )

    if not all_frames:
        raise RuntimeError(f"Aucune donnée disponible pour l'année {annee}")

    df_all = pd.concat(all_frames, ignore_index=True)

    # Indicateurs dérivés (calcul_derive)
    df_derives = compute_derived_indicators(df_all)
    if not df_derives.empty:
        df_all = pd.concat([df_all, df_derives], ignore_index=True)

    # Enrichissement unités
    df_all = add_unite(df_all)

    # Niveau géographique (EPCI / EPT / COMMUNE) : fait une fois sur
    # l'ensemble - une ligne est repérée par son code, peu importe qu'elle
    # vienne d'une source brute ou d'un indicateur dérivé calculé plus haut.
    ref_communes = {CODE_COMMUNE_PARIS}
    df_all["niveau_geo"] = np.select(
        [df_all["code_epci"].isin(ref_epts), df_all["code_epci"].isin(ref_communes)],
        [NIVEAU_GEO_EPT, NIVEAU_GEO_COMMUNE],
        default=NIVEAU_GEO_EPCI,
    )

    # Restriction à la liste de référence (EPCI, EPT ou commune selon niveau_geo)
    n_avant = df_all["code_epci"].nunique()
    mask_valide = (
        ((df_all["niveau_geo"] == NIVEAU_GEO_EPCI) & df_all["code_epci"].isin(ref_epcis))
        | ((df_all["niveau_geo"] == NIVEAU_GEO_EPT) & df_all["code_epci"].isin(ref_epts))
        | ((df_all["niveau_geo"] == NIVEAU_GEO_COMMUNE) & df_all["code_epci"].isin(ref_communes))
    )
    df_all = df_all[mask_valide]
    n_apres = df_all["code_epci"].nunique()
    if n_avant != n_apres:
        log.info(
            f"Filtrage liste de référence : {n_avant - n_apres} entités exclues "
            f"→ {n_apres} entités conservées (EPCI + EPT + Paris)"
        )

    # Totaux France / zones / groupes de zones
    log.info("Calcul des totaux France, zones et groupes de zones...")
    try:
        # Population de référence pour la pondération des moyennes
        df_pop_ref = df_all[
            (df_all["code_indicateur"] == "POP") & (df_all["annee"] == annee)
        ][["code_epci", "valeur"]].rename(columns={"valeur": "POP_REF"})

        # Zone de chaque EPCI - détection via codes_regions (load_epci_centroids)
        zone_par_epci = (
            zone_geo_lookup(epci_ref).set_index("code_epci")["zone"].to_dict()
        )

        df_totaux = compute_totaux(df_all, zone_par_epci, df_pop_ref)
        df_all = pd.concat([df_all, df_totaux], ignore_index=True)
    except Exception as e:
        raise RuntimeError(f"Erreur calcul totaux : {e}") from e

    # Enrichissement géographique : zone, code_region, code_departement
    geo_lookup_paris = pd.DataFrame([{
        "code_epci": CODE_COMMUNE_PARIS, "zone": "HEXAGONE",
        "code_region": "11", "code_departement": "75",
    }])
    geo_lookup = pd.concat(
        [zone_geo_lookup(epci_ref), ept_geo_lookup(correspondance_ept_brute), geo_lookup_paris],
        ignore_index=True,
    )
    df_all = df_all.merge(geo_lookup, on="code_epci", how="left")

    # Contrôle de conservation EPT : Σ(11 EPT) + Paris doit égaler exactement
    # la valeur MGP à couverture complète (non bloquant, log.warning sinon).
    check_conservation_ept(df_all)

    # Ordre des colonnes
    cols = ["code_epci", "nom_epci", "niveau_geo", "zone", "code_region", "code_departement",
            "code_indicateur", "annee", "valeur", "unite", "source", "date_extraction", "qualite"]
    df_all = df_all[[c for c in cols if c in df_all.columns]]

    n_epci    = (df_all["niveau_geo"] == NIVEAU_GEO_EPCI).sum()
    n_ept     = (df_all["niveau_geo"] == NIVEAU_GEO_EPT).sum()
    n_commune = (df_all["niveau_geo"] == NIVEAU_GEO_COMMUNE).sum()
    log.info(
        f"BDD long {annee}: {df_all['code_epci'].nunique()} entités, {len(df_all)} lignes "
        f"({n_epci} lignes EPCI, {n_ept} lignes EPT, {n_commune} lignes commune)"
    )
    return df_all
