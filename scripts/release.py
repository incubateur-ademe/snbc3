"""
scripts/release.py
==================
Paquet de publication figé (`main.py release`) : résultats territorialisés,
classeur, rapport méthodologique en PDF et manifeste, dans
`releases/V<version>/` (non versionné), à joindre à une release GitHub.

Le classeur publié porte la mention de version de la release dans
`Notice!B3`, sans l'alerte « version de travail » d'`INTRODUCTION!B4`.

Ne recalcule rien : assemble les produits du dernier run local (`full`, ou
`export` + `territorialize --zone HEXAGONE DROM` + `update_satool`), après
avoir vérifié qu'ils sont du même run.

Le rapport est le PDF versionné dans `docs/`, repris tel quel. Il est exporté
depuis Word à partir du docx source (non versionné) : un docx local plus
récent que le PDF est signalé.
"""

import hashlib
import io
import json
import logging
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from config.settings import (
    OUTPUT_DIR, ROOT_DIR, TRAJ_GES_TARGET_PATH, ZONES_TERRITORIALISEES,
)

log = logging.getLogger(__name__)

RELEASES_DIR = ROOT_DIR / "releases"
RAPPORT_PDF = ROOT_DIR / "docs" / "ADEME-Methodo-Outil-trajectoire-référence-V2.pdf"
RESULTATS = OUTPUT_DIR / "resultats_territorialises.parquet"
BDD_WIDE = OUTPUT_DIR / "bdd_epci_wide.xlsx"
BDD_MANIFESTE = OUTPUT_DIR / "bdd_epci_manifest.json"


class ReleaseError(RuntimeError):
    """Produit manquant ou incohérent."""


def mention_version(version: str, quand: datetime) -> str:
    """« Version 2.0 du 06/11/2026 à 14h32 »."""
    return f"Version {version} du {quand:%d/%m/%Y} à {quand:%H}h{quand:%M}"


# ---------------------------------------------------------------------------
# Classeur publié
# ---------------------------------------------------------------------------

CELLULE_VERSION = ("Notice", "B3")      # reprise par INTRODUCTION!B3
CELLULE_ALERTE = ("INTRODUCTION", "B4")  # « Version de travail, non finalisée »


def classeur_publie(src: Path, dst: Path, version: str, quand: datetime) -> str:
    """
    Copie du classeur avec la mention de version de la release dans
    `Notice!B3` et sans l'alerte « version de travail ». Retourne la mention.
    """
    import openpyxl

    from standalone_tool.classeur import ajuster_notes, finaliser

    wb = openpyxl.load_workbook(src, rich_text=True)
    ajuster_notes(wb)
    onglet, adresse = CELLULE_VERSION
    if onglet not in wb.sheetnames:
        raise ReleaseError(f"classeur : onglet « {onglet} » introuvable (mention de version)")
    mention = mention_version(version, quand)
    wb[onglet][adresse].value = mention
    onglet, adresse = CELLULE_ALERTE
    if onglet in wb.sheetnames:
        wb[onglet][adresse].value = None
    buf = io.BytesIO()
    wb.save(buf)
    dst.write_bytes(finaliser(buf.getvalue(), src.read_bytes()))
    return mention


# ---------------------------------------------------------------------------
# Contrôles et manifeste
# ---------------------------------------------------------------------------

def verifier_produits(resultats: Path, classeur: Path, bdd_wide: Path) -> None:
    """Produits présents et issus du même run : résultats et classeur
    postérieurs au dernier `export` (bdd_epci_wide.xlsx)."""
    manquants = [p for p in (resultats, classeur, bdd_wide) if not p.exists()]
    if manquants:
        raise ReleaseError("produits introuvables : " + ", ".join(str(p) for p in manquants))
    ref = bdd_wide.stat().st_mtime
    perimes = [
        (p, cmd) for p, cmd in (
            (resultats, "territorialize --zone HEXAGONE DROM"),
            (classeur, "update_satool"),
        ) if p.stat().st_mtime < ref
    ]
    if perimes:
        raise ReleaseError(
            "produits antérieurs au dernier export (bdd_epci_wide.xlsx) : "
            + "; ".join(f"{p.name} (relancer main.py {cmd})" for p, cmd in perimes)
        )


