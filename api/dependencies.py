"""
api/dependencies.py
====================
Accès au PivotStore partagé (chargé une fois au démarrage de l'application,
cf. api/main.py) depuis les routers.
"""

from fastapi import Request

from api.pivot_store import PivotStore


def get_pivot_store(request: Request) -> PivotStore:
    return request.app.state.pivot_store
