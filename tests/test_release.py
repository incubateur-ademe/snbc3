"""
Paquet de publication (`scripts/release.py`) : mention de version, rapport
PDF repris de docs/, contrôles de cohérence des produits, manifeste.
"""

import json
import logging
import os
from datetime import datetime

import openpyxl
import pandas as pd
import pytest

from config.settings import ZONES_TERRITORIALISEES
from scripts import release
from scripts.release import ReleaseError, publier

QUAND = datetime(2026, 2, 3, 9, 5)


def test_mention_version():
    assert release.mention_version("2.0", QUAND) == "Version 2.0 du 03/02/2026 à 09h05"


@pytest.fixture
def produits(tmp_path):
    """Produits minimaux d'un run : bdd_epci_wide.xlsx, puis résultats et classeur."""
    out = tmp_path / "output"
    out.mkdir()
    wide = out / "bdd_epci_wide.xlsx"
    wide.write_bytes(b"wide")
    manif = out / "bdd_epci_manifest.json"
    manif.write_text(json.dumps({"annee_ref": 2021}), encoding="utf-8")
    df = pd.DataFrame({
        "code_epci": [f"E{i}" for i in range(len(ZONES_TERRITORIALISEES))],
        "zone": list(ZONES_TERRITORIALISEES),
        "annee": [2020 + i for i in range(len(ZONES_TERRITORIALISEES))],
    })
    res = out / "resultats_territorialises.parquet"
    df.to_parquet(res)
    classeur = out / "classeur.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "INTRODUCTION"
    wb["INTRODUCTION"]["B3"] = "=Notice!B3"
    wb["INTRODUCTION"]["B4"] = "Version de travail, non finalisée"
    wb.create_sheet("Notice")["B3"] = "Version 2 du 05/11/2026"
    wb.save(classeur)
    t = wide.stat().st_mtime
    for p in (res, classeur):
        os.utime(p, (t + 10, t + 10))
    rapport = tmp_path / "docs" / "Rapport.pdf"
    rapport.parent.mkdir()
    rapport.write_bytes(b"PDF v1")
    return {
        "releases_dir": tmp_path / "releases", "resultats": res, "classeur": classeur,
        "bdd_wide": wide, "bdd_manifeste": manif, "rapport": rapport,
    }


def _manifeste(dest):
    return json.loads((dest / "manifest.json").read_text(encoding="utf-8"))


def test_pdf_copie(produits):
    dest = publier("2.0", **produits)
    assert (dest / "Rapport.pdf").read_bytes() == b"PDF v1"
    m = _manifeste(dest)
    assert m["rapport"] == {"source": "docs/Rapport.pdf"}
    assert "Rapport.pdf" in {f["nom"] for f in m["fichiers"]}


def test_pdf_absent(produits):
    produits["rapport"].unlink()
    with pytest.raises(ReleaseError, match="rapport introuvable"):
        publier("2.0", **produits)


def test_docx_plus_recent(produits, caplog):
    docx = produits["rapport"].with_suffix(".docx")
    docx.write_bytes(b"docx")
    t = produits["rapport"].stat().st_mtime
    os.utime(docx, (t + 10, t + 10))
    with caplog.at_level(logging.WARNING):
        publier("2.0", **produits)
    assert "plus récent que le PDF" in caplog.text


def test_publier_sans_pdf(produits):
    dest = publier("2.0", pdf=False, quand=QUAND, **produits)
    assert dest.name == "V2.0"
    assert sorted(p.name for p in dest.iterdir()) == [
        "classeur.xlsx", "manifest.json", "resultats_territorialises.parquet",
    ]
    m = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert m["version"] == "2.0"
    assert m["date_publication"].startswith("2026-02-03T09:05")
    assert m["donnees"] == {"annee_ref": 2021}
    assert m["resultats"]["zones"] == sorted(ZONES_TERRITORIALISEES)
    assert m["rapport"] is None
    noms = {f["nom"]: f for f in m["fichiers"]}
    assert noms["classeur.xlsx"]["octets"] == (dest / "classeur.xlsx").stat().st_size
    assert len(noms["classeur.xlsx"]["sha256"]) == 64

    mention = "Version 2.0 du 03/02/2026 à 09h05"
    assert m["classeur"] == {"mention_version": mention}
    wb = openpyxl.load_workbook(dest / "classeur.xlsx")
    assert wb["Notice"]["B3"].value == mention
    assert wb["INTRODUCTION"]["B3"].value == "=Notice!B3"
    assert wb["INTRODUCTION"]["B4"].value is None
    source = openpyxl.load_workbook(produits["classeur"])
    assert source["Notice"]["B3"].value == "Version 2 du 05/11/2026"


def test_release_existante(produits):
    publier("2.0", pdf=False, **produits)
    with pytest.raises(ReleaseError, match="existe déjà"):
        publier("2.0", pdf=False, **produits)
    assert publier("2.0", pdf=False, force=True, **produits).exists()


def test_classeur_perime(produits):
    t = produits["bdd_wide"].stat().st_mtime
    os.utime(produits["classeur"], (t - 10, t - 10))
    with pytest.raises(ReleaseError, match="update_satool"):
        publier("2.0", pdf=False, **produits)
    assert not produits["releases_dir"].exists() or not any(produits["releases_dir"].iterdir())


def test_zone_manquante(produits):
    pd.DataFrame({"code_epci": ["E"], "zone": ["HEXAGONE"], "annee": [2020]}).to_parquet(
        produits["resultats"])
    t = produits["bdd_wide"].stat().st_mtime
    os.utime(produits["resultats"], (t + 10, t + 10))
    with pytest.raises(ReleaseError, match="zones absentes"):
        publier("2.0", pdf=False, **produits)


@pytest.mark.parametrize("version", ["", "../2.0", "2 0"])
def test_version_invalide(produits, version):
    with pytest.raises(ReleaseError, match="version invalide"):
        publier(version, pdf=False, **produits)
