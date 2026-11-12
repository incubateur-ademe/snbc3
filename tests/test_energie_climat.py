"""
Source OREC (sources/energie_climat.py) : sélection de la valeur d'un EPCI pour
l'année de référence, en particulier pour les EPCI à cheval sur deux régions
dont les deux OREC publient, selon l'année, le total ou la seule part d'une
région.

Sans données réelles ni réseau : les CSV sont remplacés par de petites tables.
"""

import numpy as np
import pandas as pd
import pytest

from sources import energie_climat as ec

ID_TOTAL = "0101.0100"  # EC_EMIS_TOTAL


def _raw(*lignes) -> pd.DataFrame:
    """Lignes (code_epci, annee, valeur, code_region, completude, confidentiel=False)."""
    rows = []
    for code_epci, annee, valeur, code_region, completude, *conf in lignes:
        rows.append({
            "code_epci": code_epci, "annee": annee, "id_orec": ID_TOTAL, "valeur": valeur,
            "confidentiel": bool(conf[0]) if conf else False,
            "code_region": code_region, "completude": completude,
        })
    return pd.DataFrame(rows)


# EPCI à cheval : totaux 2020 et 2022, part d'une seule région en 2021 (cas réel
# des EPCI à cheval en année de référence).
CHEVAL = [
    ("B", 2020, 100.0, "27-84", True),
    ("B", 2021, 5.0, "84", False),
    ("B", 2022, 120.0, "27-84", True),
]


class TestValeurChevalSurDeuxRegions:
    def test_la_part_d_une_region_n_est_jamais_retenue(self):
        valeur, methode = ec._valeur_cheval(_raw(*CHEVAL), 2021)
        assert valeur == pytest.approx(110.0)
        assert methode == ec.QUALITE_CHEVAL_INTERPOLE

    def test_total_publie_pour_l_annee(self):
        valeur, methode = ec._valeur_cheval(_raw(*CHEVAL), 2022)
        assert (valeur, methode) == (120.0, "exact")

    def test_total_masque_pour_l_annee(self):
        grp = _raw(("B", 2021, np.nan, "27-84", True, True), ("B", 2021, 5.0, "84", False))
        valeur, methode = ec._valeur_cheval(ec._dedupliquer(grp), 2021)
        assert np.isnan(valeur) and methode == "secret_stat"

    def test_annee_hors_bornes_reprend_le_total_le_plus_proche(self):
        grp = _raw(("B", 2019, 90.0, "27-84", True), ("B", 2020, 3.0, "84", False))
        assert ec._valeur_cheval(grp, 2021) == (90.0, ec.QUALITE_CHEVAL_EXTRAPOLE)
        grp = _raw(("B", 2023, 130.0, "27-84", True), ("B", 2020, 3.0, "84", False))
        assert ec._valeur_cheval(grp, 2021) == (130.0, ec.QUALITE_CHEVAL_EXTRAPOLE)

    def test_sans_aucun_total_la_valeur_est_gardee_mais_non_verifiable(self):
        grp = _raw(("M", 2020, 180.0, "52", False), ("M", 2021, 185.0, "52", False))
        assert ec._valeur_cheval(grp, 2021) == (185.0, ec.QUALITE_CHEVAL_NON_VERIFIABLE)

    def test_sans_aucun_total_et_donnee_masquee_reste_secret_stat(self):
        grp = _raw(("M", 2021, np.nan, "52", False, True))
        valeur, methode = ec._valeur_cheval(grp, 2021)
        assert np.isnan(valeur) and methode == "secret_stat"


