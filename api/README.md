# API de territorialisation à la demande

Service HTTP (FastAPI) exposant le moteur de calcul Traj GES à la plateforme
ADEME Territoires en Transition (TeT). Génère à la demande les résultats de
territorialisation pour un territoire (EPCI, EPT de la Métropole du Grand
Paris, ou Paris), à partir des données pivot précollectées par le pipeline
batch, avec possibilité de surcharger certaines valeurs.

Cette page documente la procédure de mise à jour des artefacts, à lire avant
toute mise en production.

## Architecture en deux niveaux

1. **Batch** (`main.py`, existant) : produit les artefacts consommés par le
   service, à intervalle régulier.
2. **Service à la demande** (`api/`, ce dossier) : charge ces artefacts en
   mémoire au démarrage et sert les calculs à la demande, sans réseau ni
   dépendance à Excel/Windows en fonctionnement normal.

## Artefacts à régénérer périodiquement

### 1. Données pivot (obligatoire)

```bash
uv run python main.py export --annee 2021
```

Produit `data/output/bdd_epci.parquet` et son manifeste
`data/output/bdd_epci_manifest.json` (date de génération, nombre de
lignes/territoires), aux côtés de `bdd_epci_wide.xlsx` (mêmes conventions :
toujours écrasés, pas de version datée - c'est la même commande qui produit
déjà `bdd_epci_wide.xlsx` pour l'outil standalone). Le service lit le
Parquet et son manifeste au démarrage - il suffit de les remplacer et de
**redémarrer le service** (pas de rafraîchissement à chaud dans cette
version : `GET /version` confirme après coup la date effectivement chargée).

Cette commande relance `pipeline.aggregate.build_bdd()`, qui télécharge/
recalcule toutes les sources externes (INSEE, Météo-France, ALDO, etc.) -
opération de plusieurs minutes, à exécuter sur une machine ayant accès à ces
sources (pas nécessairement celle qui exécute le service).

### 2. Classeur Excel maître (optionnel, pour l'endpoint `/excel`)

```bash
uv run python main.py update_satool
```

Écrit par openpyxl : **ni Excel ni Windows requis**, la commande tourne sur
la même machine que `export` (elle lit `data/output/bdd_epci_wide.xlsx`).
Produit `standalone_tool/output/Trajectoire GES de référence-V2.xlsx`,
que le service ouvre en lecture à la demande (copie modifiée à la volée,
jamais régénéré par le service lui-même). Sans ce fichier, `/health` et les
autres endpoints fonctionnent normalement ; seul `POST
/territorialisation/{code}/excel` répond 503.

## Cadence recommandée

Pas de cadence imposée par une source amont identifiée à ce jour : à caler
sur la fréquence de mise à jour des données publiques consommées par le
pipeline (INSEE RP, énergie-climat, etc. - généralement annuelle) ou sur les
besoins de la plateforme d'accueil.

## Distribution vers l'infrastructure d'accueil

Le mécanisme dépend de l'infrastructure d'accueil. Options possibles : objet
de stockage partagé tiré au
démarrage du conteneur, image Docker reconstruite à chaque publication,
release Git LFS. Quel que soit le choix, la procédure ci-dessus (commandes
`export`/`update_satool` + redémarrage + vérification via
`GET /version`) reste la même.

## Lancer le service en local

```bash
uv sync --extra api
uv run python main.py export --annee 2021    # au moins une fois
uv run uvicorn api.main:app --reload
```

Variables d'environnement (préfixe `TRAJGES_API_`, cf. `api/config.py`) :
- `TRAJGES_API_API_KEYS` : clés API acceptées, séparées par des virgules.
  Vide (défaut) = authentification désactivée, à ne jamais laisser vide en
  production.
- `TRAJGES_API_PIVOT_DIR` : dossier contenant `bdd_epci.parquet` +
  `bdd_epci_manifest.json` (défaut `data/output/`).
- `TRAJGES_API_MASTER_WORKBOOK_PATH` : chemin du classeur maître (défaut
  `standalone_tool/output/Trajectoire GES de référence-V2.xlsx`).

## Conteneur Docker

Le `Dockerfile` (racine du dépôt) embarque le code, ses dépendances et les
entrées obligatoires de `territorialize()` (`config/`, `data/input/`) - PAS
les artefacts pivot/classeur (gros, régénérés séparément, cf. ci-dessus) :
à monter en volume au démarrage du conteneur.

```bash
docker build -t traj-ges-api .

docker run --rm -p 8000:8000 \
  -e TRAJGES_API_API_KEYS=<clé_a_definir> \
  -v "$(pwd)/data/output/bdd_epci.parquet:/app/data/output/bdd_epci.parquet:ro" \
  -v "$(pwd)/data/output/bdd_epci_manifest.json:/app/data/output/bdd_epci_manifest.json:ro" \
  -v "$(pwd)/standalone_tool/output:/app/standalone_tool/output:ro" \
  traj-ges-api
```

Le dernier volume (classeur maître) est optionnel - son absence dégrade
seulement `POST /territorialisation/{code}/excel` (503), le reste du
service fonctionne normalement. Un `HEALTHCHECK` (`GET /health`) est déjà
défini dans l'image. Pour alimenter ces volumes en production, cf.
"Distribution" ci-dessus : les montages ci-dessus en sont la version
locale/manuelle.

## Endpoints

| Endpoint | Auth | Description |
|---|---|---|
| `GET /health` | non | Etat du service (pivot chargé ou non) |
| `GET /version` | non | Version du code + version des données pivot chargées |
| `GET /territoires/{code}/pivots` | oui | Valeurs pivot brutes d'un territoire |
| `POST /territorialisation/{code}` | oui | Résultats territorialisés (+ overrides optionnels) |
| `POST /territorialisation/{code}/excel` | oui | Classeur Excel pointé sur ce territoire (+ overrides) |

`{code}` accepte un code EPCI, un code EPT, ou un code commune (résolu vers
son EPT/EPCI parent - cf. `pipeline/territoire_resolver.py`). Documentation
interactive : `/docs` (Swagger) une fois le service lancé.

## Performance d'un appel avec override

Le ratio pivot a structurellement besoin de la zone entière (dénominateur),
mais `territorialize(..., epci_unique=...)` restreint tout le reste du calcul
(jointure trajectoire nationale, corrections, recalage, tri) au seul
territoire demandé au lieu de matérialiser les ~2,5M lignes de la zone
HEXAGONE pour n'en garder qu'une poignée à la fin. De même,
`pipeline.overrides.apply_overrides()` ne recalcule les totaux de zone que
pour les indicateurs effectivement affectés (l'indicateur overridé, et les
indicateurs à moyenne pondérée par population si POP est overridé), pas tout
le catalogue. Mesuré sur données réelles (zone HEXAGONE, ~1200 territoires) :
un appel avec override complet (override + territorialisation) prend
environ 1,5 à 2 secondes, contre 10 à 35 secondes avant ces deux
optimisations.

Conséquence documentée : `ecart_conservation` (diagnostic de conservation
nationale, calculé sur la zone entière) vaut toujours `None` dans une
réponse API - seul le calcul batch complet (zone entière) le fournit.

## Limites connues

- Millésime EPCI : aucune table de passage COG 2020 (TeT) - millésime le
  plus récent (pipeline) n'existe. Un territoire dont le périmètre a changé
  entre les deux millésimes peut être renvoyé "non résolu" (404).
- Format de sortie, authentification (clé API statique) et mécanisme de
  distribution des artefacts : à adapter à la plateforme d'accueil.
