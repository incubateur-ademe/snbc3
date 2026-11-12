# Extension DROM - notes d'implémentation

La méthode (choix de l'IGT, asymétrie GES / énergie, zone par DROM et
trajectoire dérivée, Mayotte, aérien et maritime, résultats, choix retenus)
est décrite au **chapitre 4 du rapport méthodologique**
([ADEME-Methodo-Outil-trajectoire-référence-V2.pdf](ADEME-Methodo-Outil-trajectoire-référence-V2.pdf)).
Ce document ne reprend que ce qui sert à maintenir le code : où vit chaque
mécanisme, ce qui est vérifié et par quoi, et les limites techniques ouvertes.

Vocabulaire : « hexagone » désigne l'Hexagone, Corse comprise, comme dans le
format Secten du Citepa (zone `HEXAGONE`, onglet `SNBC3 retraitée HEX`).

Principe : **la zone est un paramètre, pas un fork**. Même moteur, même
classeur, même jeu de sous-secteurs ; tout le comportement DROM est porté
par la configuration.

---

## Lancement

```bash
uv run python main.py territorialize --zone HEXAGONE DROM   # hexagone + 4 DROM, un seul fichier
uv run python main.py territorialize --zone GUYANE           # un seul DROM
```

Une zone en échec n'interrompt pas les autres mais est signalée en fin
d'exécution. Un onglet `SNBC3 retraitée DROM` ou `Parts bilans DROM` manquant
dans `data/input/SNBC3 retraitée.xlsx` fait échouer les DROM avec un message
indiquant l'onglet à produire.

## Où vit chaque mécanisme

| Mécanisme | Emplacement |
|---|---|
| Zones, groupe DROM, zones produites (sans Mayotte) | `ZONE_PAR_REGION_DROM`, `GROUPES_ZONES`, `ZONES_TERRITORIALISEES` ([config/settings.py](../config/settings.py)) |
| Onglet retraité par zone ou groupe | `SNBC3_SHEETS` ([config/settings.py](../config/settings.py)) |
| Réglages par zone ou groupe | bloc `zones` de [config/traj_supra.yaml](../config/traj_supra.yaml) |
| Surcharges de pivot par sous-secteur | clé `zones` du sous-secteur, même fichier |
| Trajectoire de chaque DROM | `trajectoire_zone()`, `parts_bilans()` ([pipeline/territorialize.py](../pipeline/territorialize.py)), bloc `trajectoire_derivee` |
| Données observées DROM (IGT) | [sources/citepa_igt.py](../sources/citepa_igt.py), restreint à `IGT_ZONES` |
| Même trajectoire dans le classeur | `_update_traj_supra()` ([standalone_tool/update_standalone_tool.py](../standalone_tool/update_standalone_tool.py)) appelle `trajectoire_zone()` sur les totaux de `Data_EPCI` |

### Réglages (bloc `zones`)

Les réglages du groupe DROM valent pour chacune de ses zones, qui peuvent les
compléter ; idem pour les surcharges de pivot.

| Réglage | Niveau | Valeur |
|---|---|---|
| `corrections_dju` | DROM | `chaud: false`, `froid: false` |
| `secteurs_desactives` | DROM | `[UTCATF]` |
| `trajectoire_derivee` | DROM | onglet de parts (`onglet_parts`), onglet de bilan du groupe, types et secteurs calés, `annee_part_hors_perimetre` |
| `onglet_bilan` | chaque DROM | `Bilans énergie <territoire>` |

Les onglets de bilans ne sont que dans le classeur de travail (non versionné) :
`scripts/publier_snbc3.py` en tire les parts de chaque DROM, seules publiées
(`onglet_parts`), et le pipeline lit ces parts.

`corrections_dju` ne peut qu'**éteindre** une correction, jamais en activer
une que le sous-secteur ne prévoit pas.

| Sous-secteur | Pivot hexagone | Pivot DROM |
|---|---|---|
| `emi_mob_vp_domtra`, `cons_mob_vp_domtra` | `MOB_DT_*` | `PARC_VP` |
| `cons_agr` | `EC_CONSO_AGRICULTURE` | `SAU` |
| `cons_mob_vp_autre` | `EC_CONSO_TRANSPORT_ROUTIER` | `EC_EMIS_TRANSPORT_ROUTIER` |
| `cons_tra_autres` | `EC_CONSO_AUTRE_TRANSPORT` | `EC_EMIS_AUTRE_TRANSPORT` |
| `cons_dech` | `EC_CONSO_DECHETS` | `EC_EMIS_DECHETS` |

