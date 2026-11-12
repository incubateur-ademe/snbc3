"""
api/main.py
============
Application FastAPI exposant le moteur de territorialisation Traj GES à la
demande, pour intégration dans la plateforme ADEME Territoires en Transition
(TeT). Cf. api/README.md.

Lancer en développement : uv run uvicorn api.main:app --reload
Prérequis : un artefact pivot publié au préalable (`uv run python main.py
publish_pivot`), lu par défaut depuis data/output/pivot/.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.config import settings
from api.pivot_store import PivotStore
from api.routers import excel, meta, pivots, territorialisation
from config.logging_config import setup_logging

setup_logging()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = PivotStore(settings.pivot_dir)
    try:
        store.load()
    except Exception as e:
        log.error(
            f"Chargement des données pivot échoué au démarrage ({e}) - "
            f"le service répondra 503 tant qu'aucun artefact valide n'est disponible."
        )
    app.state.pivot_store = store
    yield


app = FastAPI(
    title="Trajectoire GES territorialisée de la SNBC à l'échelle EPCI - API",
    description="Résultats de territorialisation SNBC3 à la demande, par territoire, "
                 "avec surcharge optionnelle des données pivot.",
    lifespan=lifespan,
)

app.include_router(meta.router)
app.include_router(pivots.router)
app.include_router(territorialisation.router)
app.include_router(excel.router)
