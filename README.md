# Traj GES - Trajectoire GES territorialisée de la SNBC à l'échelle EPCI (v2)

Outil de calcul d'une **trajectoire climat de référence** par EPCI : la
trajectoire nationale SNBC3 (scénario AMS, run 3 DGEC ; run 3 bis pour les
émissions DROM) est répartie entre
territoires à l'aide de **pivots de territorialisation** (logements, emplois,
cheptels, parcs de véhicules...), corrigée du climat (DJU) et de la
démographie, puis **recalée** sur les données observées du territoire en
année de référence (2021). Émissions de GES, séquestration et consommation
d'énergie finale, 2020 à 2050.

Version 2 de l'outil développé pour l'ADEME (« Construction d'une
méthodologie et d'un outil pour élaborer des trajectoires climat de
référence adaptées à l'échelle infrarégionale »), après une v1 Excel testée
avec 13 territoires pilotes, et appliquée sur la SNBC2. Périmètre : les EPCI de France hexagonale (Corse comprise) et
des DROM (hors Mayotte), plus les 11 EPT de la Métropole du Grand Paris et
Paris.

L'outil se compose de trois éléments, alimentés par le même moteur :

| Composant | Rôle | Emplacement |
|---|---|---|
| **Pipeline de données** | Télécharge et retraite les sources ouvertes, produit la base des indicateurs par EPCI (`bdd_epci.parquet`) et la trajectoire territorialisée pour tous les territoires (`resultats_territorialises.parquet`) | `main.py`, `sources/`, `pipeline/` |
| **Classeur de restitution** | Interface utilisateur : sélection d'un territoire, calcul en formules pas à pas (onglet `CALCUL`), graphiques, comparaison aux trajectoires planifiée et observée | `standalone_tool/` |
| **API** | Calcul à la demande par territoire, avec surcharge des pivots, en vue de l'intégration dans Territoires en Transitions (TeT) | `api/` |

La méthode est décrite dans le **rapport méthodologique**
([docs/ADEME-Methodo-Outil-trajectoire-référence-V2.pdf](docs/ADEME-Methodo-Outil-trajectoire-référence-V2.pdf)),
qui fait référence. Ce README se limite à l'usage et à l'organisation du code.

---

## Documentation

| Document | Contenu | Public |
|---|---|---|
| [Rapport méthodologique (PDF)](docs/ADEME-Methodo-Outil-trajectoire-référence-V2.pdf) | Cadre, fonctionnement du classeur, méthode de calcul, pivots, recalage, extension DROM | Référence méthodologique |
| [standalone_tool/README.md](standalone_tool/README.md) | Génération du classeur, onglets reconstruits, template | Développeurs |
| [api/README.md](api/README.md) | Service HTTP, artefacts à régénérer, Docker, endpoints | Intégrateurs de l'API |
| [docs/resultats_territorialises.md](docs/resultats_territorialises.md) | Dictionnaire du parquet de sortie, légende `qualite` | Utilisateurs des données |
| [docs/territorialisation_drom.md](docs/territorialisation_drom.md) | Notes d'implémentation DROM (configuration, mécanismes, limites techniques, registre des anomalies) | Développeurs |
| [config/indicateurs.yaml](config/indicateurs.yaml) | Catalogue des indicateurs (code, libellé, unité, source, règle d'agrégation) | Source de vérité |
| [config/traj_supra.yaml](config/traj_supra.yaml) | Sous-secteurs SNBC3, pivots, corrections, recalage, réglages par zone | Source de vérité |
| `docs/Sources donnees.xlsx` | Catalogue des sources et des indicateurs (généré, `main.py generate_sources`) | Utilisateurs des données |
| `docs/Territorialisation.xlsx` | Colonnes du parquet et liste des `code_traj_supra` (généré, `main.py document_territorialize`) | Utilisateurs des données |

Les deux `.xlsx` sont générés depuis le code et la configuration, et ne sont
pas versionnés. Le rapport est versionné en PDF ; sa source Word n'est pas
versionnée. Après une modification du docx (révisions acceptées, date de la page
de garde mise à jour), réexporter le PDF depuis Word sous le même nom.

---

## Prérequis

- Python >= 3.13
- [uv](https://docs.astral.sh/uv/)
- Excel n'est pas requis, classeur compris (`update_satool`, écrit par openpyxl).
  Seuls les contrôles qui le font calculer l'utilisent (niveau de test `excel`,
  `scripts/verif_*_excel.py`), sous Windows ou macOS.

```bash
uv sync                 # pipeline et classeur
uv sync --extra api     # + service API
uv sync --extra excel   # + contrôles pilotant Excel (xlwings)
```

---

## Lancement

L'année par défaut est `ANNEE_REF` ([config/settings.py](config/settings.py), 2021).

```bash
# Pipeline complet : extract, dju, aggregate, export, territorialize (hexagone + DROM),
# update_satool, generate_sources, document_territorialize
uv run python main.py full

# Étapes unitaires
uv run python main.py extract --sources geo meteo insee_rp   # téléchargement des sources (cache dans data/raw/)
uv run python main.py dju                                    # DJU par EPCI, EPT et Paris
uv run python main.py aggregate                              # agrégation commune -> EPCI + totaux
uv run python main.py export                                 # bdd_epci.parquet + bdd_epci_wide.xlsx
uv run python main.py territorialize --zone HEXAGONE DROM   # trajectoire territorialisée (--csv pour le CSV)
uv run python main.py update_satool                          # injection dans le classeur

# Documentation générée
uv run python main.py generate_sources          # docs/Sources donnees.xlsx
uv run python main.py document_territorialize   # docs/Territorialisation.xlsx

# Carte des stations Météo-France
uv run python main.py carte

# Trajectoire nationale autre que la SNBC3 (options globales, avant la sous-commande)
uv run python main.py --traj-nationale "snw27_trajges.xlsx" --onglet "HEXAGONE=SnW27 HEX" \
    --nom-scenario SnW27 territorialize --zone HEXAGONE --sortie "resultats/snw27"
uv run python main.py --traj-nationale "snw27_trajges.xlsx" --onglet "HEXAGONE=SnW27 HEX" \
    --nom-scenario SnW27 update_satool --traj "resultats/snw27/Trajectoire GES SnW27.xlsx" --zone HEXAGONE
```

Trajectoire nationale : la SNBC3 de `data/input/SNBC3 retraitée.xlsx` par défaut
(`SNBC3_SHEETS`). `--traj-nationale` désigne un autre classeur au même format (un
onglet par zone, codes des sous-secteurs en colonne D, années 2020-2050 en
en-tête ; pour les DROM, l'onglet `Parts bilans DROM` en plus), par exemple un scénario exporté par VECTOR ; `--onglet ZONE=ONGLET`
(répétable) remplace toute la correspondance zone -> onglet, les zones non citées
ne sont pas territorialisables (DROM d'un scénario limité à l'hexagone) ;
`--nom-scenario` renseigne la plage `nom_scenario` du classeur. `territorialize
--sortie` écrit les résultats ailleurs que dans `data/output/`, `update_satool
--traj` vers un fichier absent le crée depuis le template.

Une source en échec (téléchargement, format inattendu) arrête `extract`, `aggregate`
et les commandes qui reconstruisent la BDD, après avoir tenté les autres sources : le
message liste toutes les sources en échec. Le bouquet OREC est retéléchargé au-delà
d'un mois de cache (`_RAW_CACHE_TTL_DAYS` dans `sources/energie_climat.py`), et un
avertissement signale un contenu modifié depuis le téléchargement précédent.

`--zone` accepte `HEXAGONE`, chaque DROM (`GUADELOUPE`, `MARTINIQUE`,
`GUYANE`, `REUNION`) ou le groupe `DROM`. Les mêmes tâches sont exposées par
[doit](https://pydoit.org/) (`uv run doit list`, cf. [dodo.py](dodo.py)).

Données manuelles, à déposer dans `data/input/` avant de lancer le pipeline :
- `SNBC3 retraitée.xlsx` (versionné) : trajectoire SNBC3 retraitée publiée, en valeurs
  seules (onglets retraités Kyoto, hexagone et DROM, cf. `SNBC3_SHEETS`, et parts des
  DROM dans les bilans énergie). Généré par `scripts/publier_snbc3.py` depuis le classeur
  de travail `data/input/travail/SNBC3 retraitée - travail.xlsx` (`SNBC3_TRAVAIL_PATH`, non
  versionné : onglets bruts DGEC, formules et détail du retraitement, bilans énergie
  par DROM), seul lu par `publier_snbc3.py` et `controle_snbc3.py` ;
- `260716_RPG2020_RA2020_COM_2025.xlsx` : RPG et RA 2020 à la commune (retraitement Solagro).

Toutes les autres sources sont téléchargées automatiquement. Liste,
licences et fréquences de mise à jour : `docs/Sources donnees.xlsx`.
Indicateurs produits : [config/indicateurs.yaml](config/indicateurs.yaml).

### Publication

```bash
uv run python main.py release --version 2.0
```

`main.py release` assemble un paquet figé dans `releases/V<version>/` (non versionné),
sans rien recalculer : `resultats_territorialises.parquet`, le classeur, le rapport
méthodologique en PDF et un `manifest.json`. Le manifeste contient la version, la date de
publication, le commit (avec un drapeau si des modifications ne sont pas commitées), le
manifeste des données, la couverture des résultats, et la taille et le sha256 de chaque
fichier.

Les fichiers du paquet sont ensuite joints à une release GitHub du dépôt, avec le tag
`v<version>` posé sur le commit noté dans le manifeste : publier depuis un arbre propre,
sur un commit déjà poussé.

Avant d'assembler, la commande vérifie que les produits viennent du même run : résultats
et classeur postérieurs au dernier `export`, et toutes les zones de
`ZONES_TERRITORIALISEES` présentes. Il faut donc lancer `full` (ou `export`,
`territorialize --zone HEXAGONE DROM` et `update_satool`) juste avant. Une version déjà
assemblée n'est écrasée qu'avec `--force`.

Dans le classeur publié, la mention de version de `Notice!B3` devient « Version 2.0 du
JJ/MM/AAAA à HHhMM », et l'alerte « version de travail » d'`INTRODUCTION!B4` est
retirée. Le classeur de `standalone_tool/output/` reste inchangé.

Le rapport est le PDF de `docs/`, repris tel quel : la commande échoue s'il est absent,
et avertit si le docx source local est plus récent (PDF à réexporter depuis Word).
`--sans-pdf` publie sans le rapport.

---

## Architecture

```
traj-ges/
├── main.py                  # CLI du pipeline
├── dodo.py                  # Mêmes tâches via doit
├── Dockerfile               # Image du service API
│
├── config/
│   ├── settings.py          # ANNEE_REF, chemins, zones, paramètres TERR_*
│   ├── indicateurs.yaml     # Catalogue des indicateurs
│   ├── traj_supra.yaml      # Sous-secteurs SNBC3 -> pivot / corrections / recalage + réglages par zone
│   └── anomalies_snbc3.yaml # Registre des anomalies des données DGEC (contrôles exécutables)
│
├── sources/                 # Un module par source
│   ├── geo_epci.py          # Insee + geo.api.gouv.fr : EPCI, EPT, correspondance commune -> EPCI, fusions
│   ├── meteo_france.py      # Températures quotidiennes Météo-France
│   ├── dju.py               # Calcul DJU (méthodes Météo et COSTIC, interpolation IDW)
│   ├── insee_rp.py          # INSEE RP : population, ménages, logements, emplois
│   ├── insee_omphale.py     # Projections démographiques Omphale
│   ├── flores.py            # FLORES : emplois salariés par filière industrielle
│   ├── agreste.py           # RPG + RA 2020 (Solagro) : SAU, cultures, cheptels
│   ├── mobilite_dt.py       # Émissions et consommations domicile-travail (INSEE x SDES)
│   ├── parc_vehicules.py    # Parc de véhicules SDES (VP, VUL, PL, bus)
│   ├── energie_climat.py    # OREC : consommations et émissions observées par EPCI
│   ├── citepa_igt.py        # CITEPA IGT : émissions observées aux DROM
│   └── aldo.py              # ADEME ALDO : flux UTCATF + surface forestière
│
├── pipeline/
│   ├── aggregate.py            # Commune -> EPCI/EPT, totaux France, zones et groupes
│   ├── export.py               # bdd_epci.parquet (+ manifeste), bdd_epci_wide.xlsx, résultats territorialisés
│   ├── territorialize.py       # Moteur de territorialisation (brut + recalé), trajectoires de zone
│   ├── overrides.py            # Surcharge des pivots d'un territoire (API)
│   └── territoire_resolver.py  # Résolution d'un code EPCI / EPT / commune (API)
│
├── standalone_tool/         # Génération du classeur Excel (cf. son README)
├── api/                     # Service FastAPI (cf. son README)
│
├── scripts/                 # Contrôles et rapports (cf. ci-dessous), générateurs de doc
├── tests/
│
├── data/
│   ├── input/               # Fichiers de référence manuels (versionnés)
│   │   └── travail/         # Classeur de travail SNBC3 (gitignoré, cf. publier_snbc3.py)
│   ├── raw/                 # Cache des téléchargements (gitignoré)
│   ├── interim/             # Parquet intermédiaires (gitignoré)
│   └── output/              # Produits et rapports de contrôle (gitignoré)
│
├── docs/                    # Rapport PDF et documentation (cf. tableau ci-dessus)
├── releases/                # Paquets de publication, joints aux releases GitHub (non versionné)
└── archives/                # Anciens dossiers de travail (non versionné)
```

### Produits (`data/output/`)

| Fichier | Contenu |
|---|---|
| `bdd_epci.parquet` | Indicateurs, format long : une ligne par territoire x indicateur x année, avec `unite`, `source`, `qualite` (`ok` / `interpolé` / `secret_stat` ; OREC, EPCI à cheval sur deux régions : `interpolé_cheval` / `extrapolé_cheval` / `cheval_non_verifiable`). Manifeste `bdd_epci_manifest.json` |
| `bdd_epci_wide.xlsx` | Mêmes données en format large (`CODE_ANNEE`), injecté dans l'onglet `Data_EPCI` du classeur |
| `resultats_territorialises.parquet` | Trajectoire par territoire x sous-secteur x année, brute et recalée. Cf. [dictionnaire](docs/resultats_territorialises.md) |

`niveau_geo` distingue `EPCI`, `EPT`, `COMMUNE` (Paris) et `TOTAL` (lignes
agrégées, `config.settings.CODES_TOTAUX`) : `FRANCE`, une ligne par zone
(`HEXAGONE` et chaque DROM, dénominateur des ratios de pivot) et une par
groupe (`DROM`). Stocks sommés, DJU et évolutions démographiques en moyenne
pondérée par la population.

---

## Tests et contrôles

Trois niveaux, par marqueurs pytest (cf. `tests/conftest.py`) :

```bash
uv run python -m pytest -m "not donnees and not excel"   # rapide (~5 s) : logique pure + cohérence doc
uv run python -m pytest                                  # défaut (~30 s) : + tests sur données réelles (cache data/interim)
uv run python -m pytest -m excel                         # convergence avec le classeur généré (plusieurs minutes)
```

Un `-m` passé en ligne de commande remplace le filtre par défaut (`not excel`) :
toujours y exclure `excel` explicitement.

Pas d'intégration continue : les tests `donnees` exigent le cache `data/interim`
(gitignoré, produit par `main.py aggregate`). Lancer le niveau défaut en local avant
d'ouvrir une pull request, et le niveau `excel` si la modification touche le classeur.

| Fichier | Couverture |
|---|---|
| `test_territorialize.py` | Moteur de territorialisation, recalage, mode additif UTCATF, zones et trajectoires DROM |
| `test_dju.py` | Calcul et interpolation des DJU |
| `test_ept.py` | EPT et Paris : agrégation, conservation, absence de double compte |
| `test_energie_climat.py` | OREC : valeur retenue par EPCI, EPCI à cheval sur deux régions, EPCI fusionnés, déduplication, substitution Grand Est, expiration du cache |
| `test_geo_epci.py` | Référentiel des EPCI : détection des fusions entre deux millésimes Insee, libellés des EPCI absents de geo.api.gouv.fr |
| `test_parc_vehicules.py` | Parc SDES : catégories des deux éditions du fichier, communes corses, codes fictifs exclus ; une source en échec arrête la BDD |
| `test_overrides.py`, `test_territoire_resolver.py`, `test_api.py` | Surcharges, résolution des codes, endpoints |
| `test_publier_snbc3.py` | Classeur SNBC3 publié : valeurs seules, sans onglet DGEC brut ; à jour du classeur de travail (skip si celui-ci est absent, en CI) |
| `test_release.py` | Paquet de publication : mention de version du classeur, rapport PDF, contrôles de cohérence des produits, manifeste |
| `test_docs.py` | Cohérence doc / code : modules, commandes et options cités, liens, identifiants entre backticks |
| `test_classeur.py` | Écriture du classeur sans Excel (`standalone_tool/classeur.py`) : conversions, formules recopiées, tableaux, graphiques ; génération complète si `bdd_epci_wide.xlsx` est présent |
| `test_excel_vs_python.py` | Convergence classeur / pipeline (classeur ouvert dans Excel puis enregistré, plusieurs minutes ; skip si absent ou non recalculé) |

Scripts de contrôle, non bloquants (écarts attendus par construction, sorties
CSV datées dans `data/output/`) :

```bash
uv run python scripts/controle_snbc3.py                 # Registre des anomalies des données DGEC (classeur de travail)
uv run python scripts/publier_snbc3.py                  # Classeur SNBC3 publié, depuis le classeur de travail
uv run python scripts/rapport_coherence_nationale.py    # Σ EPCI observé (OREC/ALDO) vs SNBC3 en année de référence
uv run python scripts/rapport_igt_vs_orec.py            # Substituabilité IGT/OREC, concentration aérien/maritime DROM
uv run python scripts/diag_secret_stat.py               # Ampleur du secret statistique, par source
uv run python scripts/verif_zone_excel.py --epci 249740119             # Classeur vs pipeline, une zone quelconque (Excel)
uv run python scripts/verif_tir_excel.py --departement 88 --max-epci 12   # Tirs EPCI individuels vs tir groupé (Excel)
uv run python scripts/rapport_comparaison_pilotes_v1.py "<dossier Retours territoires>"  # Outil v1 vs pipeline
```

Autres scripts : `generate_sources_doc.py`, `generate_territorialize_doc.py`,
`carte_stations.py` et `release.py` sont appelés par `main.py generate_sources`,
`document_territorialize`, `carte` et `release`.

---

## Faire évoluer l'outil

- **Ajouter un indicateur** : entrée dans [config/indicateurs.yaml](config/indicateurs.yaml),
  extraction dans `sources/`, mapping dans `pipeline/aggregate.py::build_bdd()`, puis `main.py full`.
- **Changer un pivot, une correction ou un recalage** : [config/traj_supra.yaml](config/traj_supra.yaml),
  puis `main.py territorialize` et `update_satool`.
- **Nouvelle livraison SNBC3** : mettre à jour le classeur de travail (enregistré dans
  Excel), rejouer `scripts/controle_snbc3.py`, puis `scripts/publier_snbc3.py`, versionner
  `data/input/SNBC3 retraitée.xlsx` et lancer `main.py full`.
- **Changer de scénario de référence** : fournir un onglet de même structure dans
  le classeur de travail (codes des sous-secteurs en colonne D), déclaré dans `SNBC3_SHEETS`
  et dans `ONGLETS_RETRAITES` (`scripts/publier_snbc3.py`), puis procéder comme pour une
  nouvelle livraison (rapport, §3.4.6). Pour une
  exécution seulement, sans toucher la configuration : `--traj-nationale` et `--onglet`
  (cf. Lancement).
- **Nouveau millésime** : `ANNEE_REF` dans [config/settings.py](config/settings.py).

---

## Notes techniques

- **EPT et Paris.** Les 11 EPT et Paris sont des subdivisions de la MGP, pas une partition
  du territoire. Paris (75056), hors EPT, porte `niveau_geo = "COMMUNE"` pour que EPT + Paris
  partitionnent exactement la MGP. Ils sont calculés pour les indicateurs issus de données
  communales, DJU (centroïde pondéré par la population, `compute_infra_centroids`) et Omphale
  compris. Pas de donnée OREC à cette maille : le recalage y dégrade en `non_recale_absent`,
  sauf l'UTCATF (ALDO). `check_conservation_ept` vérifie Σ EPT + Paris = MGP pour les seuls
  stocks. Les totaux (`compute_totaux`) et `ecart_conservation` excluent EPT et Paris pour ne
  pas compter deux fois la MGP.
- **Arrondissements PLM.** FLORES et la mobilité domicile-travail publient au niveau des
  arrondissements de Paris, Lyon et Marseille : `regrouper_arrondissements_plm` les regroupe
  sous leur commune avant agrégation.
- **Secret statistique.** Cellules masquées de l'INSEE (RP, FLORES) et données confidentielles
  OREC tracées `qualite = secret_stat`, propagé aux indicateurs dérivés
  (`compute_derived_indicators`). Un EPCI hérite du statut dès qu'une commune membre est masquée.
- **EPCI à cheval sur deux régions (OREC).** Les deux observatoires publient sans coordination : selon
  l'année, le CSV porte le total (`code_region` composé, `completude` = 1) ou la seule part d'une région.
  `sources/energie_climat.py` ne retient que les totaux et, à défaut pour l'année de référence, les
  interpole entre les années publiées (`interpolé_cheval`, `extrapolé_cheval`) ; sans aucun total
  publié pour l'indicateur, la valeur est gardée mais tracée `cheval_non_verifiable`. Sans cette règle,
  le recalage d'un EPCI ne reposant que sur sa part régionale s'effondre (facteur très inférieur à 1).
  Les autres sources, agrégées depuis les communes, ne sont pas concernées. Dans les sorties,
  `code_region` porte alors les deux codes (`27,84`).
- **DROM.** Une zone par DROM, rattachée au groupe `DROM`. Mayotte est dans la base mais
  n'est pas territorialisée. Détail : [docs/territorialisation_drom.md](docs/territorialisation_drom.md).
- **Correspondance commune -> EPCI.** Fichier Insee « EPCI au 01/01 » du millésime le plus
  récent, qui fournit aussi la liste de référence des EPCI : geo.api.gouv.fr suit le COG avec
  retard et ne sert plus qu'aux centroïdes et aux noms courts. Un EPCI issu d'une fusion au
  1er janvier, que le bouquet OREC publie encore sous les anciens codes, est recomposé par somme
  (qualité `fusion_epci`). Fusions détectées par comparaison avec le millésime précédent
  (cf. `sources/geo_epci.py`).
