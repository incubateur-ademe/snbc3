# `resultats_territorialises.parquet` - dictionnaire de données

Référence colonne par colonne du fichier de sortie de la territorialisation
SNBC3. Méthode de calcul (formules, pivots, recalage) : rapport
méthodologique, chapitre 3
([ADEME-Methodo-Outil-trajectoire-référence-V2.pdf](ADEME-Methodo-Outil-trajectoire-référence-V2.pdf)).

---

## Fichier

`data/output/resultats_territorialises.parquet`, format long : une ligne =
1 EPCI (ou EPT/Paris, cf. ci-dessous) x 1 sous-secteur (`code_traj_supra`) x
1 année, 2020 à 2050 (environ 2,5 millions de lignes). Généré par
`uv run python main.py territorialize --zone HEXAGONE DROM` (`territorialize()`,
[pipeline/territorialize.py](../pipeline/territorialize.py)) et écrit par
`to_territorialise_csv()` ([pipeline/export.py](../pipeline/export.py)). Le
CSV équivalent, une quinzaine de fois plus lourd, n'est écrit qu'avec `--csv`.

**EPT et Paris.** Les 11 établissements publics territoriaux et Paris (subdivisions
de la Métropole du Grand Paris) sont territorialisés comme n'importe quel
EPCI, mais exclus de `ecart_conservation` (sinon la contribution de la MGP
serait comptée deux fois). Leur recalage dégrade proprement vers `qualite =
"non_recale_absent"` (facteur 1, pass-through) là où aucune donnée OREC/IGT
n'existe à cette maille - sauf pour l'UTCATF, calé sur ALDO qui est
disponible à l'échelle EPT.

## Colonnes

