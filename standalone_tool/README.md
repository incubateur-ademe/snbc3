# Classeur « Trajectoire GES de référence-V2 »

Génération du classeur de restitution à partir des sorties du pipeline.
[update_standalone_tool.py](update_standalone_tool.py) écrit le classeur avec
openpyxl, par la couche [classeur.py](classeur.py) : **ni Excel ni Windows
requis** (Linux, macOS). Fonctionnement du classeur côté utilisateur
(onglets, parcours, onglet `CALCUL`) : rapport méthodologique, chapitre 2.

```
standalone_tool/
├── update_standalone_tool.py   # Construction des onglets
├── classeur.py                 # Accès au classeur (openpyxl) : plages, tableaux, noms, graphiques
├── template/   # Squelette versionné : Trajectoire GES de référence-V2-template.xlsx
└── output/     # Livrable généré (gitignoré) : Trajectoire GES de référence-V2.xlsx
```

## Génération

```bash
# Point d'entrée simple (--traj, --sheet, --zone)
uv run python main.py update_satool
uv run python main.py --traj-nationale "snw27_trajges.xlsx" --onglet "HEXAGONE=SnW27 HEX" \
    --nom-scenario SnW27 update_satool --traj "Trajectoire GES SnW27.xlsx" --zone HEXAGONE

# Appel direct, toutes les options
uv run python -m standalone_tool.update_standalone_tool                # template -> output, puis injection
uv run python -m standalone_tool.update_standalone_tool "chemin.xlsx"  # modification en place
uv run python -m standalone_tool.update_standalone_tool --only-calcul  # Traj_supra + onglets calculés, sans réinjecter Data_EPCI
```

Sans chemin, le classeur part du template et est écrit dans `output/`. Avec
`main.py update_satool --traj`, un fichier absent est créé depuis le template,
un fichier existant est modifié en place. Le fichier cible doit être fermé
dans Excel (sinon l'écriture échoue, avec un message explicite).

Le classeur est enregistré **sans valeurs calculées** : Excel recalcule tout à
l'ouverture (`fullCalcOnLoad`). Un lecteur sans moteur de calcul (aperçu de
fichier, `openpyxl` en `data_only`) n'y voit que des formules. Pour disposer
des valeurs, l'ouvrir dans Excel puis l'enregistrer.

| Option | Description | Défaut |
|---|---|---|
| `--wide <path>` | Source des pivots | `data/output/bdd_epci_wide.xlsx` |
| `--sheet <nom>` | Onglet cible des pivots | `Data_EPCI` |
| `--zone <ZONE...>` | Zones portées par le classeur ; `DROM` désigne les 4 DROM produits | `ZONES_TERRITORIALISEES` |
| `--no-traj-supra` | Ne pas réinjecter les valeurs SNBC3 dans `Traj_supra` | injection activée |
| `--no-calcul` | Ne pas reconstruire `CALCUL` et les onglets de visualisation | construction activée |
| `--only-calcul` | Seulement `Traj_supra` et les onglets calculés | - |
| `--reset-template` | Reporter l'output vers le template (voir plus bas) | - |

## Ce que fait la génération

1. **`Data_EPCI`** : `table.update(df)` met à jour le tableau structuré en
   place, sans le détruire, pour que les formules qui s'y réfèrent restent
   valides (écriture directe en A1 si l'onglet n'a pas de tableau).
   `Catalogue_data_EPCI` est mis à jour dans la foulée.
2. **`Traj_supra`** : valeurs nationales 2020-2050, SNBC3 de `data/input/SNBC3 retraitée.xlsx`
   par défaut (autre trajectoire : options globales `--traj-nationale`, `--onglet`
   et `--nom-scenario` de `main.py`, cf. README), appariées sur le couple
   (`Code traj supra`, `Zone`) et sur les seules colonnes-années. Les colonnes de métadonnées (pivot, corrections, recalage)
   et l'ordre des lignes sont préservés.
