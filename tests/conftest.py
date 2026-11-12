"""
Marqueurs de niveau de test (politique : cf. README.md, « Tests et contrôles »).

- `donnees` : tests sur données réelles (BDD reconstruite depuis le cache
  data/interim, classeur SNBC3), une trentaine de secondes au total. Attribué
  automatiquement aux tests qui utilisent la fixture `bdd_reelle`.
- `excel` : convergence avec le classeur généré, plusieurs minutes. Exclu par
  défaut (pyproject.toml), à lancer avec `-m excel`.
"""

import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "bdd_reelle" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.donnees)
