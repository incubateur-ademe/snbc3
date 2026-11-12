"""
pipeline/overrides.py
======================
Application d'overrides utilisateur (plateforme TeT) sur les données pivot
d'un territoire, avant territorialisation.

Un override remplace ou ajoute une ligne (code_epci, code_indicateur, annee)
de la BDD long. Comme `compute_totaux()` (pipeline.aggregate) recalcule les
lignes agrégées (FRANCE, zones, groupes) par somme/moyenne pondérée sur
l'ensemble des lignes EPCI, changer la valeur d'un seul territoire modifie
légèrement le dénominateur de zone utilisé par `territorialize()` - ces
lignes de total sont donc systématiquement recalculées après application des
overrides, jamais réutilisées telles quelles.

Portée volontairement restreinte à un seul territoire par appel (cf. api/README.md) : un territoire qui saisirait une valeur aberrante ne doit jamais
pouvoir perturber les résultats des autres territoires de la même zone.
"""

import logging

import pandas as pd

from config.settings import CODES_TOTAUX as TOTAL_CODES
from pipeline.aggregate import compute_totaux, load_catalogue

log = logging.getLogger(__name__)

OVERRIDE_SOURCE = "OVERRIDE_TET"
QUALITE_OVERRIDE = "override_tet"


class OverrideError(ValueError):
    """Levée quand un override est invalide (indicateur inconnu, territoire absent)."""


def apply_overrides(
    df_long: pd.DataFrame,
    code_epci: str,
    overrides: list[dict],
    ref_year: int,
) -> pd.DataFrame:
    """
    Applique une liste d'overrides `{code_indicateur, annee, valeur}` aux
    données pivot d'un seul territoire, puis rafraîchit les totaux de zone.

    Ne modifie jamais `df_long` en place (retourne une copie). Si `overrides`
    est vide, retourne `df_long` tel quel (pas de copie ni de recalcul
    inutile).

    Lève `OverrideError` si un `code_indicateur` est inconnu du catalogue
    (config/indicateurs.yaml) ou si `code_epci` n'a aucune ligne dans
    `df_long` (territoire non résolu en amont - cf. pipeline.territoire_resolver).
    """
    if not overrides:
        return df_long

    catalogue_codes = {ind["code"] for ind in load_catalogue()}
    demandes = {o["code_indicateur"] for o in overrides}
    inconnus = demandes - catalogue_codes
    if inconnus:
        raise OverrideError(
            f"code(s) indicateur inconnu(s) du catalogue : {sorted(inconnus)}"
        )

    territoire_rows = df_long[df_long["code_epci"] == code_epci]
    if territoire_rows.empty:
        raise OverrideError(f"territoire {code_epci!r} absent des données pivot")
    meta = territoire_rows.iloc[0]

    df = df_long.copy()
    nouvelles_lignes = []
    for o in overrides:
        mask = (
            (df["code_epci"] == code_epci)
            & (df["code_indicateur"] == o["code_indicateur"])
            & (df["annee"] == o["annee"])
        )
        if mask.any():
            df.loc[mask, "valeur"] = o["valeur"]
            df.loc[mask, "source"] = OVERRIDE_SOURCE
            df.loc[mask, "qualite"] = QUALITE_OVERRIDE
        else:
            nouvelles_lignes.append({
                "code_epci": code_epci,
                "nom_epci": meta.get("nom_epci"),
                "niveau_geo": meta.get("niveau_geo", "EPCI"),
                "zone": meta.get("zone"),
                "code_region": meta.get("code_region"),
                "code_departement": meta.get("code_departement"),
                "code_indicateur": o["code_indicateur"],
                "annee": o["annee"],
                "valeur": o["valeur"],
                "unite": "",
                "source": OVERRIDE_SOURCE,
                "date_extraction": pd.Timestamp.now().date().isoformat(),
                "qualite": QUALITE_OVERRIDE,
            })
    if nouvelles_lignes:
        df = pd.concat([df, pd.DataFrame(nouvelles_lignes)], ignore_index=True)

    return _refresh_totaux(df, ref_year, demandes)


def _refresh_totaux(df: pd.DataFrame, ref_year: int, indicateurs_touches: set[str]) -> pd.DataFrame:
    """
    Recalcule les lignes agrégées (léger, pandas pur, sans
    réseau) affectées par l'override - reproduit l'appel fait par
    `pipeline.aggregate.build_bdd()` pour rester cohérent avec le batch, mais
    restreint aux seuls indicateurs dont le total peut avoir changé, pas tout
    le catalogue (~113k lignes contre quelques milliers) : c'était, avec le
    calcul complet de `territorialize()`, le second principal poste de coût
    d'un appel à la demande avec override.

    Un indicateur est affecté si (a) il fait partie des overrides, ou (b) il
    est calculé en moyenne pondérée par population et POP a été overridé à
    l'année de référence (sa pondération change alors, même si sa propre
    valeur n'a pas été touchée). `compute_totaux()` restreint déjà en
    interne aux lignes `niveau_geo == "EPCI"` : inutile de filtrer
    l'EPT/Paris ici.
    """
    df_sans_totaux = df[~df["code_epci"].isin(TOTAL_CODES)]

    catalogue = load_catalogue()
    pond_codes = {
        ind["code"] for ind in catalogue if ind.get("methode") == "moyenne_pond_pop"
    }
    a_recalculer = set(indicateurs_touches)
    if "POP" in indicateurs_touches:
        a_recalculer |= pond_codes

    epci = df_sans_totaux[
        (df_sans_totaux.get("niveau_geo", "EPCI") == "EPCI")
        & df_sans_totaux["zone"].notna()
    ]
    zone_par_epci = (
        epci.drop_duplicates("code_epci").set_index("code_epci")["zone"].to_dict()
    )
    pop_ref = (
        df_sans_totaux[
            (df_sans_totaux["code_indicateur"] == "POP")
            & (df_sans_totaux["annee"] == ref_year)
        ][["code_epci", "valeur"]]
        .rename(columns={"valeur": "POP_REF"})
    )

    df_pour_totaux = df_sans_totaux[df_sans_totaux["code_indicateur"].isin(a_recalculer)]
    nouveaux_totaux = compute_totaux(df_pour_totaux, zone_par_epci=zone_par_epci, pop_ref=pop_ref)

    anciens_totaux_conserves = df[
        df["code_epci"].isin(TOTAL_CODES) & ~df["code_indicateur"].isin(a_recalculer)
    ]
    return pd.concat(
        [df_sans_totaux, anciens_totaux_conserves, nouveaux_totaux], ignore_index=True
    )
