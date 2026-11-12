"""
api/routers/pivots.py
======================
Lecture des valeurs pivot brutes d'un territoire (sans recalcul) - utile pour
préremplir un formulaire d'override côté plateforme TeT avant un appel à
/territorialisation.
"""

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import require_api_key
from api.dependencies import get_pivot_store
from api.pivot_store import PivotStore
from api.schemas import PivotsResponse, PivotValue
from pipeline.territoire_resolver import resolve_territoire

router = APIRouter(prefix="/territoires", tags=["pivots"], dependencies=[Depends(require_api_key)])


@router.get("/{code_epci}/pivots", response_model=PivotsResponse)
def get_pivots(
    code_epci: str,
    annee: int | None = None,
    store: PivotStore = Depends(get_pivot_store),
) -> PivotsResponse:
    if not store.loaded:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Données pivot non chargées")

    resolution = resolve_territoire(code_epci, store.df_long)
    if not resolution["resolu"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Territoire {code_epci!r} non résolu")

    rows = store.get_territoire_rows(resolution["code_epci"])
    if annee is not None:
        rows = rows[rows["annee"] == annee]

    pivots = [
        PivotValue(
            code_indicateur=str(r.code_indicateur),
            annee=int(r.annee),
            valeur=None if pd.isna(r.valeur) else float(r.valeur),
            unite=r.unite,
            source=r.source,
            qualite=r.qualite,
        )
        for r in rows.itertuples()
    ]
    return PivotsResponse(
        code_epci=resolution["code_epci"],
        zone=resolution["zone"],
        niveau_geo=resolution["niveau_geo"],
        methode_resolution=resolution["methode_resolution"],
        pivots=pivots,
    )