| Colonne | Type | Description | Exemple |
|---|---|---|---|
| `code_epci` | str | Code SIREN de l'EPCI, ou code EPT/Paris (cf. ci-dessus) | `200000172` |
| `nom_epci` | str | Nom de l'EPCI | `CC Faucigny - Glières` |
| `niveau_geo` | str | `EPCI`, `EPT` ou `COMMUNE` (Paris) : les EPT et Paris subdivisent la Métropole du Grand Paris, à exclure d'une somme de territoires | `EPCI` |
| `zone` | str | Zone de territorialisation (`HEXAGONE`, `GUADELOUPE`, `MARTINIQUE`, `GUYANE`, `REUNION` ; Mayotte non produite) | `HEXAGONE` |
| `code_region` | str | Code région INSEE de l'EPCI ; deux codes séparés par une virgule pour un EPCI à cheval sur deux régions (cf. [EPCI à cheval](#epci-à-cheval-sur-deux-régions)) | `84` |
| `code_departement` | str | Code département INSEE de l'EPCI | `74` |
| `code_traj_supra` | str | Sous-secteur de la trajectoire nationale SNBC3 (66 valeurs dans l'hexagone, 57 pour chaque DROM) | `emi_UTCATF_for` |
| `type` | str | `GES` (émissions) ou `conso` (consommation d'énergie) | `GES` |
| `secteur` | str | Secteur d'activité (9 valeurs : Agriculture, Branche énergie, CSC, Déchets, Industrie, Résidentiel, Tertiaire, Transports, UTCATF) | `UTCATF` |
| `usage` | str | Usage / sous-catégorie au sein du secteur (peut être vide) | `Forêts` |
| `code_pivot` | str | Indicateur EPCI utilisé comme clé de répartition de la trajectoire nationale | `ALDO_UTCATF_FOR` |
| `unite` | str | `MtCO2e` ou `TWh` | `MtCO2e` |
| `annee` | int | 2020 à 2050 | `2020` |
| `valeur_brute` | float | Résultat avant recalage : `trajectoire de zone × pivot_ratio × corrections` (trajectoire de zone : nationale de l'hexagone, ou dérivée de la trajectoire DROM pour chaque DROM) | `-0.008855` |
| `valeur_recalee` | float | `valeur_brute` recalée pour recoller à l'observé en année de référence - **colonne à utiliser par défaut** | `-0.006333` |
| `code_recalage` | str | Code de l'indicateur observé (OREC dans l'hexagone, CITEPA IGT aux DROM) servant de recalage ; vide si aucun ne s'applique | `ALDO_UTCATF_FOR` |
| `recal_factor` | float | Facteur appliqué = observé(année réf) / Σ `valeur_brute`(année réf) | `0.690459` |
| `ecart_conservation` | float | Écart relatif Σ EPCI de la zone vs trajectoire de la zone (EPT/Paris exclus de la somme), par sous-secteur/année (diagnostic). Aux DROM, la part de Mayotte non produite n'y figure pas : elle est déjà retirée des trajectoires de zone | vide |
| `qualite` | str | Fiabilité du recalage pour la ligne - voir légende | `ok` |

## Sous-secteurs (`code_traj_supra`)

Les 66 sous-secteurs de la trajectoire nationale SNBC3 (57 pour chaque DROM,
UTCATF étant désactivé pour le groupe DROM) sont définis dans [config/traj_supra.yaml](../config/traj_supra.yaml)
- la source de vérité. Liste complète, à jour à chaque régénération :
**`docs/Territorialisation.xlsx`**, onglet `Sous-secteurs`.

```bash
uv run python main.py document_territorialize
```

Généré depuis `TERRITORIALISE_COLUMNS` et `load_correspondance()`
([pipeline/territorialize.py](../pipeline/territorialize.py)) par
[scripts/generate_territorialize_doc.py](../scripts/generate_territorialize_doc.py)
- pas de liste recopiée à la main ici, pour ne pas dupliquer une maintenance
déjà faite dans le yaml. Régénéré à chaque
pipeline complet ; sinon lancer la commande ci-dessus après toute modification
de `config/traj_supra.yaml`. Détail des pivots DROM par sous-secteur :
[docs/territorialisation_drom.md](territorialisation_drom.md).

## Légende `qualite`

| Valeur | Signification |
|---|---|
| `ok` | Recalage appliqué normalement (observé disponible, secteur non nul en année de référence) |
| `pass_through_sans_recalage` | Pas de `code_recalage` défini pour ce sous-secteur : `valeur_brute` reprise telle quelle |
| `pivot_absent` | Aucun pivot disponible pour répartir la trajectoire entre EPCI : résultat indéterminé |
| `non_recale_absent` | Donnée observée manquante pour le recalage : `recal_factor` laissé à 1 |
| `non_recale_secteur_nul` | Somme brute nulle en année de référence pour ce (EPCI, secteur) : `recal_factor` laissé à 1 |

Répartition sur le millésime 2021 (hexagone et DROM) : 86 % `ok`, 9 % `pass_through_sans_recalage`
(UTCATF hors inventaire, CSC), 2 % `pivot_absent`, 2 % `non_recale_absent` (consommations aux DROM,
EPT et Paris), moins de 1 % `non_recale_secteur_nul`.

## EPCI à cheval sur deux régions

Quelques EPCI relèvent de deux observatoires OREC, qui ne coordonnent pas leurs
publications : selon l'année, la donnée observée est le total de l'EPCI ou la seule
part située dans une région. Le recalage ne retient que les totaux (interpolés entre
les années publiées si l'année de référence n'en a pas), sans quoi `recal_factor`
s'effondrerait pour ces EPCI. La qualité de la donnée observée figure dans
`bdd_epci.parquet` (colonne `qualite` des lignes `EC_EMIS_*` / `EC_CONSO_*` :
`interpolé_cheval`, `extrapolé_cheval`, `cheval_non_verifiable` quand aucun total n'est
publié pour l'indicateur) ; elle n'est pas reportée dans la colonne `qualite` de ce
fichier. Le filtre par région d'un EPCI à cheval doit donc tester les deux codes de
`code_region`. Règle et justification : [sources/energie_climat.py](../sources/energie_climat.py).

## EPCI issus d'une fusion

Les EPCI et leur composition communale sont ceux du dernier millésime Insee. Quand un
EPCI issu d'une fusion au 1er janvier est encore publié par l'OREC sous les codes des EPCI
qu'il réunit, sa donnée observée est leur somme, tracée `fusion_epci` dans la colonne
`qualite` de `bdd_epci.parquet`. Si l'OREC ne publie aucun des anciens EPCI, le nouvel EPCI
n'a pas d'observé et ses lignes sont `non_recale_absent`. Règle :
[sources/energie_climat.py](../sources/energie_climat.py).

## Limite connue : pivots PL et bus

`emi_fret_pl` et `emi_mob_vp_bus` sont répartis selon le parc immatriculé
(`PARC_PL`, `PARC_BUS`, [sources/parc_vehicules.py](../sources/parc_vehicules.py)),
localisé au siège social plutôt qu'au lieu de circulation (fret longue
distance, autocars interurbains). Le recalage borne le total routier de
chaque EPCI ; seule la répartition entre catégories routières est affectée,
et l'effet décroît jusqu'en 2050. Aucun pivot ouvert plus fin n'a été
identifié : le TMJA Cerema ne couvre que le réseau national non concédé,
l'enquête TRM du SDES est régionale, les km parcourus par réseau AOM ne sont
pas publiés (rapport, §3.5.5).

## Voir aussi

- Méthode de calcul : rapport méthodologique, chapitre 3
- Dictionnaire généré (colonnes + `code_traj_supra`, toujours à jour) : `docs/Territorialisation.xlsx` (`main.py document_territorialize`)
- Spécificités DROM : rapport, chapitre 4, et [territorialisation_drom.md](territorialisation_drom.md) pour l'implémentation