class TestValeurStandard:
    """EPCI à une seule région : comportement inchangé."""

    def test_valeur_exacte(self):
        grp = _raw(("A", 2020, 1.0, "84", False), ("A", 2021, 2.0, "84", False))
        assert ec._valeur_standard(grp, 2021) == (2.0, "exact")

    def test_repli_sur_l_annee_suivante_puis_precedente(self):
        grp = _raw(("A", 2019, 1.0, "84", False), ("A", 2023, 4.0, "84", False))
        assert ec._valeur_standard(grp, 2021) == (4.0, "extrapolé")
        assert ec._valeur_standard(grp[grp["annee"] == 2019], 2021) == (1.0, "extrapolé")

    def test_masque_et_absent(self):
        valeur, methode = ec._valeur_standard(_raw(("A", 2021, np.nan, "84", False, True)), 2021)
        assert np.isnan(valeur) and methode == "secret_stat"
        valeur, methode = ec._valeur_standard(_raw(("A", 2021, 1.0, "84", False)).iloc[0:0], 2021)
        assert np.isnan(valeur) and methode == "absent"


class TestDedupliquer:
    def test_la_valeur_l_emporte_sur_la_ligne_masquee_quel_que_soit_l_ordre(self):
        valeur = ("A", 2021, 7.0, "93", False)
        masquee = ("A", 2021, np.nan, "93", False, True)
        for lignes in ((valeur, masquee), (masquee, valeur)):
            out = ec._dedupliquer(_raw(*lignes))
            assert len(out) == 1 and out.iloc[0]["valeur"] == 7.0

    def test_la_ligne_complete_l_emporte_sur_la_part_d_une_region(self):
        for lignes in ((("B", 2021, 5.0, "84", False), ("B", 2021, 110.0, "27-84", True)),
                       (("B", 2021, 110.0, "27-84", True), ("B", 2021, 5.0, "84", False))):
            out = ec._dedupliquer(_raw(*lignes))
            assert len(out) == 1 and out.iloc[0]["valeur"] == 110.0

    def test_cles_distinctes_conservees(self):
        out = ec._dedupliquer(_raw(("A", 2020, 1.0, "84", False), ("A", 2021, 2.0, "84", False)))
        assert len(out) == 2


class TestEpciACheval:
    @staticmethod
    def _referentiel(monkeypatch, regions: dict[str, list[str]]):
        import sources.geo_epci as geo

        ref = pd.DataFrame({"code_epci": list(regions), "codes_regions": list(regions.values())})
        monkeypatch.setattr(geo, "load_epci_centroids", lambda *a, **k: ref)
        monkeypatch.setattr(geo, "load_fusions_epci", lambda *a, **k: {})

    def test_detecte_par_les_csv_et_par_le_referentiel(self, monkeypatch):
        # B : ligne complète ; C : deux codes région ; M : visible seulement du référentiel.
        raw = _raw(
            ("A", 2021, 1.0, "84", False),
            ("B", 2021, 110.0, "27-84", True),
            ("C", 2020, 1.0, "52", False), ("C", 2021, 1.0, "53", False),
            ("M", 2021, 1.0, "52", False),
        )
        self._referentiel(monkeypatch, {"A": ["84"], "B": ["27", "84"], "M": ["28", "52"]})
        assert ec._epci_a_cheval(raw) == {"B", "C", "M"}

    def test_referentiel_indisponible_n_empeche_pas_le_calcul(self, monkeypatch):
        import sources.geo_epci as geo

        def _panne(*a, **k):
            raise RuntimeError("réseau")

        monkeypatch.setattr(geo, "load_epci_centroids", _panne)
        raw = _raw(("A", 2021, 1.0, "84", False), ("B", 2021, 110.0, "27-84", True))
        assert ec._epci_a_cheval(raw) == {"B"}


