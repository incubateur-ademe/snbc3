"""
config/settings.py
==================
Constantes globales du projet Traj GES.
Toutes les valeurs configurables sont centralisées ici.
"""

from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Répertoires
# ---------------------------------------------------------------------------

ROOT_DIR    = Path(__file__).parent.parent
DATA_DIR    = ROOT_DIR / "data"
RAW_DIR     = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
OUTPUT_DIR  = DATA_DIR / "output"
INPUT_DIR   = DATA_DIR / "input"

# ---------------------------------------------------------------------------
# Année de référence
# ---------------------------------------------------------------------------

ANNEE_REF = 2021

# ---------------------------------------------------------------------------
# Départements France hexagonale + DROM
# ---------------------------------------------------------------------------

DEPARTEMENTS: list[str] = (
    [f"{d:02d}" for d in range(1, 20)]
    + ["20"]   # Corse : MF utilise "20" (pas "2A"/"2B") dans ses fichiers
    + [f"{d:02d}" for d in range(21, 96)]
    + ["971", "972", "973", "974"]
    + ["985"]  # Mayotte : MF utilise l'ancien code "985" (pas "976") dans ses fichiers
)

DEPTS_HEXAGONE: set[str] = set(
    [f"{d:02d}" for d in range(1, 20)]
    + ["20"]
    + [f"{d:02d}" for d in range(21, 96)]
)

# ---------------------------------------------------------------------------
# Fichiers d'entrée (données de référence versionnées)
# ---------------------------------------------------------------------------

# Outil standalone : template versionné + output généré par inject
STANDALONE_DIR       = ROOT_DIR / "standalone_tool"
TRAJ_GES_TEMPLATE    = STANDALONE_DIR / "template" / "Trajectoire GES de référence-V2-template.xlsx"
TRAJ_GES_OUTPUT_DIR  = STANDALONE_DIR / "output"
TRAJ_GES_TARGET_PATH = TRAJ_GES_OUTPUT_DIR / "Trajectoire GES de référence-V2.xlsx"

# Trajectoire nationale SNBC3 retraitée (valeurs nationales 2020→2050 par sous-secteur).
# Source des valeurs appliquées aux pivots EPCI lors de la territorialisation.
# Version publiée (versionnée) : résultats du retraitement seuls, en valeurs,
# et parts des DROM dans les bilans énergie. Générée depuis le classeur de
# travail par scripts/publier_snbc3.py.
SNBC3_RETRAITEE_PATH = INPUT_DIR / "SNBC3 retraitée.xlsx"

# Classeur de travail (non versionné, cf. .gitignore) : onglets bruts DGEC,
# formules et détail du retraitement, bilans énergie par territoire. Lu
# seulement par scripts/publier_snbc3.py et scripts/controle_snbc3.py.
SNBC3_TRAVAIL_PATH = INPUT_DIR / "travail" / "SNBC3 retraitée - travail.xlsx"

# Onglet portant la trajectoire retraitée (codes en colonne D) de chaque zone
# ou groupe de zones. Le retraitement (SECTEN -> PCAET -> désagrégation par
# pivots) est fait dans le classeur. Les DROM n'ont qu'un onglet, pour leur
# groupe : la trajectoire de chaque DROM en est dérivée à l'exécution (cf.
# `trajectoire_derivee` dans config/traj_supra.yaml).
SNBC3_SHEETS: dict[str, str] = {
    "HEXAGONE": "SNBC3 retraitée HEX",
    "DROM": "SNBC3 retraitée DROM",
}


# Trajectoire nationale effectivement territorialisée : la SNBC3 ci-dessus par
# défaut. Une autre trajectoire au même format (un onglet par zone ou groupe,
# codes en colonne D, années en en-tête), par exemple un scénario exporté par
# VECTOR, la remplace le temps d'une exécution avec `utiliser_traj_nationale`
# (options globales --traj-nationale, --onglet et --nom-scenario de main.py).
# Lue à chaque appel par pipeline.territorialize et standalone_tool, jamais
# figée à l'import.
@dataclass
class TrajNationale:
    chemin: Path                  # classeur des onglets de trajectoire (et des parts DROM)
    onglets: dict[str, str]       # zone ou groupe -> onglet
    nom: str | None = None        # nom_scenario du classeur ; None : meta.description de traj_supra.yaml


TRAJ_NATIONALE = TrajNationale(SNBC3_RETRAITEE_PATH, dict(SNBC3_SHEETS))