Les `EC_CONSO_*` servant **uniquement** de recalage ne demandent aucune
surcharge : absents de la BDD sur la zone, ils dégradent d'eux-mêmes en
`recal_factor = 1`, `qualite = "non_recale_absent"`. C'est ce qui produit
l'asymétrie GES / énergie sans code dédié.

Résultat : 57 sous-secteurs par DROM (66 dans l'hexagone), 31 pivots, tous
disponibles dans les quatre DROM produits.

### IGT : conventions d'intégration

- Alimente les mêmes codes `EC_EMIS_*` que l'OREC. La provenance reste dans la
  colonne `source` (`OREC` / `CITEPA_IGT`).
- Garde-fou dans `build_bdd()` : tout couple (EPCI, indicateur) déjà couvert
  par une autre source est écarté. L'IGT complète, il ne remplace jamais.
- Déjà au format PCAET : pas de conversion SECTEN. CO2 biogénique, soutes
  internationales et exports exclus du total (vérifié sur l'onglet
  `Synthèse FRDOM`), tCO2e -> ktCO2e. Les soutes restent exposées à part dans
  `IGT_EMIS_TRANSPORT_INTL`, à titre de diagnostic.

### Mayotte

Absente de `ZONES_TERRITORIALISEES` mais présente dans la BDD et comptée dans
le total du groupe DROM. L'ajouter à `ZONES_TERRITORIALISEES` dès qu'une source
INSEE la couvrant est branchée.

Correction de reventilation : pour un pivot qui ne couvre pas Mayotte (`MEN`,
`RP*`, `EMP_TER`, `EMP_IND_*`), la part d'un DROM est multipliée par
`(1 - part de Mayotte dans la conso du secteur)`, lue dans les bilans à
`annee_part_hors_perimetre` (2019). Points d'implémentation :
- part **énergétique**, pas démographique (un prorata de population
  surcorrigerait fortement) ;
- une valeur **nulle** vaut présence : l'IGT donne zéro émission de branche
  énergie à Mayotte, aucune correction ne s'applique ;
- la part figée à 2019 est sans effet sur les secteurs conso calés sur les
  bilans (qui fixent la part de chaque DROM année par année) ; elle ne joue
  plus que sur le partage entre sous-secteurs de la branche GES, recalée sur
  l'IGT.

### Trajectoire dérivée : propriétés garanties par le code

Mécanisme (découpage neutre puis coefficient k par secteur, DROM et année) :
rapport, §4.5.2.

- Sans l'étape de calage, la dérivation reproduit exactement l'ancien calcul
  sur une zone DROM unique : branche GES inchangée (écart relatif max 6·10⁻¹⁶).
- `publier_snbc3.py` recalcule les parts à chaque publication. Un onglet de
  bilan manquant, ou une somme des DROM qui s'écarte du bilan du groupe de plus
  de 0,1 %, y lève une erreur. `parts_bilans()` lève une erreur si l'onglet de
  parts manque, si une zone ou un secteur en est absent, ou si les parts ne
  somment pas à 1 par secteur et par année.
- Σ conso par (DROM, secteur, année) = cible des bilans à 6·10⁻⁵ près. Seul
  résidu : effet Jensen du facteur démographique par EPCI à l'intérieur du DROM.
- k vaut 1 pour la branche GES et pour les déchets (absents des bilans).
- Un onglet `SNBC3 retraitée <DROM>` déclaré dans `SNBC3_SHEETS` prendrait le
  pas sur la dérivation sans autre changement, si des trajectoires GES par DROM
  devenaient disponibles.

Pourquoi ne pas avoir dupliqué l'onglet de retraitement par DROM : le bloc
conso de l'onglet d'interface dépend aussi des GES (ventilation industrie,
lignes 185-192 ; partage routier / autres transports, lignes 232-238). Il
aurait fallu quatre onglets `GES <DROM>`, qui n'existent pas dans les données
DGEC, soit environ 1 900 formules par onglet à maintenir à chaque livraison.

## Vérifications

| Contrôle | Où |
|---|---|
| 30 pivots disponibles dans chaque DROM produit | `tests/test_territorialize.py::TestPivotsDromDisponibles` |
| Calage sur les bilans, GES inchangé, chemin `epci_unique` (API) identique au calcul de zone | `tests/test_territorialize.py::TestTrajectoireZoneDrom` |
| Parts des bilans, zones DROM | `TestPartsBilans`, `TestZonesDrom` |
| Classeur publié sans donnée DGEC brute, à jour du classeur de travail | `tests/test_publier_snbc3.py` |
| Classeur vs pipeline (CINOR : écart relatif max 9,2·10⁻¹⁶), garde-fou multi-zone | `scripts/verif_zone_excel.py` |
| Substituabilité IGT / OREC, concentration aérien / maritime | `scripts/rapport_igt_vs_orec.py` |
| Anomalies des données DGEC | `scripts/controle_snbc3.py` (voir plus bas) |

Seize couples (EPCI, sous-secteur) sortent en `non_recale_secteur_nul` : ce
sont les EPCI sans branche énergie dans l'IGT, dégradation attendue.

## Retraitement SNBC3 DROM : limites techniques ouvertes

Le retraitement est manuel, dans le classeur de travail
`data/input/travail/SNBC3 retraitée - travail.xlsx`, non versionné (onglets
`SNBC3 retraitée Kyoto`, `... DROM`, `... HEX`, même structure) ; seules ses
trajectoires finales sont publiées (`publier_snbc3.py`). Il n'est pas
généré par script : ses résultats sont contrôlés par les lignes de
vérification internes (`controle_snbc3.py`) et par la convergence avec le
pipeline (`verif_zone_excel.py`).

Faute de données DROM, plusieurs ratios hexagonaux sont réutilisés. Deux
familles : le transfert d'une **part** (ratio de composition appliqué à un
total DROM réel), toujours borné par construction, et le transfert d'un
**facteur d'émission** (appliqué au GES DROM réel pour en déduire une conso),
qui peut produire un résultat impossible. La branche conso n'étant pas recalée
aux DROM, toute erreur y passe intégralement.