class TestRecomposerFusions:
    """EPCI issu d'une fusion, publié par l'OREC sous les codes des EPCI qu'il réunit."""

    COLS = ["EC_EMIS_TOTAL", "EC_CONSO_TOTAL"]

    def _tables(self, lignes: dict[str, tuple]):
        """{code: ((valeur, qualite) par colonne de COLS)}."""
        valeurs = pd.DataFrame({c: [l[i][0] for l in lignes.values()] for i, c in enumerate(self.COLS)},
                               index=list(lignes), dtype=float)
        qualites = pd.DataFrame({c: [l[i][1] for l in lignes.values()] for i, c in enumerate(self.COLS)},
                                index=list(lignes), dtype=object)
        return valeurs, qualites

    def test_somme_des_epci_reunis_et_anciens_codes_retires(self):
        v, q = self._tables({
            "X1": ((10.0, "ok"), (1.0, "ok")),
            "X2": ((5.0, "ok"), (2.0, "extrapolé")),
            "A":  ((7.0, "ok"), (3.0, "ok")),
        })
        v, q = ec._recomposer_fusions(v, q, {"N": ["X1", "X2"]}, 2021)
        assert sorted(v.index) == ["A", "N"]
        assert v.loc["N", "EC_EMIS_TOTAL"] == 15.0
        assert q.loc["N", "EC_EMIS_TOTAL"] == ec.QUALITE_FUSION_EPCI
        # Une composante dégradée reste visible dans la qualité de la somme.
        assert v.loc["N", "EC_CONSO_TOTAL"] == 3.0
        assert q.loc["N", "EC_CONSO_TOTAL"] == "extrapolé"
        assert v.loc["A", "EC_EMIS_TOTAL"] == 7.0 and q.loc["A", "EC_EMIS_TOTAL"] == "ok"

    def test_absorption_le_code_conserve_recoit_la_somme(self):
        v, q = self._tables({"A": ((7.0, "ok"), (3.0, "ok")), "X": ((1.0, "ok"), (1.0, "ok"))})
        v, q = ec._recomposer_fusions(v, q, {"A": ["A", "X"]}, 2021)
        assert list(v.index) == ["A"] and v.loc["A", "EC_EMIS_TOTAL"] == 8.0

    def test_composante_masquee_valeur_absente_et_secret_propage(self):
        v, q = self._tables({"X1": ((10.0, "ok"), (1.0, "ok")), "X2": ((np.nan, "secret_stat"), (2.0, "ok"))})
        v, q = ec._recomposer_fusions(v, q, {"N": ["X1", "X2"]}, 2021)
        assert np.isnan(v.loc["N", "EC_EMIS_TOTAL"]) and q.loc["N", "EC_EMIS_TOTAL"] == "secret_stat"
        assert v.loc["N", "EC_CONSO_TOTAL"] == 3.0

    def test_epci_reuni_absent_du_bouquet_pas_de_recomposition(self):
        v, q = self._tables({"X1": ((10.0, "ok"), (1.0, "ok"))})
        v, q = ec._recomposer_fusions(v, q, {"N": ["X1", "X2"]}, 2021)
        assert list(v.index) == ["X1"]  # écarté plus loin, hors liste de référence

    def test_bouquet_deja_au_nouveau_perimetre(self):
        v, q = self._tables({"N": ((20.0, "ok"), (4.0, "ok")), "X1": ((10.0, "ok"), (1.0, "ok"))})
        v, q = ec._recomposer_fusions(v, q, {"N": ["X1", "X2"]}, 2021)
        assert list(v.index) == ["N"] and v.loc["N", "EC_EMIS_TOTAL"] == 20.0

    def test_extract_indicators_applique_les_fusions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ec, "INTERIM_DIR", tmp_path)
        monkeypatch.setattr(ec, "_RAW_CACHE_PATH", tmp_path / "raw.parquet")
        TestEpciACheval._referentiel(monkeypatch, {"N": ["76"]})
        raw = _raw(("X1", 2021, 10.0, "76", False), ("X2", 2021, 5.0, "76", False))
        monkeypatch.setattr(ec, "_load_raw", lambda: raw)
        valeurs, qualites = ec.extract_indicators(2021, fusions={"N": ["X1", "X2"]})
        assert "N" in valeurs.index and not {"X1", "X2"} & set(valeurs.index)
        assert valeurs.loc["N", "EC_EMIS_TOTAL"] == 15.0
        assert qualites.loc["N", "EC_EMIS_TOTAL"] == ec.QUALITE_FUSION_EPCI


