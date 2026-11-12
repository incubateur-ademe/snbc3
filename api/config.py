"""
api/config.py
=============
Réglages du service API, distincts de config/settings.py (qui reste la
configuration du pipeline batch). Surchargeables par variable d'environnement
(préfixe TRAJGES_API_, cf. pydantic-settings) - utile pour pointer le service
vers un artefact pivot différent en développement/tests.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from config.settings import OUTPUT_DIR, TRAJ_GES_TARGET_PATH


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRAJGES_API_")

    # Dossier contenant bdd_epci.parquet + bdd_epci_manifest.json (cf.
    # pipeline.export.to_pivot_parquet) - data/output/ par défaut, comme
    # l'autre export du pipeline (bdd_epci_wide.xlsx).
    pivot_dir: Path = OUTPUT_DIR
    master_workbook_path: Path = TRAJ_GES_TARGET_PATH

    # Clés API acceptées, séparées par des virgules. Vide = auth désactivée
    # (pratique en développement local) - à ne jamais laisser vide en
    # production (cf. api/auth.py).
    api_keys: str = ""

    @property
    def api_keys_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}


settings = ApiSettings()
