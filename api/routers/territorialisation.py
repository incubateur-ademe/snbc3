"""
api/routers/territorialisation.py
==================================
Point d'entrée principal : résultats de territorialisation à la demande pour
un territoire, à partir des données pivot précollectées et/ou d'overrides
fournis par l'appelant.

Un seul endpoint stateless : les overrides sont
transmis intégralement à chaque appel, rien n'est persisté côté service.
Sans override ni paramètre non-défaut, la réponse est servie depuis le
résultat pré-calculé de la zone (PivotStore) - pas de recalcul. Avec
override, `territorialize()` est appelé avec `epci_unique` (cf.
pipeline/territorialize.py) : le ratio pivot a toujours besoin de la zone
entière, mais tout le calcul qui suit (jointure trajectoire nationale,
corrections, recalage, tri) se limite au territoire demandé - de l'ordre de
1 à 2s au lieu de 10 à 30s pour la zone HEXAGONE complète.

Le chemin rapide `epci_unique` s'applique donc systématiquement. Conséquence
documentée : `ecart_conservation` (diagnostic zone entière) n'est jamais
disponible dans une réponse API et vaut toujours `None` (seul le calcul
batch complet le fournit).
"""

import math

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import require_api_key
from api.dependencies import get_pivot_store
from api.pivot_store import PivotStore
from api.schemas import TerritorializationRequest, TerritorializationResponse, TerritorializationRow
from pipeline.overrides import OverrideError, apply_overrides
from pipeline.territoire_resolver import resolve_territoire
from pipeline.territorialize import TERRITORIALISE_COLUMNS, territorialize

router = APIRouter(prefix="/territorialisation", tags=["territorialisation"],
                    dependencies=[Depends(require_api_key)])


def _row_dict(row: dict) -> dict:
    """Remplace les NaN par None (JSON ne connaît pas NaN)."""
    return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in row.items()}


@router.post("/{code_epci}", response_model=TerritorializationResponse)
def territorialiser(
    code_epci: str,
    request: TerritorializationRequest,
    store: PivotStore = Depends(get_pivot_store),
) -> TerritorializationResponse:
    if not store.loaded:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Données pivot non chargées")

    resolution = resolve_territoire(code_epci, store.df_long)
    if not resolution["resolu"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Territoire {code_epci!r} non résolu")
    zone = resolution["zone"]
    ref_year = request.annee or store.ref_year

    parametres_non_defaut = any([request.dju_variant, request.annee])

    if not request.overrides and not parametres_non_defaut:
        try:
            result = store.get_default_result(zone)
        except Exception as e:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e))
    else:
        try:
            df_long = apply_overrides(
                store.df_long,
                resolution["code_epci"],
                [o.model_dump() for o in request.overrides],
                ref_year=ref_year,
            )
        except OverrideError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e))

        kwargs = {}
        if request.dju_variant is not None:
            kwargs["dju_variant"] = request.dju_variant

        try:
            result = territorialize(
                df_long, zone=zone, ref_year=ref_year,
                epci_unique=resolution["code_epci"], **kwargs,
            )
        except Exception as e:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Échec de la territorialisation : {e}")

    territoire_rows = result[result["code_epci"] == resolution["code_epci"]]
    rows = [
        TerritorializationRow(**_row_dict(r))
        for r in territoire_rows[TERRITORIALISE_COLUMNS].to_dict(orient="records")
    ]

    return TerritorializationResponse(
        code_epci=resolution["code_epci"],
        zone=resolution["zone"],
        niveau_geo=resolution["niveau_geo"],
        methode_resolution=resolution["methode_resolution"],
        rows=rows,
    )