| Point | Hypothèse retenue | Risque | Piste |
|---|---|---|---|
| Routier / total transport, conso | Part GES routier / total transport DROM (IGT), figée à 2040 jusqu'en 2050 | Résiduel : conso routière post-2040 probablement un peu surestimée, mais bornée | Vérifier que 2040 reste antérieur au palier de décarbonation (2045-2049) |
| Déchets / « Autres industries », conso | Part hexagonale des déchets dans la conso « Autres industries » (déchets inclus), par année, appliquée à cette conso DROM (remplace un facteur d'émission appliqué au GES déchets, qui dépassait l'enveloppe) | Faible : borné par construction (déchets + « Autres industries » = enveloppe DROM de la filière) | Aucune donnée DROM sur la composition de la filière |
| Industrie, conso par filière | Facteur d'émission hexagonal par filière | Modéré | Contrôler qu'aucune filière ne dépasse l'enveloppe industrie DROM |
| Domicile-travail / VP (GES) ou / routier (conso) | Part hexagonale (46,5 % des VP en GES, 27,1 % du routier en conso), statique | Nul en GES (pivot DROM unique) ; non nul en conso (deux pivots) | Base « Mobilités professionnelles » du RP (couvre les DROM hors Mayotte), avec hypothèses de distance et de facteur par mode |
| Chauffage résidentiel / tertiaire, conso | Poste absent des bilans DGEC par usage, absorbé dans « Autres consommations » ; ratio MI/LC hexagonal | Faible : total sectoriel préservé | Aucune donnée DROM pour le ratio MI/LC |
| Dynamique intra-sectorielle | Évolution relative des sous-secteurs d'un secteur calée sur l'agrégat DROM | Second ordre | Climatisation disponible par DROM dans l'onglet `Bâtiments` (lignes 272-276 et 290-294) |

**Figeage 2040 du routier.** Le GES routier DROM chute de -98 % entre 2049 et
2050 (électrification ; même palier dans l'hexagone, déjà remonté par
`controle_snbc3.py`). Une part GES qui suit ce palier entraîne la conso
routière reconstruite vers zéro et rend le résidu « Autres transports »
négatif : d'où le figeage, sur le modèle du ratio domicile-travail /
routier hexagonal (onglet HEX, ligne 334), fixe lui aussi.

**Onglet HEX et ratio PCAET / SECTEN.** Les données SECTEN et les totaux
sectoriels de HEX sont obtenus par différence Kyoto - DROM, mais les lignes
converties au format PCAET (climatisation résidentielle, chauffage et
climatisation tertiaires) y sont recalculées avec le ratio de HEX. Ce ratio
n'est pas additif : aux DROM, la réaffectation de l'électricité fossile aux
usages le porte bien au-dessus de celui de Kyoto. Les « autres
consommations » (lignes 105 et 113) sont donc prises par différence au total
HEX, comme dans les onglets Kyoto et DROM, et non par Kyoto - DROM : sinon les
contrôles L106 et L114 s'écartent de zéro tant que l'électricité DROM n'est
pas décarbonée (corrigé le 2026-10-08).

**UTCATF.** Désactivé aux DROM, mais l'onglet HEX l'obtient par différence
Kyoto - DROM : la ventilation DROM pèse donc sur l'hexagone. Les sous-postes
(lignes 196-203) lisent le détail `GES DROM` depuis la livraison run 3 bis ;
l'ancienne ventilation du total par les parts de 2020, qui contournait un
détail erratique, inversait les signes dès que le total changeait de signe
(registre, `GES-DROM-UTCATF-DETAIL`).

**Facteurs d'émission de l'électricité.** Transposition de la méthode
hexagonale (facteurs Kyoto rééchelonnés sur les émissions de production
électrique de la zone) : conservation du total à 3,6·10⁻¹⁵ MtCO2e près. Les
écarts entre usages restent calibrés sur le mix hexagonal et
sous-différencient probablement la pointe thermique des ZNI ; erreur bornée,
décroissante avec la décarbonation.

## Trajectoires GES par DROM : éléments pour une reprise

Non mis en œuvre (rapport, §4.5.3). Si le sujet est repris :
- **Reconstituable depuis les bilans** : production d'électricité (conso
  fossile x facteurs usuels, charbon 0,346 et produits pétroliers
  0,270 kgCO2/kWh PCI ; Σ 5 DROM 4 646 kt en 2019, -1 % vs `GES DROM`),
  agriculture, déchets et UTCATF (en-têtes des onglets de bilan).
- **Manquant** : gaz fluorés par territoire (54 kt dans l'agrégat des bilans
  contre environ 350 kt de HFC dans `GES DROM`), procédés industriels, détail
  SECTEN. L'onglet `DROM` des hypothèses AMS ne donne que des émissions
  agrégées.
- **Prérequis** : trancher avec la DGEC la trajectoire agrégée de référence
  des déchets après 2023. Depuis la livraison run 3 bis (2026-10-08),
  `GES DROM` reprend les émissions des hypothèses AMS et l'agriculture
  concorde avec les bilans, mais les déchets restent quasi stables dans
  `GES DROM` quand ils baissent fortement dans les bilans (registre,
  `GES-DROM-DECHETS-BILANS`).
- **Approche** : clés de répartition plutôt que reconstitution complète,
  via `trajectoire_derivee` (`types_cales: [conso, GES]` et une seconde source
  de parts). Cibler d'abord la Martinique (électricité vers 2030),
  l'agriculture et les déchets.

## Registre des anomalies SNBC3

[config/anomalies_snbc3.yaml](../config/anomalies_snbc3.yaml) associe à chaque
anomalie des données DGEC un ou plusieurs contrôles exécutables, rejoués à
chaque livraison :

```bash
uv run python scripts/controle_snbc3.py
```

Trois surfaces contrôlées :

| Surface | Contrôle |
|---|---|
| Onglets bruts DGEC | ruptures de série, divergences entre sources, cohérence synthèse / détail d'un même onglet |
| Contrôles internes du classeur | lignes « Vérification / Delta total », qui doivent être nulles |
| Trajectoires retraitées | ruptures sur les séries livrées, par `code_traj_supra` |

Rupture non déclarée : rythme annualisé > 25 % **et** > 4 fois le rythme
médian de la série. Le critère relatif évite de signaler les paliers de
décarbonation légitimes.

L'export CSV (`data/output/controle_snbc3_<date>.csv`) porte une colonne
`section` (`registre`, `rupture_non_declaree`, `rupture_retraitee`,
`verification_interne`) et deux statuts à croiser :
- `statut_registre` : ce que le registre **déclare** (`ouverte`, `corrigee`, `n/a`) ;
- `etat` : ce que la mesure **constate** (`PRESENTE`, `CORRIGEE`, `N.D.`, `SIGNALE`).

`corrigee` + `PRESENTE` = régression ; `ouverte` + `CORRIGEE` = entrée à
passer en `corrigee`. La liste des anomalies et leur lecture sont dans le
rapport (§3.4.5 et §4.8) ; l'état à jour est celui du script.
