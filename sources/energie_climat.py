"""
sources/energie_climat.py
=========================
Extraction des consommations d'énergie finale et des émissions de GES par
secteur et par EPCI, depuis le bouquet d'indicateurs communs OREC publié sur
data.gouv.fr par le réseau RARE.

Source :
  Dataset : https://www.data.gouv.fr/datasets/bouquet-dindicateurs-communs-energie-climat-des-orec
  Fichiers : consommation-secteur et emissions-ges-secteur (CSV, approche cadastrale Scope 1&2)
  Les URLs sont des raccourcis stables qui redirigent vers la dernière version.

Indicateurs extraits :
  Consommations d'énergie finale (GWh) :
    EC_CONSO_RESIDENTIEL        - résidentiel         (ID 0201.0101)
    EC_CONSO_TERTIAIRE          - tertiaire            (ID 0201.0102)
    EC_CONSO_INDUSTRIE          - industrie            (ID 0201.0107)
    EC_CONSO_AGRICULTURE        - agriculture          (ID 0201.0103)
    EC_CONSO_TRANSPORT_ROUTIER  - transport routier    (ID 0201.0105)
    EC_CONSO_AUTRE_TRANSPORT    - autres transports    (ID 0201.0106)
    EC_CONSO_DECHETS            - déchets              (ID 0201.0110)
    EC_CONSO_TOTAL              - tous secteurs        (ID 0201.0100)

  Émissions de GES (kteq CO2) :
    EC_EMIS_RESIDENTIEL         - résidentiel                  (ID 0101.0101)
    EC_EMIS_TERTIAIRE           - tertiaire                    (ID 0101.0102)
    EC_EMIS_INDUSTRIE_HORS_BE   - industrie hors branche énergie (ID 0101.0108)
                                  repli : 0101.0107 (industrie totale) si absent
    EC_EMIS_BRANCHE_ENERGIE     - industrie branche énergie    (ID 0101.0109)
    EC_EMIS_AGRICULTURE         - agriculture          (ID 0101.0103)
    EC_EMIS_TRANSPORT_ROUTIER   - transport routier    (ID 0101.0105)
    EC_EMIS_AUTRE_TRANSPORT     - autres transports    (ID 0101.0106)
    EC_EMIS_DECHETS             - déchets              (ID 0101.0110)
    EC_EMIS_TOTAL               - tous secteurs        (ID 0101.0100)

Format de sortie :
  DataFrame indexé sur code_epci (str, 9 chiffres),
  colonnes = codes indicateurs listés ci-dessus.
  Données déjà à l'échelle EPCI (pas d'agrégation commune→EPCI).

Note secret statistique :
  Les CSV OREC portent une colonne "confidentiel" (valeur "1" quand la donnée
  est masquée pour raison de confidentialité - typiquement un secteur avec un
  établissement dominant sur l'EPCI). Dans ce cas la colonne "valeur" est
  vide dans le fichier source. Ces lignes sont conservées (valeur=NaN) et
  tracées avec qualite="secret_stat" dans la BDD finale, plutôt que
  silencieusement supprimées par le dropna sur "valeur".

Note EPCI à cheval sur deux régions :
  Certains EPCI (26 au millésime 2026) relèvent de deux OREC, qui produisent
  leurs données sans coordination (cf. fiche de métadonnées du bouquet).
  Selon l'année, le CSV porte soit le total sur les deux régions
  (code_region composé, "84-93", colonne "completude" = 1), soit la seule
  part située dans une région (code_region simple, completude = 0) : en 2021
  cinq EPCI n'avaient que 0,5 % à 37 % de leur total. Pour ces EPCI seules les
  lignes complètes sont retenues ; à défaut pour l'année demandée la valeur
  est interpolée entre les années complètes encadrantes (qualité
  "interpolé_cheval"), ou reprise de l'année complète la plus proche si l'une
  des deux bornes manque ("extrapolé_cheval"). Un EPCI sans aucune ligne
  complète (Maine Saosnois) garde sa valeur, tracée "cheval_non_verifiable".

Note zéros non publiés :
  L'OREC Corse publie 0, au lieu d'une valeur vide, pour le résidentiel
  (depuis 2021) et le transport routier (depuis 2020), sur tous ses EPCI.
  Ces deux secteurs ne peuvent pas être nuls sur un EPCI : un 0 y est traité
  comme une valeur non publiée, et la règle de repli s'applique (année
  publiée la plus proche, qualité "extrapolé"). Le total OREC (0101.0100,
  0201.0100) de ces EPCI est la somme des secteurs publiés pour l'année, donc
  sans les secteurs à 0 : il est remplacé par la somme des secteurs retenus
  (qualité "somme_secteurs"), sans quoi les parts sectorielles dépasseraient
  100 %. L'OREC Corse ne publie par ailleurs jamais la branche énergie ni les
  déchets : ces secteurs restent absents, du total comme des secteurs.

Note substitutions (_SUBSTITUTIONS) :
  Le bouquet v2.5 (22/10/2026) intègre l'inventaire Atmo Grand Est V2026
  (millésime 2026-08), dont les émissions sont limitées aux émissions
  énergétiques : en 2021, agriculture 7,7 -> 1,0 MtCO2e (somme des seuls
  vecteurs énergétiques), déchets absents sur toute la période, branche
  énergie 0,22 -> 0,03, industrie -21 %, total 41,1 -> 30,0 MtCO2e, contre
  41,9 pour l'IGT CITEPA. Les consommations ne changent pas. Dans l'attente
  d'une correction par RARE / Atmo Grand Est, les émissions (0101.*) de la
  région 44, EPCI à cheval compris (code région composé), sont reprises du
  bouquet v2.4 (data/input/orec_grand_est_emissions_v2.4.csv, extrait du
  cache brut du 21/10/2026). Le journal compare à chaque calcul les totaux
  publié et retenu : à retirer dès que le bouquet republie le périmètre complet.

Note EPCI fusionnés :
  Le bouquet peut porter les codes d'EPCI disparus au 1er janvier, quand les
  OREC publient sur le découpage de l'année précédente (en 2026 : CC de
  Lévézou Pareloup et CC du Pays de Salars, réunies dans la CC du Lévézou).
  Le nouvel EPCI est recomposé par somme des EPCI qu'il réunit (toutes les
  variables du bouquet sont des volumes), indicateur par indicateur et
  seulement si tous sont renseignés ; qualité "fusion_epci". Les fusions sont
  détectées dans les fichiers Insee (sources/geo_epci.load_fusions_epci). Une
  commune qui change d'EPCI sans fusion reste comptée dans son ancien EPCI :
  le bouquet ne descend pas à la commune.
"""