class TestExtractIndicators:
    @pytest.fixture
    def interim(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ec, "INTERIM_DIR", tmp_path)
        monkeypatch.setattr(ec, "_RAW_CACHE_PATH", tmp_path / "raw.parquet")
        TestEpciACheval._referentiel(monkeypatch, {"A": ["84"], "B": ["27", "84"]})
        return tmp_path

    def test_epci_a_cheval_reconstitue_et_epci_ordinaire_inchange(self, interim, monkeypatch):
        raw = _raw(*CHEVAL, ("A", 2021, 50.0, "84", False))
        monkeypatch.setattr(ec, "_load_raw", lambda: raw)
        valeurs, qualites = ec.extract_indicators(2021)
        assert valeurs.loc["B", "EC_EMIS_TOTAL"] == pytest.approx(110.0)
        assert qualites.loc["B", "EC_EMIS_TOTAL"] == ec.QUALITE_CHEVAL_INTERPOLE
        assert valeurs.loc["A", "EC_EMIS_TOTAL"] == 50.0
        assert qualites.loc["A", "EC_EMIS_TOTAL"] == "ok"

    def test_zero_residentiel_ou_routier_traite_comme_non_publie(self, interim, monkeypatch):
        # Cas de l'OREC Corse : 0 publié à la place d'une valeur vide.
        raw = _raw(("A", 2020, 125.0, "94", False), ("A", 2021, 0.0, "94", False),
                   ("A", 2022, 0.0, "94", False)).assign(id_orec="0101.0101")
        tertiaire = _raw(("A", 2021, 0.0, "94", False)).assign(id_orec="0101.0102")
        monkeypatch.setattr(ec, "_load_raw", lambda: pd.concat([raw, tertiaire], ignore_index=True))
        valeurs, qualites = ec.extract_indicators(2021)
        assert valeurs.loc["A", "EC_EMIS_RESIDENTIEL"] == 125.0
        assert qualites.loc["A", "EC_EMIS_RESIDENTIEL"] == "extrapolé"
        # Un zéro hors résidentiel / routier reste une valeur publiée.
        assert valeurs.loc["A", "EC_EMIS_TERTIAIRE"] == 0.0

    def test_total_sans_le_secteur_a_zero_remplace_par_la_somme_des_secteurs(self, interim, monkeypatch):
        # Cas de l'OREC Corse : le total publié est la somme des secteurs publiés,
        # résidentiel à 0 compris. C n'a pas de 0 l'année demandée : total inchangé.
        lignes = [
            ("A", 2020, 125.0, "0101.0101"), ("A", 2021, 0.0, "0101.0101"),
            ("A", 2021, 30.0, "0101.0102"), ("A", 2021, 30.0, "0101.0100"),
            ("A", 2021, 400.0, "0201.0100"),
            ("C", 2022, 0.0, "0101.0101"), ("C", 2021, 10.0, "0101.0101"),
            ("C", 2021, 18.0, "0101.0100"), ("C", 2021, 5.0, "0101.0102"),
        ]
        raw = pd.concat([_raw((e, a, v, "94", False)).assign(id_orec=i) for e, a, v, i in lignes],
                        ignore_index=True)
        monkeypatch.setattr(ec, "_load_raw", lambda: raw)
        valeurs, qualites = ec.extract_indicators(2021)
        assert valeurs.loc["A", "EC_EMIS_TOTAL"] == 155.0
        assert qualites.loc["A", "EC_EMIS_TOTAL"] == ec.QUALITE_SOMME_SECTEURS
        # Aucun 0 de consommation : le total publié est gardé.
        assert valeurs.loc["A", "EC_CONSO_TOTAL"] == 400.0
        assert qualites.loc["A", "EC_CONSO_TOTAL"] == "ok"
        assert (valeurs.loc["C", "EC_EMIS_TOTAL"], qualites.loc["C", "EC_EMIS_TOTAL"]) == (18.0, "ok")

    def test_le_cache_est_versionne(self, interim, monkeypatch):
        monkeypatch.setattr(ec, "_load_raw", lambda: _raw(*CHEVAL))
        ec.extract_indicators(2021)
        assert (interim / f"energie_climat_2021_v{ec._CACHE_VERSION}.parquet").exists()
        # Un cache d'avant la règle (nom sans version) n'est jamais relu.
        ancien = pd.DataFrame({"EC_EMIS_TOTAL": [1.0]}, index=pd.Index(["B"], name="code_epci"))
        ancien.to_parquet(interim / "energie_climat_2022.parquet")
        ancien.to_parquet(interim / "energie_climat_2022_qualite.parquet")
        monkeypatch.setattr(ec, "_load_raw", lambda: _raw(*CHEVAL))
        valeurs, _ = ec.extract_indicators(2022)
        assert valeurs.loc["B", "EC_EMIS_TOTAL"] == 120.0


class TestLoadRaw:
    CSV = pd.DataFrame({
        "code_region": ["84", "27-84", "84"],
        "code_epci": ["200070308", "200070308", "200000628"],
        "annee_ref": ["2021", "2022", "2021"],
        "id_indicateur": [ID_TOTAL] * 3,
        "valeur": ["2,5", "523,9", ""],
        "confidentiel": ["0", "0", "1"],
        "completude": ["0", "1", "0"],
    })

    def test_code_region_et_completude_conserves_et_cache_ancien_ignore(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ec, "INTERIM_DIR", tmp_path)
        cache = tmp_path / "raw.parquet"
        monkeypatch.setattr(ec, "_RAW_CACHE_PATH", cache)
        # Cache frais mais d'avant la règle : sans code_region ni completude.
        pd.DataFrame({"code_epci": ["x"], "annee": [2021], "id_orec": [ID_TOTAL],
                      "valeur": [1.0], "confidentiel": [False]}).to_parquet(cache)
        monkeypatch.setattr(ec, "_download_csv", lambda url: self.CSV.copy())

        raw = ec._load_raw()
        assert set(ec._RAW_COLONNES) <= set(raw.columns)
        par_annee = raw.set_index("annee")
        assert par_annee.loc[2022, "completude"].all()
        assert par_annee.loc[2022, "code_region"].iloc[0] == "27-84"
        assert not par_annee.loc[2021, "completude"].any()
        # La ligne masquée (valeur vide, confidentiel=1) est conservée.
        assert raw["confidentiel"].sum() == 2  # une par fichier téléchargé (conso + émissions)


class TestSubstitution:
    def _sub(self, tmp_path, lignes):
        f = tmp_path / "sub.csv"
        ec_raw = _raw(*lignes)
        ec_raw["annee"] = ec_raw["annee"].astype("Int64")
        ec_raw.to_csv(f, index=False)
        return [{"region": "44", "prefixe": "0101.", "fichier": f, "motif": "test"}]

    def test_remplace_region_simple_et_composee_seulement(self, tmp_path):
        raw = _raw(("A", 2021, 1.0, "44", False), ("C", 2021, 2.0, "27-44", True),
                   ("D", 2021, 3.0, "27", False), ("E", 2021, 4.0, "84", False))
        raw["annee"] = raw["annee"].astype("Int64")
        conso = raw.iloc[[0]].assign(id_orec="0201.0100", valeur=9.0)
        subs = self._sub(tmp_path, [("A", 2021, 10.0, "44", False), ("C", 2021, 20.0, "27-44", True)])
        res = ec._substituer(pd.concat([raw, conso], ignore_index=True), 2021, subs)
        val = res.set_index(["code_epci", "id_orec"])["valeur"]
        assert val[("A", ID_TOTAL)] == 10.0 and val[("C", ID_TOTAL)] == 20.0
        assert val[("D", ID_TOTAL)] == 3.0 and val[("E", ID_TOTAL)] == 4.0
        assert val[("A", "0201.0100")] == 9.0  # consommation non concernée
        assert len(res) == 5
        assert res["confidentiel"].dtype == bool and res["completude"].dtype == bool

    def test_fichier_hors_perimetre_refuse(self, tmp_path):
        subs = self._sub(tmp_path, [("E", 2021, 1.0, "84", False)])
        with pytest.raises(ValueError, match="hors région 44"):
            ec._substituer(_raw(("A", 2021, 1.0, "44", False)), 2021, subs)

    def test_fichier_versionne_conforme(self):
        for sub in ec._SUBSTITUTIONS:
            df = ec._lire_substitution(sub["fichier"])
            assert list(df.columns) == ec._RAW_COLONNES and len(df) > 0
            ec._substituer(df.iloc[:0], 2021, [sub])  # aucune ligne hors périmètre


class TestExpirationCache:
    @pytest.fixture
    def caches(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ec, "INTERIM_DIR", tmp_path)
        monkeypatch.setattr(ec, "_RAW_CACHE_PATH", tmp_path / "raw.parquet")
        monkeypatch.setattr(ec, "_RAW_CACHE_PRECEDENT_PATH", tmp_path / "raw_prec.parquet")
        TestEpciACheval._referentiel(monkeypatch, {"B": ["27", "84"]})
        return tmp_path

    @staticmethod
    def _vieillir(path, jours):
        import os, time
        t = time.time() - jours * 86400
        os.utime(path, (t, t))

    def _brut(self, valeur=100.0):
        df = _raw(("B", 2020, valeur, "27-84", True), ("B", 2021, 5.0, "84", False),
                  ("B", 2022, 120.0, "27-84", True))
        df["annee"] = df["annee"].astype("Int64")
        return df

    def test_cache_annuel_reutilise_puis_recalcule_si_brut_plus_recent_ou_expire(self, caches, monkeypatch):
        brut = self._brut()
        brut.to_parquet(ec._RAW_CACHE_PATH)
        self._vieillir(ec._RAW_CACHE_PATH, 2)
        appels = []
        monkeypatch.setattr(ec, "_load_raw", lambda: appels.append(1) or pd.read_parquet(ec._RAW_CACHE_PATH))
        ec.extract_indicators(2021)
        ec.extract_indicators(2021)
        assert len(appels) == 1  # cache annuel réutilisé
        # Nouveau cache brut (retéléchargé) : le cache annuel est recalculé.
        self._brut(valeur=200.0).to_parquet(ec._RAW_CACHE_PATH)
        valeurs, _ = ec.extract_indicators(2021)
        assert len(appels) == 2 and valeurs.loc["B", "EC_EMIS_TOTAL"] == pytest.approx(160.0)
        # Cache brut expiré : cache annuel recalculé (et brut retéléchargé par _load_raw).
        self._vieillir(ec._RAW_CACHE_PATH, ec._RAW_CACHE_TTL_DAYS + 1)
        ec.extract_indicators(2021)
        assert len(appels) == 3

    def test_brut_expire_retelecharge_et_changement_signale(self, caches, monkeypatch, caplog):
        csv = TestLoadRaw.CSV.copy()
        monkeypatch.setattr(ec, "_download_csv", lambda url: csv.copy())
        ec._load_raw()
        self._vieillir(ec._RAW_CACHE_PATH, ec._RAW_CACHE_TTL_DAYS + 1)
        ec._load_raw()  # contenu inchangé : pas de cache précédent
        assert not ec._RAW_CACHE_PRECEDENT_PATH.exists()
        self._vieillir(ec._RAW_CACHE_PATH, ec._RAW_CACHE_TTL_DAYS + 1)
        csv.loc[0, "valeur"] = "3,5"
        with caplog.at_level("WARNING"):
            raw = ec._load_raw()
        assert "bouquet OREC modifié" in caplog.text
        assert ec._RAW_CACHE_PRECEDENT_PATH.exists()
        assert 3.5 in set(raw["valeur"]) and ec._age_jours(ec._RAW_CACHE_PATH) < 1
