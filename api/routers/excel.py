"""
api/routers/excel.py
=====================
Export du classeur "Trajectoire GES de référence" pointé sur un seul
territoire (+ overrides éventuels) - cf. api/excel_export.py pour le détail
du mécanisme (pas de xlwings/COM, pas de reconstruction du classeur maître
à la demande).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.auth import require_api_key
from api.config import settings
from api.dependencies import get_pivot_store
from api.excel_export import ExcelExportError, generate_territoire_workbook
from api.pivot_store import PivotStore
from api.schemas import OverrideItem
from pipeline.territoire_resolver import resolve_territoire

router = APIRouter(prefix="/territorialisation", tags=["excel"],
                    dependencies=[Depends(require_api_key)])


class ExcelExportRequest(BaseModel):
    overrides: list[OverrideItem] = Field(default_factory=list)


@router.post("/{code_epci}/excel")
def territorialiser_excel(
    code_epci: str,
    request: ExcelExportRequest,
    store: PivotStore = Depends(get_pivot_store),
) -> Response:
    if not store.loaded:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Données pivot non chargées")

    resolution = resolve_territoire(code_epci, store.df_long)
    if not resolution["resolu"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Territoire {code_epci!r} non résolu")

    if not settings.master_workbook_path.exists():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Classeur maître introuvable ({settings.master_workbook_path})",
        )

    try:
        content, non_trouves = generate_territoire_workbook(
            settings.master_workbook_path,
            resolution["code_epci"],
            [o.model_dump() for o in request.overrides],
        )
    except ExcelExportError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))

    headers = {
        "Content-Disposition": f'attachment; filename="territoire_{resolution["code_epci"]}.xlsx"',
    }
    if non_trouves:
        headers["X-Overrides-Non-Appliques"] = ",".join(non_trouves)

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )
