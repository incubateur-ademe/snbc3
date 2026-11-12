"""
tests/test_ept.py
==================
Tests unitaires du support EPT (établissements publics territoriaux de la
Métropole du Grand Paris) : couverture commune↔EPT, non-double-compte dans
les totaux nationaux (pipeline.aggregate.compute_totaux) et dans la
territorialisation (pipeline.territorialize.territorialize).

Lancer avec : uv run python -m pytest tests/test_ept.py -v
"""

import pandas as pd
import pytest

from pipeline.aggregate import (
    NIVEAU_GEO_COMMUNE, NIVEAU_GEO_EPCI, NIVEAU_GEO_EPT, NIVEAU_GEO_TOTAL,
    check_conservation_ept, check_couverture_ept, compute_totaux,
)


# ---------------------------------------------------------------------------
# check_couverture_ept
# ---------------------------------------------------------------------------

class TestCouvertureEpt:
    def _correspondance_epci(self, communes_mgp: list[str]) -> pd.DataFrame:
        rows = [{"codgeo": c, "code_epci": "200054781"} for c in communes_mgp]
        rows.append({"codgeo": "69123", "code_epci": "200046977"})  # une autre EPCI, hors MGP
        return pd.DataFrame(rows)

    def _correspondance_ept(self, mapping: dict[str, str]) -> pd.DataFrame:
        return pd.DataFrame(
            [{"codgeo": c, "code_ept": ept} for c, ept in mapping.items()]
        )

    def test_partition_complete_ne_leve_rien(self):
        """11 EPT + Paris couvrent exactement les communes MGP : pas d'erreur."""
        corr_epci = self._correspondance_epci(["75056", "93001", "93002"])
        corr_ept = self._correspondance_ept({"93001": "T8", "93002": "T8"})
        check_couverture_ept(corr_epci, corr_ept, code_epci_mgp="200054781", code_commune_paris="75056")

    def test_commune_manquante_leve(self):
        """Une commune MGP absente des EPT et différente de Paris : erreur."""
        corr_epci = self._correspondance_epci(["75056", "93001", "93002"])
        corr_ept = self._correspondance_ept({"93001": "T8"})  # 93002 oublié
        with pytest.raises(ValueError, match="absente"):
            check_couverture_ept(corr_epci, corr_ept, code_epci_mgp="200054781", code_commune_paris="75056")

    def test_commune_en_trop_leve(self):
        """Une commune EPT qui n'appartient pas à la MGP dans la table EPCI : erreur."""
        corr_epci = self._correspondance_epci(["75056", "93001"])
        corr_ept = self._correspondance_ept({"93001": "T8", "93999": "T8"})  # 93999 hors MGP
        with pytest.raises(ValueError, match="hors MGP"):
            check_couverture_ept(corr_epci, corr_ept, code_epci_mgp="200054781", code_commune_paris="75056")

    def test_doublon_leve(self):
        """Une commune présente deux fois dans la table EPT (deux EPT) : erreur."""
        corr_epci = self._correspondance_epci(["75056", "93001"])
        corr_ept = pd.DataFrame([
            {"codgeo": "93001", "code_ept": "T8"},
            {"codgeo": "93001", "code_ept": "T6"},  # doublon
        ])
        with pytest.raises(ValueError, match="double"):
            check_couverture_ept(corr_epci, corr_ept, code_epci_mgp="200054781", code_commune_paris="75056")


# ---------------------------------------------------------------------------
# compute_totaux : non-double-compte EPCI + EPT
# ---------------------------------------------------------------------------

class TestComputeTotauxNiveauGeo:
    def _df_long(self) -> pd.DataFrame:
        """
        2 vraies EPCI (MGP à 100, une autre EPCI à 50) + 2 lignes EPT
        (subdivisions de la MGP, 60 et 40 - qui somment à 100 = la MGP, mais
        NE DOIVENT PAS s'ajouter une deuxième fois au total).
        """
        rows = [
            {"code_epci": "200054781", "code_indicateur": "POP", "annee": 2021,
             "valeur": 100.0, "niveau_geo": NIVEAU_GEO_EPCI, "source": "TEST",
             "date_extraction": "2026-01-01", "qualite": "ok"},
            {"code_epci": "200046977", "code_indicateur": "POP", "annee": 2021,
             "valeur": 50.0, "niveau_geo": NIVEAU_GEO_EPCI, "source": "TEST",
             "date_extraction": "2026-01-01", "qualite": "ok"},
            {"code_epci": "200057867", "code_indicateur": "POP", "annee": 2021,
             "valeur": 60.0, "niveau_geo": NIVEAU_GEO_EPT, "source": "TEST",
             "date_extraction": "2026-01-01", "qualite": "ok"},
            {"code_epci": "200057875", "code_indicateur": "POP", "annee": 2021,
             "valeur": 40.0, "niveau_geo": NIVEAU_GEO_EPT, "source": "TEST",
             "date_extraction": "2026-01-01", "qualite": "ok"},
        ]
        return pd.DataFrame(rows)

    def test_total_hexagone_ignore_les_ept(self):
        """Sans le filtre niveau_geo, le total HEXAGONE vaudrait 250 (double compte)."""
        df = self._df_long()
        zones = {"200054781": "HEXAGONE", "200046977": "HEXAGONE"}
        totaux = compute_totaux(df, zones, pop_ref=None)
        hexagone = totaux[(totaux["code_epci"] == "HEXAGONE") & (totaux["code_indicateur"] == "POP")]
        assert len(hexagone) == 1
        assert hexagone["valeur"].iloc[0] == pytest.approx(150.0)  # 100 + 50, pas +100 des EPT

    def test_total_porte_niveau_geo_total(self):
        df = self._df_long()
        totaux = compute_totaux(
            df, {"200054781": "HEXAGONE", "200046977": "HEXAGONE"}, pop_ref=None
        )
        assert (totaux["niveau_geo"] == NIVEAU_GEO_TOTAL).all()

    def test_sans_colonne_niveau_geo_le_double_compte_reapparait(self):
        """
        Sans niveau_geo, seule la table des zones protège des totaux : si elle
        contient par erreur des codes EPT (scénario réaliste si la liste de
        référence n'est pas filtrée en amont), le double compte réapparaît.
        C'est exactement ce que le filtre niveau_geo neutralise.
        """
        df = self._df_long().drop(columns=["niveau_geo"])
        zones_errone = dict.fromkeys(
            ["200054781", "200046977", "200057867", "200057875"], "HEXAGONE"
        )
        totaux = compute_totaux(df, zones_errone, pop_ref=None)
        hexagone = totaux[(totaux["code_epci"] == "HEXAGONE") & (totaux["code_indicateur"] == "POP")]
        assert hexagone["valeur"].iloc[0] == pytest.approx(250.0)  # 100+50+60+40 : double compte

    def test_avec_niveau_geo_meme_table_des_zones_erronee_est_protege(self):
        """Le filtre niveau_geo protège même si la table des zones inclut par erreur des EPT."""
        df = self._df_long()
        zones_errone = dict.fromkeys(
            ["200054781", "200046977", "200057867", "200057875"], "HEXAGONE"
        )
        totaux = compute_totaux(df, zones_errone, pop_ref=None)
        hexagone = totaux[(totaux["code_epci"] == "HEXAGONE") & (totaux["code_indicateur"] == "POP")]
        assert hexagone["valeur"].iloc[0] == pytest.approx(150.0)


# ---------------------------------------------------------------------------
# check_conservation_ept
# ---------------------------------------------------------------------------

class TestConservationEpt:
    """
    Avec Paris comme sa propre ligne (niveau_geo COMMUNE), l'invariant est une
    égalité stricte Σ(11 EPT) + Paris == MGP - pas seulement une inégalité -
    mais uniquement quand les 11 EPT ET Paris ont une valeur (couverture
    complète). Une valeur manquante (secret statistique, absence réelle comme
    MOBILITE_DT pour Paris) n'est pas une fuite : elle est exclue du test.
    """

    def _ligne_epci(self, valeur):
        return {"code_epci": "200054781", "code_indicateur": "SAU", "annee": 2020,
                "valeur": valeur, "niveau_geo": NIVEAU_GEO_EPCI}

    def _lignes_ept(self, valeurs: list[float]):
        codes = ["200057867", "200057875", "200057941", "200057966", "200057974",
                 "200057982", "200057990", "200058006", "200058014", "200058097",
                 "200058790"]
        assert len(valeurs) == len(codes)
        return [
            {"code_epci": c, "code_indicateur": "SAU", "annee": 2020,
             "valeur": v, "niveau_geo": NIVEAU_GEO_EPT}
            for c, v in zip(codes, valeurs) if v is not None
        ]

    def _ligne_paris(self, valeur):
        return {"code_epci": "75056", "code_indicateur": "SAU", "annee": 2020,
                "valeur": valeur, "niveau_geo": NIVEAU_GEO_COMMUNE}

    def _df_all(self, valeurs_ept: list[float], valeur_paris, valeur_mgp: float) -> pd.DataFrame:
        rows = [self._ligne_epci(valeur_mgp), *self._lignes_ept(valeurs_ept)]
        if valeur_paris is not None:
            rows.append(self._ligne_paris(valeur_paris))
        return pd.DataFrame(rows)

    def _valeurs_11(self, total, dernier):
        """10 EPT à 0, le dernier porte tout - pratique pour construire des cas."""
        return [0.0] * 10 + [dernier]

    def test_conservation_exacte_ok(self):
        """Σ(EPT) + Paris == MGP à couverture complète : rien à signaler."""
        df = self._df_all(self._valeurs_11(80, 80.0), valeur_paris=20.0, valeur_mgp=100.0)
        assert check_conservation_ept(df).empty

    def test_exces_detecte(self):
        """Σ(EPT) + Paris > MGP : fuite/doublon détecté (dépassement positif)."""
        df = self._df_all(self._valeurs_11(80, 90.0), valeur_paris=20.0, valeur_mgp=100.0)
        depassements = check_conservation_ept(df)
        assert len(depassements) == 1

    def test_deficit_detecte(self):
        """Σ(EPT) + Paris < MGP à couverture complète : perte détectée (écart négatif)."""
        df = self._df_all(self._valeurs_11(80, 50.0), valeur_paris=20.0, valeur_mgp=100.0)
        depassements = check_conservation_ept(df)
        assert len(depassements) == 1

    def test_indicateur_negatif_conservation_exacte(self):
        """
        Cas réel observé (ALDO_UTCATF_PRBO) : MGP plus négatif que Σ(EPT) car
        la contribution de Paris (hors EPT) est elle-même négative. Avec Paris
        inclus, l'égalité stricte tient malgré le signe.
        """
        df = self._df_all(self._valeurs_11(0, -101.165315), valeur_paris=-46.658803, valeur_mgp=-147.824119)
        assert check_conservation_ept(df).empty

    def test_paris_absent_est_couverture_incomplete_pas_une_fuite(self):
        """
        Paris peut être réellement absent de certaines sources (ex. observé :
        MOBILITE_DT n'a aucune ligne Paris). Ce n'est pas un écart de
        conservation, juste une comparaison non faite.
        """
        df = self._df_all(self._valeurs_11(80, 80.0), valeur_paris=None, valeur_mgp=100.0)
        assert check_conservation_ept(df).empty

    def test_un_ept_manquant_est_couverture_incomplete_pas_une_fuite(self):
        """Un EPT sans valeur (secret statistique) : comparaison non faite, pas de fausse alerte."""
        valeurs = self._valeurs_11(80, 80.0)
        rows = [self._ligne_epci(100.0), self._ligne_paris(20.0)]
        rows += self._lignes_ept(valeurs)[:-1]  # un seul EPT manquant sur 11
        df = pd.DataFrame(rows)
        assert check_conservation_ept(df).empty

    def test_sans_lignes_ept_ne_leve_rien(self):
        df = pd.DataFrame([self._ligne_epci(100.0)])
        assert check_conservation_ept(df).empty

    def test_indicateur_non_additif_ignore(self):
        """
        DJU (moyenne climatique) et POP_PROJ_RATIO (ratio) ne sont pas des
        stocks : Σ(EPT) n'a aucune raison d'égaler la valeur MGP pour ces
        indicateurs (methode != somme_communes/calcul_derive dans le
        catalogue). Un déséquilibre volontaire dans la fixture ne doit
        déclencher aucune alerte.
        """
        rows = [
            {"code_epci": "200054781", "code_indicateur": "DJU_CHAUD_COSTIC", "annee": 2021,
             "valeur": 1940.24, "niveau_geo": NIVEAU_GEO_EPCI},
            {"code_epci": "75056", "code_indicateur": "DJU_CHAUD_COSTIC", "annee": 2021,
             "valeur": 1877.73, "niveau_geo": NIVEAU_GEO_COMMUNE},
        ] + [
            {"code_epci": c, "code_indicateur": "DJU_CHAUD_COSTIC", "annee": 2021,
             "valeur": 1900.0, "niveau_geo": NIVEAU_GEO_EPT}
            for c in ["200057867", "200057875", "200057941", "200057966", "200057974",
                      "200057982", "200057990", "200058006", "200058014", "200058097", "200058790"]
        ]
        df = pd.DataFrame(rows)
        # Σ(EPT) = 11*1900 = 20900, très loin de MGP (1940.24) : une fausse
        # alerte apparaîtrait si l'indicateur n'était pas exclu.
        assert check_conservation_ept(df).empty


# ---------------------------------------------------------------------------
# territorialize() : EPT/Paris calculés comme un EPCI, exclus seulement de
# la conservation nationale
# ---------------------------------------------------------------------------

class TestTerritorializeIncludesEptSaufConservation:
    def test_epci_de_zone_voit_les_ept(self):
        """
        epci_de_zone() ne connaît pas niveau_geo (fonction bas niveau) : les
        EPT/Paris sont bien comptés comme des territoires de la zone. C'est
        désormais voulu - territorialize() les calcule, il ne les neutralise
        plus en amont (seule la conservation les exclut, cf. test suivant).
        """
        from pipeline.territorialize import epci_de_zone

        df = pd.DataFrame([
            {"code_epci": "200054781", "zone": "HEXAGONE", "code_departement": "75",
             "niveau_geo": NIVEAU_GEO_EPCI},
            {"code_epci": "200057867", "zone": "HEXAGONE", "code_departement": "93",
             "niveau_geo": NIVEAU_GEO_EPT},
        ])
        assert epci_de_zone(df, "HEXAGONE") == {"200054781", "200057867"}

    def test_territorialize_calcule_ept_mais_exclut_conservation(self, monkeypatch):
        """
        Bout en bout minimal (pivots/national/config mockés, reste du calcul
        réel) : un EPT reçoit une ligne territorialisée comme n'importe quel
        EPCI, mais sa valeur_brute n'entre pas dans le Σ de conservation
        nationale. Sans le filtre, 60 (MGP) + 40 (autre EPCI) + 40 (EPT,
        imbriqué dans la MGP) = 140 contre un national de 100 - 40 % d'écart
        qui masquerait un vrai problème. Avec le filtre : 60+40=100 - 0 %.
        """
        import pipeline.territorialize as terr

        corr = pd.DataFrame([{
            "code_traj_supra": "SR1", "type": "GES", "secteur": "Test", "usage": "",
            "code_pivot": "PIVTEST", "unite": "MtCO2e", "code_recalage": "RECALTEST",
            "correction_dju_chaud": False, "correction_dju_froid": False,
            "influence_demographie": False,
        }])
        national = pd.DataFrame([{"code_traj_supra": "SR1", "annee": 2021, "valeur_nationale": 100.0}])

        monkeypatch.setattr(terr, "load_correspondance", lambda zone: corr)
        monkeypatch.setattr(terr, "load_national_traj", lambda codes, zone, path=None: national)
        monkeypatch.setattr(terr, "load_zone_config", lambda zone: {})

        df_long = pd.DataFrame([
            {"code_epci": "200054781", "nom_epci": "MGP", "zone": "HEXAGONE",
             "code_region": "11", "code_departement": "75",
             "niveau_geo": NIVEAU_GEO_EPCI, "code_indicateur": "PIVTEST", "annee": 2021, "valeur": 60.0},
            {"code_epci": "200046977", "nom_epci": "Autre EPCI", "zone": "HEXAGONE",
             "code_region": "84", "code_departement": "74",
             "niveau_geo": NIVEAU_GEO_EPCI, "code_indicateur": "PIVTEST", "annee": 2021, "valeur": 40.0},
            {"code_epci": "200057867", "nom_epci": "Plaine Commune", "zone": "HEXAGONE",
             "code_region": "11", "code_departement": "93",
             "niveau_geo": NIVEAU_GEO_EPT, "code_indicateur": "PIVTEST", "annee": 2021, "valeur": 40.0},
            {"code_epci": "HEXAGONE", "nom_epci": "France hexagonale", "zone": None,
             "code_region": "", "code_departement": "",
             "niveau_geo": NIVEAU_GEO_TOTAL, "code_indicateur": "PIVTEST", "annee": 2021, "valeur": 100.0},
            # Un seul EPCI (pas l'EPT) porte une valeur observée RECALTEST :
            # suffit à tester que l'EPT dégrade proprement (pas d'observé
            # pour lui), sans déclencher un cas particulier pandas sur une
            # colonne "obs" entièrement vide (aucun rapport avec l'EPT).
            {"code_epci": "200046977", "nom_epci": "Autre EPCI", "zone": "HEXAGONE",
             "code_region": "84", "code_departement": "74",
             "niveau_geo": NIVEAU_GEO_EPCI, "code_indicateur": "RECALTEST", "annee": 2021, "valeur": 999.0},
        ])

        df = terr.territorialize(df_long, zone="HEXAGONE", ref_year=2021)

        ept_row = df[df["code_epci"] == "200057867"]
        assert len(ept_row) == 1
        assert ept_row["valeur_brute"].iloc[0] == pytest.approx(40.0)
        assert ept_row["qualite"].iloc[0] == "non_recale_absent"  # pas d'observé RECALTEST pour l'EPT

        ecarts = df["ecart_conservation"].dropna().unique()
        assert len(ecarts) == 1
        assert ecarts[0] == pytest.approx(0.0, abs=1e-9)