import io
import logging
import time

import numpy as np
import pandas as pd
import requests

from config.settings import INPUT_DIR, INTERIM_DIR

log = logging.getLogger(__name__)

SOURCE_META = {
    "id": "ENERGIE_CLIMAT",
    "nom": "OREC - Bouquet indicateurs communs energie-climat",
    "description": (
        "Consommations d'energie finale et emissions de GES par secteur et "
        "par EPCI, depuis le bouquet d'indicateurs communs OREC publie sur "
        "data.gouv.fr par le reseau RARE."
    ),
    "urls": "https://www.data.gouv.fr/datasets/bouquet-dindicateurs-communs-energie-climat-des-orec",
    "niveau_geo": "EPCI (donnees directes)",
    "annees": "2005 - derniere annee disponible",
    "format": "CSV (API data.gouv.fr)",
    "acces": "Automatique (telechargement)",
    "frequence_maj": "Annuelle",
    "licence": "Etalab 2.0",
    "theme": "Energie / Climat",
    "notes": (
        "Approche cadastrale Scope 1&2. Repli industrie totale si industrie hors branche energie absente. "
        "EPCI a cheval sur deux regions : les deux OREC publient sans coordination, soit le total (code_region "
        "compose, completude=1), soit la seule part d'une region ; seuls les totaux sont retenus, interpoles "
        "entre annees publiees si besoin (qualite interpole_cheval / extrapole_cheval / cheval_non_verifiable). "
        "OREC Corse : 0 publie pour le residentiel et le transport routier, traite comme non publie ; le total "
        "est alors la somme des secteurs (qualite somme_secteurs). "
        "Emissions Grand Est (region 44) reprises du bouquet v2.4 : la v2.5 (22/10/2026) ne porte que les "
        "emissions energetiques (data/input/orec_grand_est_emissions_v2.4.csv). "
        "EPCI issu d'une fusion au 1er janvier publie sous les anciens codes : recompose par somme "
        "(qualite fusion_epci)."
    ),
    "indicateurs": [
        {"code": "EC_CONSO_RESIDENTIEL",       "label": "Conso. energie finale - residentiel",             "unite": "GWh", "methode_agregation": "Somme", "annees": "2005-2022"},
        {"code": "EC_CONSO_TERTIAIRE",         "label": "Conso. energie finale - tertiaire",               "unite": "GWh", "methode_agregation": "Somme", "annees": "2005-2022"},
        {"code": "EC_CONSO_INDUSTRIE",         "label": "Conso. energie finale - industrie",               "unite": "GWh", "methode_agregation": "Somme", "annees": "2005-2022"},
        {"code": "EC_CONSO_AGRICULTURE",       "label": "Conso. energie finale - agriculture",             "unite": "GWh", "methode_agregation": "Somme", "annees": "2005-2022"},
        {"code": "EC_CONSO_TRANSPORT_ROUTIER", "label": "Conso. energie finale - transport routier",       "unite": "GWh", "methode_agregation": "Somme", "annees": "2005-2022"},
        {"code": "EC_CONSO_AUTRE_TRANSPORT",   "label": "Conso. energie finale - autres transports",       "unite": "GWh", "methode_agregation": "Somme", "annees": "2005-2022"},
        {"code": "EC_CONSO_DECHETS",           "label": "Conso. energie finale - dechets",                 "unite": "GWh", "methode_agregation": "Somme", "annees": "2005-2022"},
        {"code": "EC_CONSO_TOTAL",             "label": "Conso. energie finale - total",                   "unite": "GWh", "methode_agregation": "Somme", "annees": "2005-2022"},
        {"code": "EC_EMIS_RESIDENTIEL",        "label": "Emissions GES - residentiel",                     "unite": "kteq CO2", "methode_agregation": "Somme", "annees": "2005-2022"},
        {"code": "EC_EMIS_TERTIAIRE",          "label": "Emissions GES - tertiaire",                       "unite": "kteq CO2", "methode_agregation": "Somme", "annees": "2005-2022"},
        {"code": "EC_EMIS_INDUSTRIE_HORS_BE",  "label": "Emissions GES - industrie hors branche energie",  "unite": "kteq CO2", "methode_agregation": "Somme", "annees": "2005-2022"},
        {"code": "EC_EMIS_BRANCHE_ENERGIE",    "label": "Emissions GES - branche energie",                 "unite": "kteq CO2", "methode_agregation": "Somme", "annees": "2005-2022"},
        {"code": "EC_EMIS_AGRICULTURE",        "label": "Emissions GES - agriculture",                     "unite": "kteq CO2", "methode_agregation": "Somme", "annees": "2005-2022"},
        {"code": "EC_EMIS_TRANSPORT_ROUTIER",  "label": "Emissions GES - transport routier",               "unite": "kteq CO2", "methode_agregation": "Somme", "annees": "2005-2022"},
        {"code": "EC_EMIS_AUTRE_TRANSPORT",    "label": "Emissions GES - autres transports",               "unite": "kteq CO2", "methode_agregation": "Somme", "annees": "2005-2022"},
        {"code": "EC_EMIS_DECHETS",            "label": "Emissions GES - dechets",                         "unite": "kteq CO2", "methode_agregation": "Somme", "annees": "2005-2022"},
        {"code": "EC_EMIS_TOTAL",              "label": "Emissions GES - total",                           "unite": "kteq CO2", "methode_agregation": "Somme", "annees": "2005-2022"},
    ],
}

