"""
api/pivot_store.py
===================
Charge en mémoire les données pivot publiées par `main.py export`
(cf. pipeline/export.py::to_pivot_parquet) et met en cache le résultat de
territorialisation par défaut (sans override) de chaque zone, pour servir
les requêtes sans override instantanément.

Chargement au démarrage uniquement, sans rafraîchissement à chaud : un nouvel
artefact publié nécessite un redémarrage du service (cf. api/README.md).
"""

import json
import logging
from pathlib import Path

import pandas as pd

from config.settings import ZONES_TERRITORIALISEES as ZONES
from pipeline.territorialize import territorialize

log = logging.getLogger(__name__)


class PivotStoreError(RuntimeError):
    """Levée quand aucun artefact pivot n'est trouvé ou n'est chargeable."""


class PivotStore:
    """
    `pivot_dir` pointe vers le dossier `data/output/` (défaut) où
    `pipeline.export.to_pivot_parquet()` écrit deux fichiers fixes,
    toujours écrasés (comme `bdd_epci_wide.xlsx` - pas de version datée ni
    de sous-dossier, rien n'exige encore de faire coexister plusieurs
    versions) : `bdd_epci.parquet` et `bdd_epci_manifest.json`.
    """

    def __init__(self, pivot_dir: Path):
        self.parquet_path = pivot_dir / "bdd_epci.parquet"
        self.manifest_path = pivot_dir / "bdd_epci_manifest.json"
        self.df_long: pd.DataFrame | None = None
        self.manifest: dict | None = None
        self._default_results: dict[str, pd.DataFrame] = {}

    @property
    def loaded(self) -> bool:
        return self.df_long is not None

    def load(self) -> None:
        if not self.parquet_path.exists() or not self.manifest_path.exists():
            raise PivotStoreError(
                f"Artefact pivot introuvable ({self.parquet_path}) - "
                f"lancer `main.py publish_pivot` au préalable"
            )
        log.info(f"Chargement pivot: {self.parquet_path}")
        self.df_long = pd.read_parquet(self.parquet_path)
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))

        self._default_results = {}
        for zone in ZONES:
            try:
                self._default_results[zone] = territorialize(
                    self.df_long, zone=zone, ref_year=self.manifest["annee_ref"],
                )
                log.info(f"Résultat par défaut pré-calculé pour {zone}")
            except Exception as e:
                log.warning(f"Territorialisation par défaut {zone} indisponible : {e}")

    def get_default_result(self, zone: str) -> pd.DataFrame:
        if zone not in self._default_results:
            raise PivotStoreError(f"Aucun résultat par défaut disponible pour la zone {zone}")
        return self._default_results[zone]

    def get_territoire_rows(self, code_epci: str) -> pd.DataFrame:
        return self.df_long[self.df_long["code_epci"] == code_epci]

    @property
    def ref_year(self) -> int:
        return self.manifest["annee_ref"]
