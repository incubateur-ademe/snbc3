# Service API de territorialisation à la demande (cf. api/README.md).
#
# N'embarque PAS l'artefact pivot (data/output/bdd_epci.parquet +
# bdd_epci_manifest.json) ni le classeur maître standalone
# (standalone_tool/output/) : ces fichiers sont gros, régénérés
# périodiquement par un batch séparé (main.py publish_pivot / update_satool),
# et doivent être montés ou téléchargés au démarrage du conteneur - cf.
# api/README.md pour le mécanisme à mettre en place côté TeT.
FROM python:3.13-slim AS base

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra api --no-dev --no-install-project

COPY api/ api/
# Notes, formes et graphiques du classeur maître (api/excel_export.py)
COPY standalone_tool/classeur.py standalone_tool/classeur.py
COPY pipeline/ pipeline/
COPY sources/ sources/
COPY config/ config/
COPY main.py ./
COPY data/input/ data/input/

RUN useradd --create-home appuser \
    && mkdir -p data/output standalone_tool/output \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uv", "run", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