# ---------------------------------------------------------------------------
# URLs data.gouv.fr (raccourcis stables → redirigent vers la dernière version)
# ---------------------------------------------------------------------------
_URL_CONSO_SECTEUR = "https://www.data.gouv.fr/api/1/datasets/r/ad9b1fce-7d2e-4cdf-a8ab-cfec6da113a8"
_URL_EMIS_SECTEUR  = "https://www.data.gouv.fr/api/1/datasets/r/255330e8-3001-4f02-b2b9-f3df0ef7f0b8"

_RAW_CACHE_PATH     = INTERIM_DIR / "energie_climat_raw.parquet"
# Expiration mensuelle : le bouquet est republié plusieurs fois par an (v2.0 à
# v2.5 en 2026), et les runs sans cache prennent toujours la dernière version.
_RAW_CACHE_TTL_DAYS = 30
# Cache brut précédent, conservé quand un retéléchargement change le contenu.
_RAW_CACHE_PRECEDENT_PATH = INTERIM_DIR / "energie_climat_raw_precedent.parquet"
_RAW_COLONNES = ["code_epci", "annee", "id_orec", "valeur", "confidentiel",
                 "code_region", "completude"]

# Version du format des caches par année (energie_climat_{annee}_v*.parquet) :
# à incrémenter quand la règle de sélection des valeurs change, pour ne pas
# resservir un cache calculé avec l'ancienne.
_CACHE_VERSION = 6

# Substitutions de données publiées jugées erronées (cf. docstring du module) :
# les lignes du bouquet dont le code région (simple ou composé) contient
# `region` et dont l'ID commence par `prefixe` sont remplacées par celles de
# `fichier` (colonnes _RAW_COLONNES, versionné dans data/input).
_SUBSTITUTIONS: list[dict] = [
    {
        "region": "44",
        "prefixe": "0101.",
        "fichier": INPUT_DIR / "orec_grand_est_emissions_v2.4.csv",
        "motif": (
            "bouquet v2.5 (22/10/2026, Invent'Air V2026) : émissions Grand Est "
            "limitées aux émissions énergétiques (agriculture /7, déchets absents) ; "
            "émissions du bouquet v2.4 conservées"
        ),
    },
]

# Qualités propres aux EPCI à cheval sur deux régions (cf. docstring du module).
QUALITE_CHEVAL_INTERPOLE = "interpolé_cheval"
QUALITE_CHEVAL_EXTRAPOLE = "extrapolé_cheval"
QUALITE_CHEVAL_NON_VERIFIABLE = "cheval_non_verifiable"
# Total recalculé quand un secteur a été repris à la place d'un 0 non publié.
QUALITE_SOMME_SECTEURS = "somme_secteurs"
# EPCI issu d'une fusion, recomposé par somme des EPCI qu'il réunit.
QUALITE_FUSION_EPCI = "fusion_epci"

# ---------------------------------------------------------------------------
# Mapping ID indicateur OREC → code interne
# ---------------------------------------------------------------------------
_ID_TO_CODE: dict[str, str] = {
    "0201.0101": "EC_CONSO_RESIDENTIEL",
    "0201.0102": "EC_CONSO_TERTIAIRE",
    "0201.0107": "EC_CONSO_INDUSTRIE",
    "0201.0103": "EC_CONSO_AGRICULTURE",
    "0201.0105": "EC_CONSO_TRANSPORT_ROUTIER",
    "0201.0106": "EC_CONSO_AUTRE_TRANSPORT",
    "0201.0110": "EC_CONSO_DECHETS",
    "0201.0100": "EC_CONSO_TOTAL",
    "0101.0101": "EC_EMIS_RESIDENTIEL",
    "0101.0102": "EC_EMIS_TERTIAIRE",
    "0101.0108": "EC_EMIS_INDUSTRIE_HORS_BE",
    "0101.0109": "EC_EMIS_BRANCHE_ENERGIE",
    "0101.0103": "EC_EMIS_AGRICULTURE",
    "0101.0105": "EC_EMIS_TRANSPORT_ROUTIER",
    "0101.0106": "EC_EMIS_AUTRE_TRANSPORT",
    "0101.0110": "EC_EMIS_DECHETS",
    "0101.0100": "EC_EMIS_TOTAL",
}

