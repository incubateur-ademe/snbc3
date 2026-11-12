"""
tests/test_territorialize.py
=============================
Tests unitaires de la territorialisation (pipeline/territorialize.py).

Tests portant sur la logique de calcul (mode additif, recalage, pivots),
sans dépendance aux données réelles - uniquement des DataFrames synthétiques.

Lancer avec : uv run python -m pytest tests/test_territorialize.py -v
"""

import numpy as np
import pandas as pd
import pytest

from pipeline.territorialize import (
    compute_pivot_ratios,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df_long(epci_values: dict[str, float], indicator: str, annee: int = 2021,
                  zone: str = "HEXAGONE", total: float | None = None) -> pd.DataFrame:
    """Construit un df_long minimal avec N EPCI + une ligne total de zone."""
    rows = []
    for code, val in epci_values.items():
        rows.append({
            "code_epci": code, "code_indicateur": indicator,
            "annee": annee, "valeur": val, "zone": zone,
        })
    if total is None:
        total = sum(epci_values.values())
    rows.append({
        "code_epci": "HEXAGONE", "code_indicateur": indicator,
        "annee": annee, "valeur": total, "zone": None,
    })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# compute_pivot_ratios : abs_weight
# ---------------------------------------------------------------------------

class TestPivotRatios:
    def test_abs_weight_somme_un(self):
        """Les poids absolus doivent sommer à 1."""
        df = _make_df_long({"A": 100, "B": -50, "C": 30}, "PIV")
        ratios = compute_pivot_ratios(df, {"PIV"}, "HEXAGONE", 2021)
        assert ratios["abs_weight"].sum() == pytest.approx(1.0)

    def test_abs_weight_proportionnel(self):
        """Le poids est proportionnel à la valeur absolue."""
        df = _make_df_long({"A": 60, "B": -40}, "PIV")
        ratios = compute_pivot_ratios(df, {"PIV"}, "HEXAGONE", 2021)
        w = ratios.set_index("code_epci")["abs_weight"]
        assert w["A"] == pytest.approx(0.6)
        assert w["B"] == pytest.approx(0.4)

    def test_pivot_ratio_unchanged(self):
        """Le pivot_ratio classique n'est pas affecté par abs_weight."""
        df = _make_df_long({"A": 60, "B": -40}, "PIV")
        ratios = compute_pivot_ratios(df, {"PIV"}, "HEXAGONE", 2021)
        r = ratios.set_index("code_epci")["pivot_ratio"]
        assert r["A"] == pytest.approx(60 / 20)
        assert r["B"] == pytest.approx(-40 / 20)

    def test_abs_weight_all_zero(self):
        """Si toutes les valeurs sont nulles, poids = 0 (pas de division par zéro)."""
        df = _make_df_long({"A": 0, "B": 0}, "PIV", total=0)
        ratios = compute_pivot_ratios(df, {"PIV"}, "HEXAGONE", 2021)
        if not ratios.empty:
            assert all(ratios["abs_weight"] == 0)


# ---------------------------------------------------------------------------
# Mode additif : formule recalé(t) = obs(ref) + delta_nat × |facteur| × weight
# ---------------------------------------------------------------------------

class TestModeAdditif:
    """
    Teste le mode additif de bout en bout sur un cas synthétique minimal,
    en reproduisant les étapes de territorialize() sans appeler la vraie
    fonction (qui nécessite SNBC3 + données réelles). Le comportement réel
    est verrouillé par `TestModeAdditifDonnees`.
    """

    def _build_additif_case(self):
        """
        3 EPCI, 1 sous-secteur UTCATF, 3 années.

        ALDO observations (ref 2021) :
          A = -10, B = +5, C = -3  (total = -8, en ktCO2e)

        National SNBC3 (MtCO2e) :
          2021 = +0.020  (20 ktCO2e, signe opposé à ALDO !)
          2030 = +0.010
          2050 = -0.005

        Facteur de recalage f = ALDO_total / nat_ref = -0.008 / 0.020 = -0.4,
        utilisé en valeur absolue (|f| = 0.4) pour garder le sens du national.

        Attendu (additif) :
          obs_ref(A) = -10 × 1e-3 = -0.010 MtCO2e
          nat_ref = 0.020, nat(2030) = 0.010
          weight(A) = 10/18 = 0.556
          recalé(A, 2030) = -0.010 + (0.010 - 0.020) × 0.4 × 0.556 = -0.01222
        """
        epci_vals = {"A": -10.0, "B": 5.0, "C": -3.0}
        abs_total = sum(abs(v) for v in epci_vals.values())

        nat_values = {2021: 0.020, 2030: 0.010, 2050: -0.005}
        facteur = abs(sum(epci_vals.values()) * 1e-3 / nat_values[2021])

        rows = []
        for epci, obs_kt in epci_vals.items():
            weight = abs(obs_kt) / abs_total
            obs_mt = obs_kt * 1e-3
            for year, nat in nat_values.items():
                pivot_ratio = obs_kt / sum(epci_vals.values())
                brut = nat * pivot_ratio
                if year == 2021:
                    recal_factor = obs_mt / brut if brut != 0 else 1.0
                    recalee = brut * recal_factor
                else:
                    recalee = obs_mt + (nat - nat_values[2021]) * facteur * weight
                rows.append({
                    "code_epci": epci, "annee": year,
                    "valeur_nationale": nat, "pivot_ratio": pivot_ratio,
                    "abs_weight": weight, "obs_mt": obs_mt,
                    "valeur_brute": brut,
                    "expected_recalee": recalee,
                })
        return pd.DataFrame(rows), epci_vals, nat_values

    def test_ref_year_equals_observation(self):
        """A l'année de référence, recalé = observation ALDO."""
        df, vals, _ = self._build_additif_case()
        ref = df[df["annee"] == 2021]
        for _, row in ref.iterrows():
            assert row["expected_recalee"] == pytest.approx(
                row["obs_mt"], abs=1e-9
            )

    def test_delta_conservation(self):
        """Sum(recalé(t)) = ALDO_total + |f| × (national(t) - national(ref))."""
        df, vals, nat_values = self._build_additif_case()
        aldo_total = sum(v * 1e-3 for v in vals.values())
        facteur = abs(aldo_total / nat_values[2021])
        for year in [2030, 2050]:
            sy = df[df["annee"] == year]
            actual_sum = sy["expected_recalee"].sum()
            expected_sum = aldo_total + facteur * (nat_values[year] - nat_values[2021])
            assert actual_sum == pytest.approx(expected_sum, abs=1e-9)

    def test_all_epci_follow_national_direction(self):
        """Avec le mode additif, tous les EPCI évoluent dans le même sens que le national."""
        df, _, nat_values = self._build_additif_case()
        for year in [2030, 2050]:
            delta_nat = nat_values[year] - nat_values[2021]
            for epci in ["A", "B", "C"]:
                ref_val = df[(df["code_epci"] == epci) & (df["annee"] == 2021)]["expected_recalee"].iloc[0]
                yr_val = df[(df["code_epci"] == epci) & (df["annee"] == year)]["expected_recalee"].iloc[0]
                delta_epci = yr_val - ref_val
                if delta_nat != 0:
                    assert np.sign(delta_epci) == np.sign(delta_nat), (
                        f"EPCI {epci} year {year}: delta_epci={delta_epci:.6f} "
                        f"vs delta_nat={delta_nat:.6f}"
                    )

    def test_sign_inversion_handled(self):
        """
        Quand ALDO et SNBC ont des signes opposés (comme Zones humides),
        le mode additif ne produit pas de recal_factor négatif propagé.
        """
        df, _, nat_values = self._build_additif_case()
        # EPCI A : obs = -0.010 (séquestration), national = +0.020 (émission)
        a_ref = df[(df["code_epci"] == "A") & (df["annee"] == 2021)]["expected_recalee"].iloc[0]
        assert a_ref < 0  # séquestration maintenue
        a_2050 = df[(df["code_epci"] == "A") & (df["annee"] == 2050)]["expected_recalee"].iloc[0]
        assert a_2050 < a_ref  # renforcement de la séquestration (national baisse)


# ---------------------------------------------------------------------------
# Configuration par zone (bloc `zones` de config/traj_supra.yaml)
# ---------------------------------------------------------------------------

class TestConfigZone:
    """
    La zone DROM se règle entièrement par configuration : aucun code spécifique,
    aucun second jeu de sous-secteurs. Ces tests verrouillent les trois effets
    attendus et surtout la non-régression de l'hexagone.
    """

    def test_hexagone_non_affecte(self):
        """Aucun bloc zone ne s'applique à l'hexagone."""
        import yaml
        from config.settings import ROOT_DIR
        from pipeline.territorialize import load_correspondance

        with open(ROOT_DIR / "config" / "traj_supra.yaml", encoding="utf-8") as f:
            brut = yaml.safe_load(f)["sous_secteurs"]

        hx = load_correspondance("HEXAGONE")
        assert len(hx) == len(brut)
        assert "UTCATF" in set(hx["secteur"])
        assert hx["correction_dju_chaud"].sum() > 0
        assert hx["correction_dju_froid"].sum() > 0

    def test_drom_secteurs_desactives(self):
        """UTCATF est retiré : ni ALDO ni l'IGT ne couvrent les puits aux DROM."""
        from pipeline.territorialize import load_correspondance

        hx = load_correspondance("HEXAGONE")
        dr = load_correspondance("DROM")
        n_utcatf = (hx["secteur"] == "UTCATF").sum()

        assert n_utcatf > 0, "fixture : l'hexagone doit avoir des sous-secteurs UTCATF"
        assert "UTCATF" not in set(dr["secteur"])
        assert len(dr) == len(hx) - n_utcatf

    def test_drom_corrections_dju_eteintes(self):
        """Aucune correction climatique aux DROM (cf. justification dans le YAML)."""
        from pipeline.territorialize import load_correspondance

        dr = load_correspondance("DROM")
        assert dr["correction_dju_chaud"].sum() == 0
        assert dr["correction_dju_froid"].sum() == 0

    def test_bloc_zone_ne_peut_pas_activer_une_correction(self):
        """`corrections_dju` peut éteindre, jamais allumer."""
        from pipeline.territorialize import load_correspondance

        hx = load_correspondance("HEXAGONE")
        dr = load_correspondance("DROM")
        # Sur les sous-secteurs communs, DROM ne doit jamais avoir une
        # correction active là où l'hexagone ne l'a pas.
        j = hx.merge(dr, on="code_traj_supra", suffixes=("_hx", "_dr"))
        for sens in ("chaud", "froid"):
            active_en_trop = j[f"correction_dju_{sens}_dr"] & ~j[f"correction_dju_{sens}_hx"]
            assert not active_en_trop.any()

    def test_drom_pivots_surcharges(self):
        """Les pivots indisponibles aux DROM sont bien redirigés."""
        from pipeline.territorialize import load_correspondance

        dr = load_correspondance("DROM").set_index("code_traj_supra")["code_pivot"]
        attendu = {
            "emi_mob_vp_domtra":  "PARC_VP",
            "cons_mob_vp_domtra": "PARC_VP",
            "cons_agr":           "SAU",
            "cons_mob_vp_autre":  "EC_EMIS_TRANSPORT_ROUTIER",
            "cons_tra_autres":    "EC_EMIS_AUTRE_TRANSPORT",
            "cons_dech":          "EC_EMIS_DECHETS",
        }
        for code, pivot in attendu.items():
            assert dr[code] == pivot, f"{code} : pivot DROM attendu {pivot}, obtenu {dr[code]}"

    def test_aucun_pivot_drom_indisponible(self):
        """
        Garde-fou de configuration : aucun sous-secteur DROM ne doit pointer
        vers un indicateur que la zone ne peut pas fournir (OREC, mobilité
        domicile-travail, ALDO).
        """
        from pipeline.territorialize import load_correspondance

        dr = load_correspondance("DROM")
        interdits = {
            p for p in dr["code_pivot"]
            if p.startswith(("EC_CONSO_", "MOB_DT_", "ALDO_"))
        }
        assert not interdits, f"pivots indisponibles aux DROM : {sorted(interdits)}"


class TestCorrespondanceUnion:
    """
    Le tableur standalone porte toutes les zones dans un seul classeur : ses
    onglets sont construits sur l'union des sous-secteurs, les métadonnées
    variant par zone étant résolues par formule.
    """

    def test_union_couvre_toutes_les_zones(self):
        from pipeline.territorialize import (
            load_correspondance, load_correspondance_union,
        )

        hx = load_correspondance("HEXAGONE")
        dr = load_correspondance("DROM")
        u = load_correspondance_union(["HEXAGONE", "DROM"])

        codes = set(u["code_traj_supra"])
        assert codes == set(hx["code_traj_supra"]) | set(dr["code_traj_supra"])
        # UTCATF n'existe que côté hexagone : l'union doit le conserver, sinon
        # l'onglet UTCATF du classeur disparaîtrait pour tout le monde.
        assert "UTCATF" in set(u["secteur"])

    def test_flags_en_ou_logique(self):
        """
        Une étape de correction doit exister dès qu'une zone l'applique : le
        DROM éteint toutes les corrections DJU, l'union doit les garder.
        """
        from pipeline.territorialize import (
            load_correspondance, load_correspondance_union,
        )

        hx = load_correspondance("HEXAGONE")
        u = load_correspondance_union(["HEXAGONE", "DROM"])
        for flag in ("correction_dju_chaud", "correction_dju_froid",
                     "influence_demographie"):
            assert u[flag].sum() == hx[flag].sum() > 0

    def test_ordre_stable(self):
        """L'ordre suit la première zone, gage de lisibilité du tableau."""
        from pipeline.territorialize import (
            load_correspondance, load_correspondance_union,
        )

        hx = list(load_correspondance("HEXAGONE")["code_traj_supra"])
        u = list(load_correspondance_union(["HEXAGONE", "DROM"])["code_traj_supra"])
        assert u[:len(hx)] == hx


class TestZonesDrom:
    """
    Chaque DROM est sa propre zone (config.settings) : EPCI, total de zone et
    réglages hérités du groupe DROM.
    """

    def test_zones_demandees_developpe_le_groupe(self):
        from config.settings import zones_demandees

        assert zones_demandees(["HEXAGONE", "DROM"]) == [
            "HEXAGONE", "GUADELOUPE", "MARTINIQUE", "GUYANE", "REUNION",
        ]
        # Mayotte n'est pas territorialisée, même demandée explicitement.
        assert zones_demandees(["MAYOTTE"]) == []
        assert zones_demandees(["GUYANE", "DROM"]) == [
            "GUYANE", "GUADELOUPE", "MARTINIQUE", "REUNION",
        ]

    def test_zone_herite_du_groupe(self):
        from pipeline.territorialize import load_correspondance, load_zone_config

        cfg = load_zone_config("GUYANE")
        assert cfg["corrections_dju"] == {"chaud": False, "froid": False}
        assert cfg["onglet_bilan"] == "Bilans énergie Guyane"
        assert "trajectoire_derivee" in cfg
        cols = ["code_traj_supra", "code_pivot", "correction_dju_chaud"]
        pd.testing.assert_frame_equal(
            load_correspondance("GUYANE")[cols], load_correspondance("DROM")[cols]
        )

    def test_epci_de_zone(self):
        from pipeline.territorialize import epci_de_zone

        df = pd.DataFrame([
            {"code_epci": "A", "zone": "REUNION"},
            {"code_epci": "B", "zone": "MAYOTTE"},
            {"code_epci": "C", "zone": "HEXAGONE"},
            {"code_epci": "DROM", "zone": None},
        ])
        assert epci_de_zone(df, "REUNION") == {"A"}

    def test_totaux_par_zone_et_par_groupe(self):
        """compute_totaux produit une ligne par zone et une par groupe."""
        from pipeline.aggregate import compute_totaux

        df = pd.DataFrame([
            {"code_epci": e, "code_indicateur": "POP", "annee": 2021, "valeur": v,
             "niveau_geo": "EPCI", "source": "T", "date_extraction": "", "qualite": "ok"}
            for e, v in [("A", 10.0), ("B", 20.0), ("C", 5.0), ("D", 100.0)]
        ])
        zones = {"A": "GUYANE", "B": "REUNION", "C": "MAYOTTE", "D": "HEXAGONE"}
        t = compute_totaux(df, zones).set_index("code_epci")["valeur"]
        assert t["GUYANE"] == 10.0 and t["REUNION"] == 20.0 and t["MAYOTTE"] == 5.0
        assert t["DROM"] == 35.0
        assert t["HEXAGONE"] == 100.0 and t["FRANCE"] == 135.0


# ---------------------------------------------------------------------------
# Parts des zones dans les bilans énergie DGEC
# ---------------------------------------------------------------------------

class TestPartsBilans:
    """
    Parts de chaque DROM dans la conso de chaque secteur, lues dans l'onglet de
    parts du classeur publié (calculé depuis les bilans par
    scripts/publier_snbc3.py).
    """

    def test_parts_forment_une_partition(self):
        """Σ des cinq DROM (Mayotte comprise) = 1 par secteur et par année."""
        from pipeline.territorialize import load_zone_config, parts_bilans

        parts = parts_bilans("DROM")
        src = load_zone_config("DROM")["trajectoire_derivee"]
        assert set(parts["secteur"]) == set(src["secteurs"]) | set(src["alias"])
        assert set(parts["zone"]) == {"GUADELOUPE", "MARTINIQUE", "GUYANE", "REUNION", "MAYOTTE"}
        sommes = parts.groupby(["secteur", "annee"])["part"].sum()
        assert sommes.to_numpy() == pytest.approx(1.0, abs=1e-3)

    def test_interpolation_des_parts(self):
        from pipeline.territorialize import _interpoler_parts

        parts = pd.DataFrame([
            {"zone": "GUYANE", "secteur": "Résidentiel", "annee": 2019, "part": 0.2},
            {"zone": "GUYANE", "secteur": "Résidentiel", "annee": 2025, "part": 0.5},
        ])
        out = _interpoler_parts(parts, [2018, 2021, 2025, 2030]).set_index("annee")["part"]
        assert out[2018] == pytest.approx(0.2)   # avant la plage : 1re valeur
        assert out[2021] == pytest.approx(0.3)   # linéaire
        assert out[2030] == pytest.approx(0.5)   # après la plage : dernière valeur

    @staticmethod
    def _classeur_modifie(tmp_path, modifier):
        import openpyxl

        from config.settings import SNBC3_RETRAITEE_PATH

        chemin = tmp_path / "modifie.xlsx"
        wb = openpyxl.load_workbook(SNBC3_RETRAITEE_PATH)
        modifier(wb)
        wb.save(chemin)
        return chemin

    def test_onglet_manquant_leve_une_erreur(self, tmp_path):
        """
        Un onglet de parts absent doit échouer bruyamment : se rabattre sur
        l'absence de calage produirait des résultats faux sans le signaler.
        """
        from pipeline.territorialize import load_zone_config, parts_bilans

        onglet = load_zone_config("DROM")["trajectoire_derivee"]["onglet_parts"]
        ampute = self._classeur_modifie(tmp_path, lambda wb: wb.remove(wb[onglet]))
        with pytest.raises(RuntimeError, match=onglet):
            parts_bilans("DROM", ampute)

    def test_somme_differente_de_un_leve_une_erreur(self, tmp_path):
        from pipeline.territorialize import load_zone_config, parts_bilans

        onglet = load_zone_config("DROM")["trajectoire_derivee"]["onglet_parts"]

        def doubler(wb):
            c = wb[onglet].cell(2, 3)
            c.value = 2 * c.value

        with pytest.raises(RuntimeError, match="≠ 1"):
            parts_bilans("DROM", self._classeur_modifie(tmp_path, doubler))

    def test_groupe_n_est_pas_territorialisable(self):
        from pipeline.territorialize import trajectoire_zone

        with pytest.raises(ValueError, match="groupe"):
            trajectoire_zone({"emi_dech"}, "DROM", pd.DataFrame())


class TestTotauxDepuisTableTableur:
    """Lecture des lignes de total de Data_EPCI (tableur) au format long."""

    def test_format_long_et_cellules_vides(self):
        from standalone_tool.update_standalone_tool import totaux_long_depuis_table

        headers = ["code_epci", "nom_epci", "MEN_2021", "POP_PROJ_RATIO_2030"]
        rows = [
            ["GUYANE", "GUYANE", 100.0, 1.2],
            ["MAYOTTE", "MAYOTTE", None, 1.5],      # MEN absent : pas de ligne
            ["200000172", "EPCI", 5.0, 1.0],       # EPCI : ignoré
        ]
        df = totaux_long_depuis_table(headers, rows)
        assert set(df["code_epci"]) == {"GUYANE", "MAYOTTE"}
        assert df[(df.code_epci == "MAYOTTE") & (df.code_indicateur == "MEN")].empty
        v = df[(df.code_epci == "GUYANE") & (df.code_indicateur == "POP_PROJ_RATIO")]
        assert v["annee"].iloc[0] == 2030 and v["valeur"].iloc[0] == 1.2


# ---------------------------------------------------------------------------
# Conservation nationale : Σ EPCI (bruts, "somme des tirs individuels") vs
# trajectoire nationale SNBC3 ("tir unique" sur l'ensemble des EPCI). Utilise
# la BDD réelle (cache data/interim) - skip proprement si indisponible.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bdd_reelle():
    from config.settings import ANNEE_REF
    from pipeline.aggregate import build_bdd
    try:
        return build_bdd(annee=ANNEE_REF)
    except Exception as e:
        pytest.skip(f"BDD réelle indisponible : {e}")


class TestConservationNationale:
    """
    Σ_EPCI(valeur_brute) doit rester proche de la trajectoire nationale
    SNBC3 - seul résidu attendu : l'effet Jensen des corrections DJU/démo
    appliquées par EPCI plutôt qu'au niveau zone (cf. rapport méthodologique §3.6 « Conservation »,
    docstring de tests/test_excel_vs_python.py).
    """

    SEUIL_ECART = 0.05  # cohérent avec RTOL_CORRECTED (test_excel_vs_python.py)

    def test_ecart_conservation_borne(self, bdd_reelle):
        from pipeline.territorialize import territorialize
        from config.settings import ANNEE_REF

        df = territorialize(bdd_reelle, zone="HEXAGONE", ref_year=ANNEE_REF)
        ecarts = (
            df[["code_traj_supra", "annee", "ecart_conservation"]]
            .drop_duplicates()
            .dropna(subset=["ecart_conservation"])
        )
        depassements = ecarts[ecarts["ecart_conservation"].abs() > self.SEUIL_ECART]
        assert depassements.empty, (
            f"{len(depassements)} écart(s) de conservation > {self.SEUIL_ECART:.0%} "
            f"vs trajectoire SNBC3 :\n{depassements.to_string(index=False)}"
        )


class TestModeAdditifDonnees:
    """
    Mode additif (UTCATF) sur données réelles : le delta national est mis à
    l'échelle du facteur de recalage |f| = |Σ ALDO / national(ref)|, comme le
    recalage multiplicatif l'est ailleurs. Sans cette échelle, un delta calibré
    sur l'inventaire écrase les puits ALDO (forêts : ALDO = 69 % de l'inventaire).
    """

    @pytest.fixture(scope="class")
    def partition(self, bdd_reelle):
        """Résultats de l'hexagone restreints aux sous-secteurs additifs et aux EPCI."""
        from config.settings import ANNEE_REF
        from pipeline.territorialize import load_correspondance, territorialize

        df = territorialize(bdd_reelle, zone="HEXAGONE", ref_year=ANNEE_REF)
        corr = load_correspondance("HEXAGONE")
        codes = corr.loc[corr["mode_additif"].fillna(False).astype(bool), "code_traj_supra"]
        epci = set(bdd_reelle.loc[bdd_reelle["niveau_geo"] == "EPCI", "code_epci"])
        return df[df["code_traj_supra"].isin(set(codes)) & df["code_epci"].isin(epci)]

    def test_somme_suit_le_facteur_de_recalage(self, partition):
        """
        Σ EPCI recalé(t) = f × Σ EPCI brut(t) pour f > 0, comme pour les
        sous-secteurs multiplicatifs (sans recalage, f = 1 : somme = national).
        Pour f < 0 (ALDO et inventaire de signes opposés), seul le sens est
        garanti : cf. test_sens_de_variation_du_national.
        """
        from config.settings import ANNEE_REF

        echecs = []
        for code, sub in partition.groupby("code_traj_supra"):
            ref = sub[sub["annee"] == ANNEE_REF]
            brut_ref = ref["valeur_brute"].sum()
            f = ref["valeur_recalee"].sum() / brut_ref if brut_ref else 1.0
            if f <= 0:
                continue
            somme = sub.groupby("annee")[["valeur_brute", "valeur_recalee"]].sum()
            ecart = (somme["valeur_recalee"] - f * somme["valeur_brute"]).abs()
            if (ecart > 1e-6).any():
                echecs.append(f"{code} (f={f:.3f}) : écart max {ecart.max():.6f} Mt")
        assert not echecs, "Σ recalé ≠ f × national :\n" + "\n".join(echecs)

    def test_sens_de_variation_du_national(self, partition):
        """Chaque EPCI évolue dans le sens du national (delta ≥ 0 ou ≤ 0 comme lui)."""
        from config.settings import ANNEE_REF

        echecs = []
        for code, sub in partition.groupby("code_traj_supra"):
            piv = sub.pivot(index="code_epci", columns="annee", values="valeur_recalee")
            brut = sub.groupby("annee")["valeur_brute"].sum()   # = national (pas de DJU/démo)
            delta_nat = brut - brut[ANNEE_REF]
            for annee in piv.columns:
                if annee == ANNEE_REF or abs(delta_nat[annee]) < 1e-9:
                    continue
                delta_epci = (piv[annee] - piv[ANNEE_REF]).dropna()
                a_rebours = (np.sign(delta_epci) * np.sign(delta_nat[annee]) < 0).sum()
                if a_rebours:
                    echecs.append(f"{code} {annee} : {a_rebours} EPCI à contre-sens du national")
        assert not echecs, "\n".join(echecs)

class TestEpciUnique:
    """
    `epci_unique` (optimisation pour l'API à la demande, cf. api/) ne doit
    changer AUCUNE valeur produite pour le territoire demandé - seulement le
    périmètre des lignes retournées et la disponibilité du diagnostic
    `ecart_conservation` (zone entière, non calculable sur un seul
    territoire). Comparaison directe avec le calcul zone entière filtré a
    posteriori, sur données réelles.
    """

    def test_resultat_identique_au_filtre_a_posteriori(self, bdd_reelle):
        from pipeline.territorialize import epci_de_zone, territorialize
        from config.settings import ANNEE_REF

        code = sorted(epci_de_zone(bdd_reelle, "HEXAGONE"))[0]

        complet = territorialize(bdd_reelle, zone="HEXAGONE", ref_year=ANNEE_REF)
        attendu = (
            complet[complet["code_epci"] == code]
            .drop(columns=["ecart_conservation"])
            .sort_values(["type", "code_traj_supra", "annee"])
            .reset_index(drop=True)
        )

        rapide = territorialize(
            bdd_reelle, zone="HEXAGONE", ref_year=ANNEE_REF, epci_unique=code,
        )
        obtenu = (
            rapide.drop(columns=["ecart_conservation"])
            .sort_values(["type", "code_traj_supra", "annee"])
            .reset_index(drop=True)
        )

        pd.testing.assert_frame_equal(attendu, obtenu)
        assert rapide["ecart_conservation"].isna().all()

ZONES_DROM_PRODUITES = ["GUADELOUPE", "MARTINIQUE", "GUYANE", "REUNION"]


class TestPivotsDromDisponibles:
    """
    Chaque DROM territorialisé doit être servi par les données : pivots,
    totaux de zone et données de recalage IGT.
    """

    @pytest.mark.parametrize("zone", ZONES_DROM_PRODUITES)
    def test_tous_les_pivots_sont_calculables(self, bdd_reelle, zone):
        from config.settings import ANNEE_REF
        from pipeline.territorialize import compute_pivot_ratios, load_correspondance

        corr = load_correspondance(zone)
        pivots = set(corr["code_pivot"])
        ratios = compute_pivot_ratios(bdd_reelle, pivots, zone, ANNEE_REF)
        assert not ratios.empty, f"aucun EPCI dans la zone {zone}"
        manquants = pivots - set(ratios["code_pivot"])
        assert not manquants, f"{zone} : pivots sans ratio calculable : {sorted(manquants)}"

    @pytest.mark.parametrize("zone", ZONES_DROM_PRODUITES)
    def test_recalages_ges_servis_par_igt(self, bdd_reelle, zone):
        """Tous les codes de recalage GES doivent être couverts pour chaque EPCI."""
        from config.settings import ANNEE_REF
        from pipeline.territorialize import epci_de_zone, load_correspondance

        corr = load_correspondance(zone)
        epci_zone = epci_de_zone(bdd_reelle, zone)
        codes_ges = set(corr.loc[corr["type"] == "GES", "code_recalage"].dropna())
        dispo = bdd_reelle[
            bdd_reelle["code_epci"].isin(epci_zone)
            & (bdd_reelle["annee"] == ANNEE_REF)
            & bdd_reelle["valeur"].notna()
        ]
        for code in sorted(codes_ges):
            couverts = dispo.loc[dispo["code_indicateur"] == code, "code_epci"].nunique()
            assert couverts == len(epci_zone), (
                f"{zone} / {code} : {couverts}/{len(epci_zone)} EPCI couverts"
            )

    def test_mayotte_zone_a_part_non_produite(self, bdd_reelle):
        """Mayotte a sa zone et son total dans la BDD, sans être territorialisée."""
        from config.settings import ZONES_TERRITORIALISEES

        assert "MAYOTTE" not in ZONES_TERRITORIALISEES
        assert (bdd_reelle["zone"] == "MAYOTTE").any()
        totaux = set(bdd_reelle.loc[bdd_reelle["niveau_geo"] == "TOTAL", "code_epci"])
        assert {"DROM", "MAYOTTE", *ZONES_DROM_PRODUITES} <= totaux


class TestTrajectoireZoneDrom:
    """
    Territorialisation par DROM, sur données réelles.

    Référence : l'ancien moteur, qui territorialisait la trajectoire DROM sur
    le groupe entier (ratio au total DROM, facteur démographique normalisé
    sur le DROM, part de Mayotte retirée des pivots qui ne la couvrent pas).
    Le découpage en zones doit le reproduire exactement sur la branche GES,
    et ne modifier la branche conso que par le calage sur les bilans.
    """

    @pytest.fixture(scope="class")
    def drom(self, bdd_reelle):
        from pipeline.territorialize import territorialize

        return pd.concat(
            [territorialize(bdd_reelle, zone=z) for z in ZONES_DROM_PRODUITES],
            ignore_index=True,
        )

    @pytest.fixture(scope="class")
    def reference(self, bdd_reelle, drom):
        """Bruts de l'ancien moteur (zone DROM unique), recalculés ici."""
        from config.settings import ANNEE_REF
        from pipeline.territorialize import (
            compute_demo_factors, compute_pivot_ratios, load_correspondance,
            load_national_traj, load_zone_config, parts_bilans,
        )

        corr = load_correspondance("DROM")
        annee_hp = load_zone_config("DROM")["trajectoire_derivee"]["annee_part_hors_perimetre"]
        epci = set(drom["code_epci"])
        parts = parts_bilans("DROM")
        p_may = (
            parts[(parts["zone"] == "MAYOTTE") & (parts["annee"] == annee_hp)]
            .set_index("secteur")["part"]
        )
        may = set(bdd_reelle.loc[bdd_reelle["zone"] == "MAYOTTE", "code_epci"])
        couvre_mayotte = {
            p for p in set(corr["code_pivot"])
            if bdd_reelle[(bdd_reelle["code_indicateur"] == p)
                          & bdd_reelle["code_epci"].isin(may)]["valeur"].notna().any()
        }
        ref = (
            load_national_traj(set(corr["code_traj_supra"]), "DROM")
            .merge(corr, on="code_traj_supra")
            .merge(compute_pivot_ratios(bdd_reelle, set(corr["code_pivot"]), "DROM",
                                        ANNEE_REF, epci), on="code_pivot")
            .merge(compute_demo_factors(bdd_reelle, "DROM", ANNEE_REF, epci),
                   on=["code_epci", "annee"], how="left")
        )
        f_demo = np.where(ref["influence_demographie"], ref["demo_factor"].fillna(1.0), 1.0)
        f_may = np.where(ref["code_pivot"].isin(couvre_mayotte), 1.0,
                         1.0 - ref["secteur"].map(p_may).fillna(0.0))
        ref["brut_ref"] = ref["valeur_nationale"] * ref["pivot_ratio"] * f_demo * f_may
        return ref[["code_epci", "code_traj_supra", "annee", "brut_ref"]]

    def test_branche_ges_identique_a_la_zone_unique(self, drom, reference):
        g = drom[drom["type"] == "GES"].merge(
            reference, on=["code_epci", "code_traj_supra", "annee"]
        )
        assert len(g) == (drom["type"] == "GES").sum()
        assert g["valeur_brute"].to_numpy() == pytest.approx(
            g["brut_ref"].to_numpy(), rel=1e-9, abs=1e-15
        )

    def test_conso_calee_sur_les_bilans(self, drom):
        """
        Σ conso d'un DROM par secteur = trajectoire DROM du secteur × part du
        DROM dans le bilan. Seul résidu admis : l'effet Jensen du facteur
        démographique par EPCI à l'intérieur de la zone.
        """
        from pipeline.territorialize import (
            _interpoler_parts, load_correspondance, load_national_traj, parts_bilans,
        )

        conso = drom[drom["type"] == "conso"]
        corr = load_correspondance("DROM")
        nat = (
            load_national_traj(set(conso["code_traj_supra"]), "DROM")
            .merge(corr[["code_traj_supra", "secteur"]], on="code_traj_supra")
            .groupby(["secteur", "annee"])["valeur_nationale"].sum()
            .rename("nat").reset_index()
        )
        s = (
            conso.groupby(["zone", "secteur", "annee"])["valeur_brute"]
            .sum().rename("somme").reset_index()
            .merge(nat, on=["secteur", "annee"])
            .merge(_interpoler_parts(parts_bilans("DROM"), conso["annee"].unique()),
                   on=["zone", "secteur", "annee"])
        )
        s = s[s["nat"] != 0]
        assert not s.empty
        assert s["somme"].to_numpy() == pytest.approx(
            (s["nat"] * s["part"]).to_numpy(), rel=self.TOLERANCE_JENSEN
        )

    # Écart relatif toléré entre Σ conso d'un DROM et sa cible : effet Jensen
    # du facteur démographique par EPCI, mesuré à 6·10⁻⁵ sur données réelles.
    TOLERANCE_JENSEN = 1e-3

    def test_chemin_rapide_identique(self, bdd_reelle, drom):
        """`epci_unique` doit reproduire le calcul de la zone entière."""
        from pipeline.territorialize import territorialize

        code = sorted(drom.loc[drom["zone"] == "GUYANE", "code_epci"].unique())[0]
        cle = ["type", "code_traj_supra", "annee"]
        attendu = (
            drom[drom["code_epci"] == code].drop(columns=["ecart_conservation"])
            .sort_values(cle).reset_index(drop=True)
        )
        obtenu = (
            territorialize(bdd_reelle, zone="GUYANE", epci_unique=code)
            .drop(columns=["ecart_conservation"]).sort_values(cle).reset_index(drop=True)
        )
        pd.testing.assert_frame_equal(attendu, obtenu)



# ---------------------------------------------------------------------------
# Trajectoire nationale remplacée (--traj-nationale, --onglet)
# ---------------------------------------------------------------------------

class TestTrajNationaleRemplacee:

    @pytest.fixture(autouse=True)
    def _restaurer(self):
        from dataclasses import replace

        from config import settings

        avant = replace(settings.TRAJ_NATIONALE)
        yield
        settings.TRAJ_NATIONALE.chemin = avant.chemin
        settings.TRAJ_NATIONALE.onglets = avant.onglets
        settings.TRAJ_NATIONALE.nom = avant.nom

    def test_lue_a_l_appel(self, tmp_path):
        """
        La trajectoire remplacée est celle que lit la territorialisation, sans
        réimport : les fonctions la résolvent à l'appel. Une zone sans onglet
        dans la nouvelle correspondance n'est pas territorialisable.
        """
        import openpyxl

        from config.settings import utiliser_traj_nationale
        from pipeline.territorialize import trajectoire_zone

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Scénario HEX"
        ws.append(["Indicateur", "Secteur", "Usage", "Code traj supra", "Unité", 2020, 2050])
        ws.append(["Trajectoire", "Déchets", "", "emi_dech", "MtCO2eq", 10.0, 2.0])
        chemin = tmp_path / "scenario.xlsx"
        wb.save(chemin)

        utiliser_traj_nationale(chemin, {"HEXAGONE": "Scénario HEX"}, "Scénario")
        out = trajectoire_zone({"emi_dech"}, "HEXAGONE", pd.DataFrame())
        assert out.set_index("annee")["valeur_nationale"].to_dict() == {2020: 10.0, 2050: 2.0}
        with pytest.raises(ValueError, match="GUYANE"):
            trajectoire_zone({"emi_dech"}, "GUYANE", pd.DataFrame())


def test_dju_variante_inconnue():
    """Une variante inconnue lève une erreur au lieu d'un facteur DJU de 1."""
    from pipeline.territorialize import compute_dju_factors
    with pytest.raises(ValueError, match="variante de DJU inconnue"):
        compute_dju_factors(pd.DataFrame(), "HEXAGONE", dju_variant="costic")
