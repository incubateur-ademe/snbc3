"""
api/schemas.py
===============
Modèles Pydantic des requêtes/réponses de l'API.

La réponse de territorialisation expose telles quelles les colonnes de
`pipeline.territorialize.TERRITORIALISE_COLUMNS` (format stable, documenté
dans docs/resultats_territorialises.md) plutôt qu'un mapping vers un schéma
applicatif propre à la plateforme d'accueil.
"""

from pydantic import BaseModel, Field


class OverrideItem(BaseModel):
    code_indicateur: str
    annee: int
    valeur: float


class TerritorializationRequest(BaseModel):
    annee: int | None = Field(default=None, description="Année de référence (défaut : ANNEE_REF)")
    overrides: list[OverrideItem] = Field(default_factory=list)
    dju_variant: str | None = None


class TerritorializationRow(BaseModel):
    code_epci: str
    nom_epci: str | None = None
    zone: str | None = None
    code_region: str | None = None
    code_departement: str | None = None
    code_traj_supra: str | None = None
    type: str | None = None
    secteur: str | None = None
    usage: str | None = None
    code_pivot: str | None = None
    unite: str | None = None
    annee: int
    valeur_brute: float | None = None
    valeur_recalee: float | None = None
    code_recalage: str | None = None
    recal_factor: float | None = None
    ecart_conservation: float | None = None
    qualite: str | None = None


class TerritorializationResponse(BaseModel):
    code_epci: str
    zone: str | None = None
    niveau_geo: str | None = None
    methode_resolution: str
    rows: list[TerritorializationRow]


class PivotValue(BaseModel):
    code_indicateur: str
    annee: int
    valeur: float | None = None
    unite: str | None = None
    source: str | None = None
    qualite: str | None = None


class PivotsResponse(BaseModel):
    code_epci: str
    zone: str | None = None
    niveau_geo: str | None = None
    methode_resolution: str
    pivots: list[PivotValue]


class HealthResponse(BaseModel):
    status: str


class ZoneVersion(BaseModel):
    annee_ref: int
    date_generation: str
    n_lignes: int
    n_territoires: int


class VersionResponse(BaseModel):
    code_version: str
    pivot: ZoneVersion | None = None