# Utilisé uniquement comme repli pour EC_EMIS_INDUSTRIE_HORS_BE - non exposé en sortie.
_ID_FALLBACK: dict[str, str] = {
    "0101.0107": "EC_EMIS_INDUSTRIE",
}

# Secteurs jamais nuls sur un EPCI : un 0 publié y est une valeur non publiée
# (cf. docstring du module, note zéros non publiés).
_ID_JAMAIS_NULS = {"0201.0101", "0201.0105", "0101.0101", "0101.0105"}

# Secteurs dont la somme forme chaque total (préfixe d'ID OREC -> total).
_SECTEURS_DU_TOTAL: dict[str, tuple[str, list[str]]] = {
    prefixe: (total, [c for i, c in _ID_TO_CODE.items() if i.startswith(prefixe) and c != total])
    for prefixe, total in (("0101.", "EC_EMIS_TOTAL"), ("0201.", "EC_CONSO_TOTAL"))
}

_ALL_CODES      = list(_ID_TO_CODE.values())
_ID_TO_CODE_ALL = {**_ID_TO_CODE, **_ID_FALLBACK}


def _download_csv(url: str) -> pd.DataFrame:
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return pd.read_csv(io.BytesIO(r.content), dtype=str, sep=";")


def _load_raw() -> pd.DataFrame:
    """
    Télécharge et combine les CSV consommation-secteur et émissions-secteur
    depuis data.gouv.fr. Le résultat brut est mis en cache (TTL :
    _RAW_CACHE_TTL_DAYS). Si le contenu retéléchargé diffère du cache
    précédent, un avertissement le signale et l'ancien cache est conservé
    (_RAW_CACHE_PRECEDENT_PATH) pour comparaison.

    Retourne un DataFrame avec colonnes :
        code_epci  (str, 9 chiffres)
        annee      (Int64)
        id_orec    (str, ex: '0201.0101')
        valeur     (float)
        confidentiel (bool)  - donnée masquée (secret statistique)
        code_region  (str)   - "84", ou "84-93" pour le total d'un EPCI à
                               cheval sur deux régions
        completude   (bool)  - True pour le total d'un EPCI à cheval ; False
                               pour un EPCI à une seule région, comme pour la
                               part d'un EPCI à cheval limitée à une région
    """
    ancien = None
    if _RAW_CACHE_PATH.exists():
        age_days = _age_jours(_RAW_CACHE_PATH)
        cache = pd.read_parquet(_RAW_CACHE_PATH)
        if not set(_RAW_COLONNES) <= set(cache.columns):
            log.info("Énergie-climat : cache brut sans code_region/completude, retéléchargement")
        elif age_days < _RAW_CACHE_TTL_DAYS:
            log.info(f"Énergie-climat : cache brut utilisé ({age_days:.0f} j)")
            return cache
        else:
            log.info(
                f"Énergie-climat : cache brut expiré ({age_days:.0f} j, TTL "
                f"{_RAW_CACHE_TTL_DAYS} j), retéléchargement"
            )
            ancien = cache

    dfs = []
    for url in [_URL_CONSO_SECTEUR, _URL_EMIS_SECTEUR]:
        log.info(f"Téléchargement {url} ...")
        dfs.append(_download_csv(url))
    df = pd.concat(dfs, ignore_index=True)

    df = df.rename(columns={"id_indicateur": "id_orec", "annee_ref": "annee"})

    df["code_epci"] = df["code_epci"].str.strip().str.zfill(9)
    df = df[df["code_epci"].str.match(r"^\d{9}$")]
    df["annee"]     = pd.to_numeric(df["annee"], errors="coerce").astype("Int64")
    # Les CSV OREC utilisent la virgule comme séparateur décimal
    df["valeur"]    = pd.to_numeric(
        df["valeur"].str.replace(",", ".", regex=False), errors="coerce"
    )
    df["id_orec"]   = df["id_orec"].str.strip()

    if "confidentiel" in df.columns:
        df["confidentiel"] = df["confidentiel"].astype(str) == "1"
    else:
        df["confidentiel"] = False

    # code_region et completude distinguent, pour un EPCI à cheval sur deux
    # régions, le total (composé) de la part d'une seule région (cf. docstring
    # du module).
    df["code_region"] = df["code_region"].astype(str).str.strip() if "code_region" in df.columns else ""
    df["completude"] = (
        df["completude"].astype(str).str.strip() == "1" if "completude" in df.columns else False
    )

    df = df.dropna(subset=["annee", "id_orec"])
    # "valeur" est vide quand confidentiel=True (secret statistique) : ces
    # lignes sont conservées quand même pour que le masquage soit tracé
    # plus loin, plutôt que silencieusement supprimées avec les autres NaN.
    df = df[df["valeur"].notna() | df["confidentiel"]]

    result = df[_RAW_COLONNES].reset_index(drop=True)
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    if ancien is not None:
        n_diff = _nb_lignes_differentes(ancien, result)
        if n_diff:
            date_ancien = time.strftime("%Y-%m-%d", time.localtime(_RAW_CACHE_PATH.stat().st_mtime))
            log.warning(
                f"Énergie-climat : bouquet OREC modifié depuis le téléchargement du "
                f"{date_ancien} ({n_diff} lignes ajoutées, retirées ou modifiées) ; "
                f"ancien cache conservé dans {_RAW_CACHE_PRECEDENT_PATH.name}"
            )
            _RAW_CACHE_PATH.replace(_RAW_CACHE_PRECEDENT_PATH)
        else:
            log.info("Énergie-climat : bouquet OREC inchangé depuis le dernier téléchargement")
    result.to_parquet(_RAW_CACHE_PATH)
    return result


