"""
api/auth.py
===========
Vérification d'une clé API statique (header X-API-Key), volontairement
simple : à remplacer par le mécanisme d'authentification de la plateforme
d'accueil avant toute exposition publique.

Si `ApiSettings.api_keys` est vide (défaut en développement local),
l'authentification est désactivée : pratique pour tester en local, mais ne
doit jamais être le cas d'un déploiement exposé.
"""

from fastapi import Header, HTTPException, status

from api.config import settings


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    valid_keys = settings.api_keys_set
    if not valid_keys:
        return
    if x_api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé API absente ou invalide (en-tête X-API-Key requis)",
        )