def verifier_rapport(rapport: Path) -> None:
    """PDF présent ; avertissement si le docx source local est plus récent."""
    if not rapport.exists():
        raise ReleaseError(f"rapport introuvable : {rapport} (ou --sans-pdf)")
    docx = rapport.with_suffix(".docx")
    if docx.exists() and docx.stat().st_mtime > rapport.stat().st_mtime:
        log.warning(f"{docx.name} est plus récent que le PDF : le réexporter depuis "
                    "Word avant de publier si le contenu a changé.")


def resume_resultats(resultats: Path) -> dict:
    """Couverture du parquet ; erreur si une zone territorialisée manque."""
    import pandas as pd
    df = pd.read_parquet(resultats, columns=["code_epci", "zone", "annee"])
    zones = sorted(df["zone"].unique())
    manquantes = sorted(set(ZONES_TERRITORIALISEES) - set(zones))
    if manquantes:
        raise ReleaseError(
            f"zones absentes de {resultats.name} : {manquantes} "
            "(relancer main.py territorialize --zone HEXAGONE DROM)"
        )
    return {
        "n_lignes": len(df),
        "n_territoires": int(df["code_epci"].nunique()),
        "zones": zones,
        "annees": [int(df["annee"].min()), int(df["annee"].max())],
    }


def etat_git() -> dict:
    def git(*a):
        return subprocess.run(
            ["git", *a], cwd=ROOT_DIR, capture_output=True, text=True, check=True,
        ).stdout.strip()
    try:
        return {
            "commit": git("rev-parse", "HEAD"),
            "branche": git("rev-parse", "--abbrev-ref", "HEAD"),
            "modifications_non_commitees": bool(git("status", "--porcelain")),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "branche": None, "modifications_non_commitees": None}


def sha256_fichier(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def decrire_fichier(p: Path) -> dict:
    return {"nom": p.name, "octets": p.stat().st_size, "sha256": sha256_fichier(p)}


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------

def publier(
    version: str,
    *,
    force: bool = False,
    pdf: bool = True,
    quand: datetime | None = None,
    releases_dir: Path = RELEASES_DIR,
    resultats: Path = RESULTATS,
    classeur: Path = TRAJ_GES_TARGET_PATH,
    bdd_wide: Path = BDD_WIDE,
    bdd_manifeste: Path = BDD_MANIFESTE,
    rapport: Path = RAPPORT_PDF,
) -> Path:
    """
    Assemble `releases_dir/V<version>/` et retourne son chemin. Rapport :
    le PDF `rapport` copié tel quel, ou absent (`pdf=False`).
    """
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.\-]*", version):
        raise ReleaseError(f"version invalide : {version!r} (ex. 2.0, 2.1-rc1)")
    quand = quand or datetime.now().astimezone()
    dest = releases_dir / f"V{version}"
    if dest.exists() and not force:
        raise ReleaseError(f"{dest} existe déjà (--force pour l'écraser)")

    verifier_produits(resultats, classeur, bdd_wide)
    if pdf:
        verifier_rapport(rapport)
    couverture = resume_resultats(resultats)
    git = etat_git()
    if git["modifications_non_commitees"]:
        log.warning("Modifications non commitées : le manifeste le signale.")

    tmp = releases_dir / f".V{version}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        fichiers = [shutil.copy2(resultats, tmp / resultats.name), tmp / classeur.name]
        mention_classeur = classeur_publie(classeur, tmp / classeur.name, version, quand)
        if pdf:
            fichiers.append(shutil.copy2(rapport, tmp / rapport.name))

        manifeste = {
            "version": version,
            "date_publication": quand.isoformat(timespec="seconds"),
            "code": git,
            "donnees": (json.loads(bdd_manifeste.read_text(encoding="utf-8"))
                        if bdd_manifeste.exists() else None),
            "resultats": couverture,
            "classeur": {"mention_version": mention_classeur},
            "rapport": {"source": f"docs/{rapport.name}"} if pdf else None,
            "fichiers": [decrire_fichier(Path(f)) for f in fichiers],
        }
        (tmp / "manifest.json").write_text(
            json.dumps(manifeste, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return dest
