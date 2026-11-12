"""
pipeline/territoire_resolver.py
================================
Résout un identifiant de territoire transmis par un appelant externe (code
EPCI, code EPT, ou code commune) vers un `code_epci` reconnu par
`pipeline.territorialize.territorialize()`.

Depuis la gestion des subdivisions métropolitaines (EPT de la Métropole du
Grand Paris, Paris), `code_epci` porte aussi bien de vraies EPCI que des EPT
ou la commune de Paris : tous sont territorialisés à l'identique (cf.
pipeline/territorialize.py, en-tête du module). Un EPT ou Paris se résout
donc directement, sans logique de désagrégation particulière.

Ordre de résolution :
  1. `code` est déjà une valeur de `code_epci` dans les données pivot (EPCI,
     EPT ou Paris) -> résolution directe.
  2. `code` est un code commune de la Métropole du Grand Paris -> résolu
     vers son EPT (plus fin que l'EPCI parent, la MGP).
  3. `code` est un code commune quelconque -> résolu vers son EPCI parent.
  4. Sinon -> non résolu (statut explicite, jamais de résultat silencieux).

Les EPCI sont ceux du millésime Insee le plus récent (cf. sources/geo_epci.py) :
un code d'EPCI disparu dans une fusion tombe en "non_resolu" plutôt que de
retourner un résultat pour le mauvais périmètre. Un code commune se résout
d'après le rattachement de ce millésime.
"""

import logging

import pandas as pd

from config.settings import CODES_TOTAUX as TOTAL_CODES

log = logging.getLogger(__name__)

METHODE_DIRECT = "direct"
METHODE_COMMUNE_VERS_EPT = "commune_vers_ept"
METHODE_COMMUNE_VERS_EPCI = "commune_vers_epci"
METHODE_NON_RESOLU = "non_resolu"


def _resolution(code_epci, zone, niveau_geo, methode, resolu):
    return {
        "code_epci": code_epci,
        "zone": zone,
        "niveau_geo": niveau_geo,
        "methode_resolution": methode,
        "resolu": resolu,
    }


def resolve_territoire(code: str, df_long: pd.DataFrame) -> dict:
    """
    Résout `code` vers un territoire des données pivot `df_long`.

    Retourne un dict `{code_epci, zone, niveau_geo, methode_resolution,
    resolu}`. Quand `resolu` est False, les autres champs valent None - à
    l'appelant (API) de traduire ça en 404 plutôt que de propager un
    territoire indéterminé dans un calcul.
    """
    code = str(code).strip()

    territoires = df_long[~df_long["code_epci"].astype(str).isin(TOTAL_CODES)]
    match = territoires[territoires["code_epci"].astype(str) == code]
    if not match.empty:
        row = match.iloc[0]
        return _resolution(
            code, row.get("zone"), row.get("niveau_geo", "EPCI"), METHODE_DIRECT, True
        )

    from sources.geo_epci import load_commune_ept_cog, load_commune_epci_cog

    try:
        corr_ept = load_commune_ept_cog()
    except Exception as e:
        log.warning(f"Correspondance commune->EPT indisponible ({e}) - étape ignorée")
        corr_ept = pd.DataFrame(columns=["codgeo", "code_ept"])
    match_ept = corr_ept[corr_ept["codgeo"].astype(str) == code]
    if not match_ept.empty:
        code_ept = str(match_ept.iloc[0]["code_ept"])
        row = territoires[territoires["code_epci"].astype(str) == code_ept]
        if not row.empty:
            r = row.iloc[0]
            return _resolution(
                code_ept, r.get("zone"), r.get("niveau_geo", "EPT"),
                METHODE_COMMUNE_VERS_EPT, True,
            )

    try:
        corr_epci = load_commune_epci_cog()
    except Exception as e:
        log.warning(f"Correspondance commune->EPCI indisponible ({e}) - étape ignorée")
        corr_epci = pd.DataFrame(columns=["codgeo", "code_epci"])
    match_epci = corr_epci[corr_epci["codgeo"].astype(str) == code]
    if not match_epci.empty:
        code_parent = str(match_epci.iloc[0]["code_epci"])
        row = territoires[territoires["code_epci"].astype(str) == code_parent]
        if not row.empty:
            r = row.iloc[0]
            return _resolution(
                code_parent, r.get("zone"), r.get("niveau_geo", "EPCI"),
                METHODE_COMMUNE_VERS_EPCI, True,
            )

    return _resolution(None, None, None, METHODE_NON_RESOLU, False)