def utiliser_traj_nationale(
    chemin: Path | str | None = None,
    onglets: dict[str, str] | None = None,
    nom: str | None = None,
) -> None:
    """
    Remplace la trajectoire nationale (cf. TRAJ_NATIONALE). `onglets` remplace
    toute la correspondance zone -> onglet : une zone absente n'est pas
    territorialisable (DROM d'un scénario limité à l'hexagone).
    """
    if chemin is not None:
        TRAJ_NATIONALE.chemin = Path(chemin)
    if onglets:
        TRAJ_NATIONALE.onglets = dict(onglets)
    if nom is not None:
        TRAJ_NATIONALE.nom = nom

# ---------------------------------------------------------------------------
# Zones de territorialisation
# ---------------------------------------------------------------------------

# Une zone = un ensemble d'EPCI territorialisés ensemble, dont le total sert de
# dénominateur aux ratios de pivot. Chaque DROM est sa propre zone : un pivot
# unique pour les cinq supposerait une conso par ménage ou par emploi homogène
# entre territoires, ce que les bilans DGEC démentent (cf.
# docs/territorialisation_drom.md).
# Code région (geo.api.gouv.fr) -> zone.
ZONE_PAR_REGION_DROM: dict[str, str] = {
    "01": "GUADELOUPE",
    "02": "MARTINIQUE",
    "03": "GUYANE",
    "04": "REUNION",
    "06": "MAYOTTE",
}
ZONES_DROM: tuple[str, ...] = tuple(ZONE_PAR_REGION_DROM.values())

# Groupes de zones : réglages de configuration communs (bloc `zones` de
# traj_supra.yaml), trajectoire retraitée commune (SNBC3_SHEETS) et ligne de
# total propre dans la BDD. `--zone DROM` désigne les zones territorialisées
# du groupe.
GROUPES_ZONES: dict[str, tuple[str, ...]] = {"DROM": ZONES_DROM}

# Zones effectivement territorialisées. Mayotte reste dans la BDD (zone, total,
# émissions IGT) mais n'est pas produite : les bases INSEE `base-cc-*` et
# FLORES ne la couvrent pas, ses pivots bâtiment, tertiaire et industrie
# manquent (cf. docs/territorialisation_drom.md, « Mayotte »).
ZONES_TERRITORIALISEES: tuple[str, ...] = (
    "HEXAGONE", "GUADELOUPE", "MARTINIQUE", "GUYANE", "REUNION",
)

# Codes des lignes agrégées de la BDD (code_epci = nom de la zone ou du groupe).
CODES_TOTAUX: set[str] = {"FRANCE", "HEXAGONE", *GROUPES_ZONES, *ZONES_DROM}


def groupe_de_zone(zone: str) -> str | None:
    """Groupe auquel appartient une zone (None pour HEXAGONE)."""
    return next((g for g, membres in GROUPES_ZONES.items() if zone in membres), None)


def zones_demandees(noms: list[str]) -> list[str]:
    """
    Développe une liste de zones ou de groupes en zones territorialisées, dans
    l'ordre, sans doublon : `["HEXAGONE", "DROM"]` donne HEXAGONE puis les
    quatre DROM produits.
    """
    out: list[str] = []
    for nom in noms:
        membres = GROUPES_ZONES.get(nom, (nom,))
        out += [z for z in membres if z in ZONES_TERRITORIALISEES and z not in out]
    return out

# ---------------------------------------------------------------------------
# Paramètres de territorialisation (pipeline/territorialize.py)
# ---------------------------------------------------------------------------

# Variante de DJU utilisée pour la correction climatique des sous-secteurs
# concernés (rapport DJU territoire / moyenne de zone). COSTIC = standard pro.
DJU_VARIANTS: tuple[str, ...] = ("COSTIC", "METEO")
TERR_DJU_VARIANT: str = "COSTIC"          # une de DJU_VARIANTS

# Zone traitée par défaut.
TERR_ZONE_DEFAUT: str = "HEXAGONE"

# ---------------------------------------------------------------------------
# Inventaire GES Territorialisé CITEPA (sources/citepa_igt.py)
# ---------------------------------------------------------------------------

# Zones où l'IGT alimente les codes EC_EMIS_* en remplacement des données OREC.
# Les DROM ne disposent pas d'observatoire régional publiant le bouquet
# d'indicateurs communs (seule La Réunion en a un ; la Guadeloupe uniquement à
# l'échelle régionale) : l'IGT y est la seule source d'émissions observées à la
# maille EPCI. Dans l'hexagone, l'OREC reste prioritaire et l'IGT n'est pas injecté
# (cf. scripts/rapport_igt_vs_orec.py pour les écarts entre les deux sources).
IGT_ZONES: set[str] = set(ZONES_DROM)
