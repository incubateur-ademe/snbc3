"""
api/routers/meta.py
====================
Endpoints d'exploitation : santé du service et version chargée (code +
données pivot) - pas d'authentification requise, utiles pour un healthcheck
de conteneur ou une vérification rapide après mise à jour d'un artefact.
"""

import tomllib
from pathlib import Path

from fastapi import APIRouter, Depends

from api.dependencies import get_pivot_store
from api.pivot_store import PivotStore
from api.schemas import HealthResponse, VersionResponse, ZoneVersion
from config.settings import ROOT_DIR

router = APIRouter(tags=["meta"])


def _code_version() -> str:
    try:
        data = tomllib.loads((ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))
        return data["project"]["version"]
    except Exception:
        return "unknown"


@router.get("/health", response_model=HealthResponse)
def health(store: PivotStore = Depends(get_pivot_store)) -> HealthResponse:
    return HealthResponse(status="ok" if store.loaded else "pivot_non_charge")


@router.get("/version", response_model=VersionResponse)
def version(store: PivotStore = Depends(get_pivot_store)) -> VersionResponse:
    pivot_version = None
    if store.loaded:
        pivot_version = ZoneVersion(
            annee_ref=store.manifest["annee_ref"],
            date_generation=store.manifest["date_generation"],
            n_lignes=store.manifest["n_lignes"],
            n_territoires=store.manifest["n_territoires"],
        )
    return VersionResponse(code_version=_code_version(), pivot=pivot_version)