def _age_jours(path) -> float:
    return (time.time() - path.stat().st_mtime) / 86400


def _nb_lignes_differentes(a: pd.DataFrame, b: pd.DataFrame) -> int:
    """Nombre de lignes présentes dans un seul des deux bouquets bruts."""
    def _empreintes(d):
        d = d[_RAW_COLONNES].astype({"annee": "Int64", "valeur": "float64"})
        return pd.util.hash_pandas_object(d.round({"valeur": 6}), index=False).value_counts()
    ea, eb = _empreintes(a), _empreintes(b)
    return int((ea.sub(eb, fill_value=0)).abs().sum())


def _cache_annuel_valide(cache_path) -> bool:
    """Un cache annuel n'est valide que s'il a été calculé depuis le cache brut
    courant, lui-même non expiré : sans quoi il masquerait indéfiniment une
    nouvelle version du bouquet."""
    if not _RAW_CACHE_PATH.exists():
        return False
    if _age_jours(_RAW_CACHE_PATH) >= _RAW_CACHE_TTL_DAYS:
        log.info("Énergie-climat : cache brut expiré, cache annuel recalculé")
        return False
    return cache_path.stat().st_mtime >= _RAW_CACHE_PATH.stat().st_mtime


def _lire_substitution(fichier) -> pd.DataFrame:
    df = pd.read_csv(fichier, dtype={"code_epci": str, "id_orec": str, "code_region": str})
    df["annee"] = df["annee"].astype("Int64")
    df["valeur"] = pd.to_numeric(df["valeur"], errors="coerce")
    for col in ("confidentiel", "completude"):
        df[col] = df[col].astype(str).str.strip().str.lower().isin(("true", "1"))
    return df[_RAW_COLONNES]


def _substituer(df_raw: pd.DataFrame, annee: int, substitutions=None) -> pd.DataFrame:
    """Applique _SUBSTITUTIONS au bouquet brut (cf. docstring du module).

    Le journal compare, pour l'année demandée, le total publié et le total
    substitué : un écart devenu nul signale une substitution à retirer.
    """
    substitutions = _SUBSTITUTIONS if substitutions is None else substitutions
    for sub in substitutions:
        region, prefixe = sub["region"], sub["prefixe"]
        cible = (
            df_raw["code_region"].astype(str).str.split("-").apply(lambda r: region in r)
            & df_raw["id_orec"].str.startswith(prefixe)
        )
        remplacement = _lire_substitution(sub["fichier"])
        hors_cible = ~(
            remplacement["code_region"].str.split("-").apply(lambda r: region in r)
            & remplacement["id_orec"].str.startswith(prefixe)
        )
        if hors_cible.any():
            raise ValueError(
                f"{sub['fichier']} : {int(hors_cible.sum())} lignes hors région {region} / ID {prefixe}*"
            )

        def _total(d):
            d = d[(d["annee"] == annee) & d["id_orec"].str.endswith(".0100") & (d["code_region"] == region)]
            return d["valeur"].sum()

        log.warning(
            f"Énergie-climat : substitution région {region}, ID {prefixe}* - "
            f"{int(cible.sum())} lignes publiées remplacées par {len(remplacement)} "
            f"({sub['fichier'].name}) ; total {annee} publié {_total(df_raw[cible]):,.0f}, "
            f"retenu {_total(remplacement):,.0f} - {sub['motif']}"
        )
        df_raw = pd.concat([df_raw[~cible], remplacement], ignore_index=True)
    return df_raw


def _dedupliquer(df: pd.DataFrame) -> pd.DataFrame:
    """
    Une seule ligne par (EPCI, indicateur, année).

    Plusieurs lignes coexistent pour une même clé : un EPCI à cheval sur deux
    régions (ligne complète + part d'une région) et, dans les fichiers, une
    ligne masquée (valeur vide, confidentiel=1) à côté de la valeur. Ordre de
    préférence : ligne complète, puis non masquée, puis renseignée. Le choix
    est explicite pour ne pas dépendre de l'ordre des lignes du fichier, sous
    peine de remplacer une valeur par une ligne masquée.
    """
    cle = ["code_epci", "id_orec", "annee"]
    ordre = df.assign(
        _partielle=~df["completude"], _masquee=df["confidentiel"], _vide=df["valeur"].isna()
    ).sort_values(cle + ["_partielle", "_masquee", "_vide"], kind="stable")
    return ordre.drop_duplicates(cle, keep="first").drop(columns=["_partielle", "_masquee", "_vide"])