3. **`Territoire`** : blocs ⓪ (zone), ① (sélection), ② (pivots), ③ (recalage).
4. **`CALCUL`** : territorialisation en formules, sept lignes par sous-secteur
   (colonne `Étape` : valeur traj supra, ratio pivot, correction DJU,
   correction démo, résultat brut, ratio recalage, résultat recalé).
5. **Onglets de visualisation** : les 9 onglets sectoriels (`RESIDENTIEL`
   à `CSC`) et `TOUS SECTEURS`, branchés sur `CALCUL` par `SUMIFS`. Un onglet
   absent du classeur est signalé et ignoré.
6. **Mention de version** : `Notice!B3` (reprise par `INTRODUCTION!B3`) est
   datée du jour de génération (« Version 2 du JJ/MM/AAAA ») et l'alerte
   `ALERTE_VERSION` est écrite en rouge dans `INTRODUCTION!B4`. La passer à
   `None` quand l'outil est finalisé. `main.py release` remplace cette mention
   par le numéro de version publié et retire l'alerte, sur la copie publiée seulement
   (cf. [README principal](../README.md#publication)). Avec une trajectoire nationale autre que la
   SNBC3 (`--nom-scenario` de `main.py`), les titres d'`INTRODUCTION` et de
   `Notice` portent le nom du scénario, et l'alerte le signale ; les autres textes
   restent ceux de la SNBC3.

## Un seul classeur pour toutes les zones

Le classeur porte toutes les zones à la fois : une ligne de `Traj_supra` par
couple (sous-secteur, zone), identifiée par la colonne `Clé` (`code|zone`).
La trajectoire d'un DROM y est injectée déjà dérivée, par le même résolveur
que le pipeline (`pipeline.territorialize.trajectoire_zone`), à partir des
totaux de `Data_EPCI` : `CALCUL` n'a aucune étape propre aux DROM.

Les colonnes `Zone` et `Clé` sont ajoutées si elles manquent, **en fin de
tableau** : insérer en tête décalerait `Traj_supra!$C$1:$C$3` (`nom_scenario`,
`echelon_scenario`, `Annee_ref`), dont dépend tout `CALCUL`.

La zone est **déduite de la sélection** : le bloc ⓪ de `Territoire` compte
les EPCI sélectionnés par zone et définit la plage nommée `terr_zone`. Tout ce
qui en dépend est résolu par formule sur la clé :

| Élément | Résolution |
|---|---|
| Valeur de zone | `INDEX(Traj_supra[], MATCH(code&"\|"&terr_zone, Traj_supra[Clé], 0), …)` |
| Code pivot, code de recalage | même recherche sur la clé |
| Activation des corrections DJU / démo | flags de `Traj_supra` pour la zone |
| Total de zone (dénominateur des ratios) | `MATCH(terr_zone, Data_EPCI[code_epci], 0)` |

`CALCUL` couvre l'**union** des sous-secteurs de toutes les zones. Un
sous-secteur absent de la zone retenue (UTCATF aux DROM) fait échouer la
recherche et vaut 0.

**Garde-fous.**
- Sélection mélangeant plusieurs zones, ou vide : `terr_zone = ""`, résultats
  neutralisés à 0, message en rouge dans le bloc ⓪.
- Zone dont la trajectoire est incalculable (onglet SNBC3 ou de parts DROM absent) :
  signalée à la génération, ses lignes ne sont pas créées, ses territoires
  produisent des zéros. Les EPCI d'une zone non territorialisée (Mayotte) ne
  sont pas proposés.
- Pivot ou donnée de recalage sans valeur pour la sélection (EPT, secret
  statistique) : la valeur remonte vide, pas un faux zéro, et la cellule
  « Correction manuelle » est surlignée en orange
  (`_warn_valeur_territoire_absente`) tant qu'elle n'est pas renseignée.
- Recalage important : le bloc ③ affiche, par donnée de recalage, le ratio
  de la sélection, celui de la zone et leur rapport (« Écart pivot », rapport
  méthodologique, 3.6). Orange au-delà d'un facteur 2, rouge au-delà de 3 ou si
  l'écart est négatif, pour un secteur pesant au moins 10 % du total du territoire
  (hors UTCATF, part observée ou attendue d'après le pivot) ; synthèse en `T4`.
  Seuils : `SEUIL_ECART_PIVOT_*` et `SEUIL_PART_RECALAGE`.
- Avant reconstruction, les critères de filtre sur `CALCUL` sont vidés et les
  lignes réaffichées (sinon elles resteraient masquées).

## Mise à jour du template

Le template ne contient que le squelette : structure des onglets, plages
nommées, données de référence (`Traj_observée`, `Traj_planifiée`) et une ligne
de données par table.

openpyxl, qui écrit le classeur (génération comme endpoint `/excel` de
l'API), perd au chargement les formes et zones de texte, ainsi que les
commentaires à thread. D'où trois règles pour le template :
- **Formes** (flèches, zones de texte de `Traj_planifiée` et `Traj_observée`) :
  uniquement dans des onglets que la génération ne reconstruit pas, et sans
  image dans le même onglet. `classeur.finaliser` les recopie après
  l'enregistrement depuis le fichier d'origine (template, ou classeur maître
  pour l'API).
- **Commentaires** : notes classiques uniquement, leur taille est recalculée
  d'après le texte.
- **Images** : conservées (pillow requis).

Garde-fous : `TestClasseurMaitreOpenpyxl` dans `tests/test_api.py`,
`test_generation_complete` dans `tests/test_classeur.py`.

Pour y reporter des modifications faites dans l'output :

```bash
uv run python -m standalone_tool.update_standalone_tool --reset-template            # depuis l'output standard
uv run python -m standalone_tool.update_standalone_tool --reset-template chemin.xlsx
```

Le nettoyage :
- **vide** les onglets reconstruits : `CALCUL`, `Territoire`, les 9 onglets
  sectoriels et `TOUS SECTEURS` (contenu et graphiques) ;
- **réduit** `Data_EPCI`, `Traj_supra` et `Catalogue_data_EPCI` à une ligne
  (formules de `Catalogue_data_EPCI` conservées) ;
- **conserve** `Traj_observée`, `Traj_planifiée`, les plages nommées
  (`nom_scenario`, `Annee_ref`, `terr_num_epci`, `terr_zone`) et les colonnes
  `Zone` et `Clé` de `Traj_supra`.

## Écriture sans Excel

[classeur.py](classeur.py) reprend la sémantique d'Excel là où le classeur en
dépend : une chaîne numérique écrite devient un nombre, une formule affectée
à une plage est recopiée avec décalage des références relatives, une formule
qui reprend une cellule (`=X`, `=SUM(X:Y)`...) en hérite le format. Les
graphiques sont écrits au format de ceux qu'Excel crée (style 102 avec repli
sur le style 2, couleurs automatiques, option « #N/A comme vide ») et portent
des caches de séries vides : sans eux, Excel ignore à l'ouverture les entrées
de légende supprimées. Les parties positive et négative
d'une série scindée reçoivent explicitement la couleur automatique d'Excel
(`couleur_auto`), relevée sur Excel selon le nombre de séries du graphique.

Les largeurs de colonnes du module sont les valeurs stockées dans le fichier,
pas `ColumnWidth` d'Excel (dont la conversion dépend de la résolution de
l'écran). Les graphiques sont ancrés sur une cellule, à taille fixe.

Les formes, qu'openpyxl ne conserve pas, sont recopiées depuis le fichier
d'origine (cf. « Mise à jour du template »).

## Vérification

- `tests/test_excel_vs_python.py` : convergence `CALCUL` / pipeline (lent,
  nécessite un classeur généré, ouvert dans Excel puis enregistré).
- `scripts/verif_zone_excel.py --epci <code...>` : même contrôle pour une zone
  quelconque, et garde-fou multi-zone si plusieurs codes de zones différentes.
- `scripts/verif_tir_excel.py` : somme des tirs EPCI individuels vs tir groupé.

Ces trois contrôles font calculer Excel (Windows ou macOS, `uv sync --extra excel`).
