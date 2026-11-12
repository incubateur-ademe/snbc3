"""
sources - Modules d'extraction de donnees pour Traj GES.

Chaque module source exporte un dict SOURCE_META decrivant la source
et ses indicateurs. La fonction get_all_sources_meta() collecte
automatiquement ces metadonnees.
"""

import importlib
import pkgutil
from pathlib import Path


def get_all_sources_meta() -> list[dict]:
    """Importe tous les modules sources et collecte leurs SOURCE_META."""
    metas = []
    pkg_path = str(Path(__file__).parent)
    for importer, modname, ispkg in pkgutil.iter_modules([pkg_path]):
        if modname.startswith("_"):
            continue
        mod = importlib.import_module(f"sources.{modname}")
        meta = getattr(mod, "SOURCE_META", None)
        if meta is not None:
            meta.setdefault("module", f"sources/{modname}.py")
            metas.append(meta)
    return metas