def _epci_a_cheval(df_raw: pd.DataFrame) -> set[str]:
    """
    EPCI à cheval sur deux régions : ceux qui portent une ligne complète ou
    plusieurs codes région dans les CSV, plus ceux du référentiel des EPCI
    (`codes_regions`, cf. geo_epci.load_epci_centroids). Ce second volet attrape un EPCI que les
    OREC ne publient jamais en total (Maine Saosnois : une seule région).
    """
    codes_region = df_raw.groupby("code_epci")["code_region"].nunique()
    a_cheval = set(df_raw.loc[df_raw["completude"], "code_epci"]) | set(codes_region[codes_region > 1].index)
    try:
        from sources.geo_epci import load_epci_centroids

        ref = load_epci_centroids()
        a_cheval |= set(ref.loc[ref["codes_regions"].apply(len) > 1, "code_epci"])
    except Exception as e:
        log.warning(f"Énergie-climat : référentiel des EPCI à cheval indisponible ({e}), CSV seuls")
    return a_cheval


def _valeur_standard(grp: pd.DataFrame, annee: int) -> tuple[float, str]:
    """
    Valeur d'un (EPCI, indicateur) pour l'année demandée : valeur exacte, sinon
    prochaine valeur disponible (ou dernière si aucune n'existe après).
    Retourne (valeur, méthode) : "exact", "extrapolé", "secret_stat" ou "absent".
    """
    grp = grp.sort_values("annee")
    exact = grp[grp["annee"] == annee]
    if not exact.empty:
        row = exact.iloc[0]
        methode = "secret_stat" if row["confidentiel"] else "exact"
        return float(row["valeur"]), methode

    before = grp[grp["annee"] < annee]
    after  = grp[grp["annee"] > annee]

    if not after.empty:
        row = after.iloc[0]
    elif not before.empty:
        row = before.iloc[-1]
    else:
        return float("nan"), "absent"
    methode = "secret_stat" if row["confidentiel"] else "extrapolé"
    return float(row["valeur"]), methode


def _valeur_cheval(grp: pd.DataFrame, annee: int) -> tuple[float, str]:
    """
    Valeur d'un (EPCI à cheval sur deux régions, indicateur) pour l'année
    demandée, sans jamais retenir la part limitée à une région (cf. docstring
    du module). Même contrat que _valeur_standard.

    - ligne complète pour l'année : reprise ("exact", ou "secret_stat" si
      masquée) ;
    - sinon interpolation linéaire entre les années complètes encadrantes
      ("interpolé_cheval"), ou année complète la plus proche si l'une des
      bornes manque ("extrapolé_cheval") ;
    - aucune ligne complète pour cet indicateur : la valeur disponible est
      gardée mais ne peut être vérifiée ("cheval_non_verifiable").
    """
    complets = grp[grp["completude"]].sort_values("annee")
    if complets.empty:
        valeur, methode = _valeur_standard(grp, annee)
        if methode in ("exact", "extrapolé"):
            methode = QUALITE_CHEVAL_NON_VERIFIABLE
        return valeur, methode

    exact = complets[complets["annee"] == annee]
    if not exact.empty:
        row = exact.iloc[0]
        return float(row["valeur"]), "secret_stat" if row["confidentiel"] else "exact"

    renseignes = complets[complets["valeur"].notna()]
    before = renseignes[renseignes["annee"] < annee]
    after  = renseignes[renseignes["annee"] > annee]
    if not before.empty and not after.empty:
        b, a = before.iloc[-1], after.iloc[0]
        valeur = float(np.interp(annee, [b["annee"], a["annee"]], [b["valeur"], a["valeur"]]))
        return valeur, QUALITE_CHEVAL_INTERPOLE
    proche = after.iloc[0] if not after.empty else before.iloc[-1] if not before.empty else None
    if proche is not None:
        return float(proche["valeur"]), QUALITE_CHEVAL_EXTRAPOLE
    # Années complètes toutes masquées : la valeur n'est pas disponible.
    return float("nan"), "secret_stat" if complets["confidentiel"].any() else "absent"


