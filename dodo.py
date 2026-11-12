"""
dodo.py
=======
Lance les commandes du pipeline Traj GES via doit (alternative à main.py en CLI direct).

Exemples :
  uv run doit list                       # liste les tâches disponibles
  uv run doit extract                    # équivalent à: uv run python main.py extract
  uv run doit dju -a 2020 -a 2021         # --annee 2020 2021
  uv run doit aggregate -a 2020
  uv run doit export -a 2020
  uv run doit territorialize -a 2020 --zone HEXAGONE
  uv run doit update_satool
  uv run doit generate_sources
  uv run doit carte
  uv run doit full -a 2020               # tâche par défaut (uv run doit)
"""

import subprocess
import sys
from pathlib import Path

from config.settings import ANNEE_REF

DOIT_CONFIG = {
    "default_tasks": ["full"],
    "verbosity": 2,  # affiche la sortie (colorée) des sous-commandes en direct
}

PYTHON = sys.executable
MAIN   = str(Path(__file__).parent / "main.py")


def _run_main(*cli_args: str) -> bool:
    result = subprocess.run([PYTHON, MAIN, *cli_args])
    return result.returncode == 0


def _annee_args(annee):
    # doit "list" params accumulate onto the default rather than replacing it,
    # so the default is kept empty here and the fallback applied at call time.
    return ["--annee", *[str(a) for a in (annee or [ANNEE_REF])]]


def task_extract():
    """Télécharge/met en cache les données brutes."""
    def run(annee, sources, force):
        args = ["extract", *_annee_args(annee)]
        if sources:
            args += ["--sources", *sources]
        if force:
            args.append("--force")
        return _run_main(*args)

    return {
        "actions": [run],
        "params": [
            {"name": "annee", "short": "a", "long": "annee", "type": list, "default": []},
            {"name": "sources", "short": "s", "long": "sources", "type": list, "default": []},
            {"name": "force", "short": "f", "long": "force", "type": bool, "default": False},
        ],
    }


def task_dju():
    """Calcule les DJU par EPCI."""
    def run(annee, methode, force):
        args = ["dju", *_annee_args(annee), "--methode", methode]
        if force:
            args.append("--force")
        return _run_main(*args)

    return {
        "actions": [run],
        "params": [
            {"name": "annee", "short": "a", "long": "annee", "type": list, "default": []},
            {"name": "methode", "short": "m", "long": "methode", "type": str, "default": "both"},
            {"name": "force", "short": "f", "long": "force", "type": bool, "default": False},
        ],
    }


def task_aggregate():
    """Agrège commune vers EPCI et produit la BDD au format long."""
    def run(annee, sources):
        args = ["aggregate", *_annee_args(annee)]
        if sources:
            args += ["--sources", *sources]
        return _run_main(*args)

    return {
        "actions": [run],
        "params": [
            {"name": "annee", "short": "a", "long": "annee", "type": list, "default": []},
            {"name": "sources", "short": "s", "long": "sources", "type": list, "default": []},
        ],
    }


def task_export():
    """Exporte la BDD en CSV long et XLSX large."""
    def run(annee):
        return _run_main("export", *_annee_args(annee))

    return {
        "actions": [run],
        "params": [
            {"name": "annee", "short": "a", "long": "annee", "type": list, "default": []},
        ],
    }


def task_territorialize():
    """Applique la trajectoire nationale SNBC3 aux pivots EPCI."""
    def run(annee, zone, dju_variant):
        return _run_main("territorialize", *_annee_args(annee), "--zone", zone,
                         "--dju-variant", dju_variant)

    return {
        "actions": [run],
        "params": [
            {"name": "annee", "short": "a", "long": "annee", "type": list, "default": []},
            {"name": "zone", "long": "zone", "type": str, "default": "HEXAGONE"},
            {"name": "dju_variant", "long": "dju-variant", "type": str, "default": "COSTIC"},
        ],
    }


def task_update_satool():
    """Met à jour le tableur Trajectoire GES avec bdd_epci_wide.xlsx."""
    def run(traj, sheet):
        args = ["update_satool", "--sheet", sheet]
        if traj:
            args += ["--traj", traj]
        return _run_main(*args)

    return {
        "actions": [run],
        "params": [
            {"name": "traj", "long": "traj", "type": str, "default": ""},
            {"name": "sheet", "long": "sheet", "type": str, "default": "Data_EPCI"},
        ],
    }


def task_generate_sources():
    """Génère le fichier Excel de documentation des sources de données."""
    return {"actions": [lambda: _run_main("generate_sources")]}


def task_carte():
    """Génère la carte interactive des stations Météo-France."""
    return {"actions": [lambda: _run_main("carte")]}


def task_full():
    """Enchaîne extract + dju + aggregate + export + territorialize + update_satool + generate_sources."""
    def run(annee, force):
        args = ["full", *_annee_args(annee)]
        if force:
            args.append("--force")
        return _run_main(*args)

    return {
        "actions": [run],
        "params": [
            {"name": "annee", "short": "a", "long": "annee", "type": list, "default": []},
            {"name": "force", "short": "f", "long": "force", "type": bool, "default": False},
        ],
    }