def _recomposer_fusions(
    df_wide: pd.DataFrame, df_qualite: pd.DataFrame, fusions: dict[str, list[str]], annee: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Recompose chaque EPCI issu d'une fusion (cf. geo_epci.load_fusions_epci)
    par somme des EPCI qu'il réunit, quand le bouquet porte encore leurs codes
    (cf. docstring du module). Les codes réunis sont retirés.

    Par indicateur, la somme n'est calculée que si tous les EPCI réunis sont
    renseignés ; sinon la valeur reste absente, avec la qualité du premier
    EPCI non renseigné (secret_stat compris). Qualité d'une somme : "fusion_epci"
    si toutes ses composantes sont "ok", sinon la première qualité différente
    (ex. "extrapolé"), pour ne pas masquer une composante dégradée.
    """
    df_wide, df_qualite = df_wide.copy(), df_qualite.copy()
    recomposes = []
    for nouveau, anciens in sorted(fusions.items()):
        presents = [c for c in anciens if c in df_wide.index]
        if not presents or (nouveau in df_wide.index and nouveau not in anciens):
            # Bouquet déjà au nouveau périmètre (ou EPCI non couvert) : les
            # anciens codes éventuels sont seulement retirés.
            retires = [c for c in presents if c != nouveau]
            df_wide, df_qualite = df_wide.drop(index=retires), df_qualite.drop(index=retires)
            continue
        if len(presents) < len(anciens):
            log.warning(
                f"Énergie-climat {annee} : EPCI {nouveau} non recomposé, "
                f"{', '.join(sorted(set(anciens) - set(presents)))} absent(s) du bouquet"
            )
            continue

        valeurs = df_wide.loc[anciens]
        qualites = df_qualite.loc[anciens]
        ligne_v, ligne_q = {}, {}
        for col in df_wide.columns:
            v, q = valeurs[col], qualites[col]
            if v.notna().all():
                ligne_v[col] = float(v.sum())
                degradees = [x for x in q if x != "ok"]
                ligne_q[col] = degradees[0] if degradees else QUALITE_FUSION_EPCI
            else:
                ligne_v[col] = float("nan")
                manquantes = q[v.isna()].dropna()
                ligne_q[col] = (
                    "secret_stat" if (manquantes == "secret_stat").any()
                    else manquantes.iloc[0] if len(manquantes) else pd.NA
                )
        retires = [c for c in anciens if c != nouveau]
        df_wide, df_qualite = df_wide.drop(index=retires), df_qualite.drop(index=retires)
        df_wide.loc[nouveau] = pd.Series(ligne_v)
        df_qualite.loc[nouveau] = pd.Series(ligne_q)
        recomposes.append(f"{nouveau} <- {', '.join(anciens)}")

    if recomposes:
        log.info(
            f"Énergie-climat {annee} : {len(recomposes)} EPCI fusionnés recomposés par somme "
            f"({' ; '.join(recomposes)})"
        )
    return df_wide, df_qualite


def extract_indicators(annee: int, fusions: dict[str, list[str]] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Retourne les indicateurs énergie-climat pour l'année demandée.

    Paramètres
    ----------
    annee : int
        Année de référence.
    fusions : dict, optionnel
        EPCI issus d'une fusion (format de geo_epci.load_fusions_epci, qui
        les fournit par défaut), recomposés par _recomposer_fusions.

    Retourne
    --------
    (df_wide, df_qualite) - deux DataFrames indexés sur code_epci (str, 9 chiffres),
    avec les mêmes colonnes (codes indicateurs) :
      - df_wide   : valeurs numériques (NaN si absentes)
      - df_qualite : qualité par valeur - "ok" (donnée exacte),
                     "extrapolé" (prochaine valeur disponible, ou dernière si aucune n'existe après),
                     "secret_stat" (masquée) ; pour un EPCI à cheval sur deux
                     régions : "interpolé_cheval", "extrapolé_cheval" ou
                     "cheval_non_verifiable" ; pour un EPCI fusionné
                     recomposé : "fusion_epci" (cf. docstring du module)
    """
    cache_path         = INTERIM_DIR / f"energie_climat_{annee}_v{_CACHE_VERSION}.parquet"
    cache_qualite_path = INTERIM_DIR / f"energie_climat_{annee}_v{_CACHE_VERSION}_qualite.parquet"
    if cache_path.exists() and cache_qualite_path.exists() and _cache_annuel_valide(cache_path):
        log.info(f"Énergie-climat {annee} : cache utilisé")
        return pd.read_parquet(cache_path), pd.read_parquet(cache_qualite_path)
    if cache_path.exists():
        cache_path.unlink()

    df_raw = _substituer(_load_raw(), annee)
    a_cheval = _epci_a_cheval(df_raw)

    df_ind = df_raw[df_raw["id_orec"].isin(_ID_TO_CODE_ALL)].copy()
    # Même logique qu'en _load_raw() : garder les lignes masquées (valeur=NaN,
    # confidentiel=True) pour que _valeur_standard() les marque secret_stat.
    df_ind = df_ind[df_ind["valeur"].notna() | df_ind["confidentiel"]]
    zero_non_publie = df_ind["id_orec"].isin(_ID_JAMAIS_NULS) & (df_ind["valeur"] == 0)
    if zero_non_publie.any():
        log.warning(
            f"Énergie-climat : {zero_non_publie.sum()} zéros résidentiel / transport routier "
            f"traités comme non publiés ({df_ind.loc[zero_non_publie, 'code_epci'].nunique()} EPCI)"
        )
    # (EPCI, ID) dont le 0 tombe sur l'année demandée : leur total publié l'exclut.
    zeros_annee = df_ind.loc[zero_non_publie & (df_ind["annee"] == annee), ["code_epci", "id_orec"]]
    df_ind = df_ind[~zero_non_publie]
    df_ind = _dedupliquer(df_ind)

    _empty_qualite = pd.DataFrame(columns=_ALL_CODES)
    _empty_qualite.index.name = "code_epci"

    if df_ind.empty:
        log.warning("Énergie-climat : aucune donnée dans les fichiers source.")
        return pd.DataFrame(columns=_ALL_CODES), _empty_qualite

    records = []
    n_exact = n_fallback = n_secret = 0
    n_cheval: dict[str, int] = {}
    for (epci, id_orec), grp in df_ind.groupby(["code_epci", "id_orec"]):
        if epci in a_cheval:
            valeur, methode = _valeur_cheval(grp, annee)
        else:
            valeur, methode = _valeur_standard(grp, annee)
        records.append({"code_epci": epci, "id_orec": id_orec, "valeur": valeur, "methode": methode})
        if methode == "secret_stat":
            n_secret += 1
        elif methode == "exact":
            n_exact += 1
        elif methode in (QUALITE_CHEVAL_INTERPOLE, QUALITE_CHEVAL_EXTRAPOLE, QUALITE_CHEVAL_NON_VERIFIABLE):
            n_cheval[methode] = n_cheval.get(methode, 0) + 1
        else:
            n_fallback += 1

    if n_fallback:
        log.warning(
            f"Énergie-climat {annee} : {n_exact} valeurs exactes, "
            f"{n_fallback} en repli (prochaine/dernière valeur connue)"
        )
    if n_cheval:
        log.warning(
            f"Énergie-climat {annee} : valeurs d'EPCI à cheval sur deux régions sans "
            f"total publié pour l'année ({', '.join(f'{n} {m}' for m, n in sorted(n_cheval.items()))}) - "
            f"la part limitée à une région n'est jamais retenue"
        )
    if n_secret:
        log.info(
            f"Énergie-climat {annee} : {n_secret} valeurs masquées par le "
            f"secret statistique (colonne 'confidentiel')"
        )

    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=_ALL_CODES), _empty_qualite

    df["code_indicateur"] = df["id_orec"].map(_ID_TO_CODE_ALL)

    def _pivot(value_col):
        dfw = df.pivot_table(
            index="code_epci",
            columns="code_indicateur",
            values=value_col,
            aggfunc="first",
        )
        dfw.columns.name = None
        dfw.index.name = "code_epci"
        return dfw

    df_wide    = _pivot("valeur")
    df_qualite = _pivot("methode")

    df_qualite = df_qualite.replace("exact", "ok")

    hors_be_col = "EC_EMIS_INDUSTRIE_HORS_BE"
    ind_col     = "EC_EMIS_INDUSTRIE"
    if ind_col in df_wide.columns:
        mask_repli = df_wide[hors_be_col].isna() & df_wide[ind_col].notna() if hors_be_col in df_wide.columns \
                     else df_wide[ind_col].notna()
        if mask_repli.any():
            if hors_be_col not in df_wide.columns:
                df_wide[hors_be_col]    = float("nan")
                df_qualite[hors_be_col] = pd.NA
            df_wide.loc[mask_repli, hors_be_col]    = df_wide.loc[mask_repli, ind_col]
            df_qualite.loc[mask_repli, hors_be_col] = "repli_industrie_totale"
            log.info(
                f"Énergie-climat {annee} : EC_EMIS_INDUSTRIE_HORS_BE manquant → repli "
                f"sur EC_EMIS_INDUSTRIE (0101.0107) pour {mask_repli.sum()} EPCI"
            )
        df_wide    = df_wide.drop(columns=[ind_col], errors="ignore")
        df_qualite = df_qualite.drop(columns=[ind_col], errors="ignore")

    for code in _ALL_CODES:
        if code not in df_wide.columns:
            log.debug(f"Énergie-climat {annee} : indicateur {code} absent → NaN")
            df_wide[code]    = float("nan")
            df_qualite[code] = pd.NA

    df_wide    = df_wide[_ALL_CODES]
    df_qualite = df_qualite[_ALL_CODES]

    for prefixe, (total, secteurs) in _SECTEURS_DU_TOTAL.items():
        epci = zeros_annee.loc[zeros_annee["id_orec"].str.startswith(prefixe), "code_epci"].unique()
        epci = df_wide.index.intersection(epci)
        if len(epci):
            df_wide.loc[epci, total]    = df_wide.loc[epci, secteurs].sum(axis=1, min_count=1)
            df_qualite.loc[epci, total] = QUALITE_SOMME_SECTEURS
            log.warning(
                f"Énergie-climat {annee} : {total} remplacé par la somme des secteurs "
                f"pour {len(epci)} EPCI (total publié sans les secteurs à 0)"
            )

    if fusions is None:
        from sources.geo_epci import load_fusions_epci
        fusions = load_fusions_epci()
    df_wide, df_qualite = _recomposer_fusions(df_wide, df_qualite, fusions, annee)

    n_epci    = len(df_wide)
    conso_tot = df_wide["EC_CONSO_TOTAL"].sum(skipna=True)
    emis_tot  = df_wide["EC_EMIS_TOTAL"].sum(skipna=True)
    log.info(
        f"Énergie-climat {annee} : {n_epci} EPCI - "
        f"conso totale = {conso_tot:,.0f} GWh, "
        f"émissions totales = {emis_tot:,.1f} ktCO2e/an"
    )

    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    df_wide.to_parquet(cache_path)
    df_qualite.to_parquet(cache_qualite_path)
    return df_wide, df_qualite
