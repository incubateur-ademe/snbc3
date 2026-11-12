"""
tests/test_excel_vs_python.py
=============================
Test d'intégration : vérifie la cohérence entre les résultats du tableur Excel
(onglet CALCUL) et ceux du pipeline Python (pipeline/territorialize.py).

Deux sources d'écart structurel entre Excel et Python :

  1. Corrections DJU/démo : appliquées au niveau territoire (Excel) vs par EPCI
     (Python). Jensen's inequality -> ecart ~1-3%.

  2. Recalage partagé : quand plusieurs sous-secteurs partagent le même
     code_recalage (industrie, agriculture, transports, résidentiel, tertiaire),
     le facteur de recalage est un ratio de sommes dans Excel (territoire)
     mais une somme de ratios dans Python (par EPCI). Les sous-secteurs
     avec recalage propre (UTCATF, déchets, branche énergie) sont comparables.

Les tests se concentrent donc sur ce qui est directement comparable :
  - Bruts pour tous les secteurs (tolérance adaptée aux corrections)
  - Recalés uniquement pour les secteurs à recalage propre (one-to-one)
  - Cohérence interne du mode additif (propriété mathématique pure)

Prérequis :
  - Le tableur doit avoir été ouvert dans Excel puis enregistré : généré par
    openpyxl, il ne contient aucune valeur calculée.
  - Tous les EPCI hexagonaux doivent être sélectionnés (configuration par défaut).

Lancer avec : uv run python -m pytest tests/test_excel_vs_python.py -v
"""

import logging

import pandas as pd
import pytest

pytestmark = pytest.mark.excel

from config.settings import ANNEE_REF, TRAJ_GES_TARGET_PATH

logging.basicConfig(level=logging.WARNING)

RTOL_STRICT = 0.005
RTOL_CORRECTED = 0.03

YEARS_TO_CHECK = [ANNEE_REF, ANNEE_REF + 9, ANNEE_REF + 29]

ETAPE_BRUT = "Résultat brut"
ETAPE_RECALE = "Résultat recalé"
ETAPE_RATIO_RECALAGE = "Ratio recalage"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _read_excel_calcul():
    """Lit l'onglet CALCUL du tableur et retourne un DataFrame structuré."""
    import openpyxl

    path = TRAJ_GES_TARGET_PATH
    if not path.exists():
        pytest.skip(f"Tableur introuvable : {path}")

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if "CALCUL" not in wb.sheetnames:
        wb.close()
        pytest.skip("Onglet CALCUL absent du tableur")

    ws = wb["CALCUL"]
    if ws.cell(5, 11).value is None:
        wb.close()
        pytest.skip("Classeur non recalculé (généré par openpyxl, sans valeurs) : "
                    "l'ouvrir dans Excel puis l'enregistrer")

    year_cols = {}
    for c in range(11, 60):
        v = ws.cell(5, c).value
        if v is None:
            break
        year_cols[int(v)] = c

    if not year_cols:
        wb.close()
        pytest.skip("Pas de colonnes-annees dans CALCUL")

    records = []
    for r in range(6, 600):
        code = ws.cell(r, 4).value
        etape = ws.cell(r, 8).value
        if code is None:
            break
        if etape is None:
            continue

        etape_str = str(etape).strip()
        code_str = str(code).strip()

        for year, col in year_cols.items():
            val = ws.cell(r, col).value
            if val is not None:
                records.append({
                    "code_traj_supra": code_str,
                    "etape": etape_str,
                    "annee": year,
                    "valeur_excel_kt": float(val),
                })

    wb.close()

    if not records:
        pytest.skip("Aucune donnee lisible dans CALCUL (valeurs en cache vides)")

    return pd.DataFrame(records)


@pytest.fixture(scope="module")
def excel_data():
    return _read_excel_calcul()


@pytest.fixture(scope="module")
def df_long_reel():
    """BDD long réelle (cache data/interim), construite une seule fois pour le module."""
    from pipeline.aggregate import build_bdd
    return build_bdd(annee=ANNEE_REF)


@pytest.fixture(scope="module")
def python_data(df_long_reel):
    """
    Résultats Python restreints aux EPCI (partition de la zone) : les lignes
    EPT et Ville de Paris, imbriquées dans la Métropole du Grand Paris,
    doublonneraient sa contribution dans les sommes comparées à Excel, dont
    la sélection ne contient que des EPCI.
    """
    from pipeline.aggregate import NIVEAU_GEO_EPCI
    from pipeline.territorialize import territorialize
    df = territorialize(df_long_reel, zone="HEXAGONE", ref_year=ANNEE_REF)
    epci = set(df_long_reel.loc[df_long_reel["niveau_geo"] == NIVEAU_GEO_EPCI, "code_epci"])
    return df[df["code_epci"].isin(epci)]


@pytest.fixture(scope="module")
def correspondance():
    from pipeline.territorialize import load_correspondance
    return load_correspondance("HEXAGONE")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _excel_val(excel_data, code, etape, annee):
    mask = (
        (excel_data["code_traj_supra"] == code)
        & (excel_data["etape"] == etape)
        & (excel_data["annee"] == annee)
    )
    rows = excel_data[mask]
    if rows.empty:
        return None
    return rows.iloc[0]["valeur_excel_kt"]


def _python_sum(python_data, code, col, annee):
    mask = (python_data["code_traj_supra"] == code) & (python_data["annee"] == annee)
    s = python_data.loc[mask, col]
    if s.empty:
        return None
    return s.sum()


def _has_corrections(correspondance, code):
    row = correspondance[correspondance["code_traj_supra"] == code]
    if row.empty:
        return False
    r = row.iloc[0]
    return bool(r.get("correction_dju_chaud") or r.get("correction_dju_froid")
                or r.get("influence_demographie"))


def _has_own_recalage(correspondance, code):
    """True si le sous-secteur a un code_recalage qui lui est propre (one-to-one)."""
    row = correspondance[correspondance["code_traj_supra"] == code]
    if row.empty:
        return False
    rc = row.iloc[0].get("code_recalage")
    if not rc or pd.isna(rc):
        return False
    n = (correspondance["code_recalage"] == rc).sum()
    return n == 1


def _rtol_for(correspondance, code):
    return RTOL_CORRECTED if _has_corrections(correspondance, code) else RTOL_STRICT


def _find_etape(excel_data, keyword, exclude=None):
    """Trouve le nom exact d'une étape dans les données Excel."""
    for e in excel_data["etape"].unique():
        if keyword in e.lower():
            if exclude and exclude in e.lower():
                continue
            return e
    return None


def _compare_brut(excel_data, python_data, correspondance, annee=ANNEE_REF):
    """Compare les bruts Excel/Python pour tous les sous-secteurs à `annee`."""
    codes = correspondance["code_traj_supra"].tolist()
    mismatches = []

    for code in codes:
        excel_brut = _excel_val(excel_data, code, ETAPE_BRUT, annee)
        if excel_brut is None or excel_brut == 0:
            continue

        py_brut_mt = _python_sum(python_data, code, "valeur_brute", annee)
        if py_brut_mt is None:
            continue

        py_brut_kt = py_brut_mt * 1000
        ratio = py_brut_kt / excel_brut
        rtol = _rtol_for(correspondance, code)

        if abs(ratio - 1) > rtol:
            mismatches.append(
                f"{code}: Excel={excel_brut:.2f} Python={py_brut_kt:.2f} "
                f"ratio={ratio:.4f} (tol={rtol:.0%})"
            )
    return mismatches


def _compare_recale_one_to_one(excel_data, python_data, correspondance, annee=ANNEE_REF):
    """
    Compare les recalés Excel/Python à `annee`, uniquement pour les
    sous-secteurs à code_recalage propre (one-to-one). Retourne None si
    l'étape recalée est introuvable dans l'Excel (appelant : pytest.skip).
    """
    etape = _find_etape(excel_data, "recal", exclude="ratio")
    if etape is None:
        return None

    codes = [
        c for c in correspondance["code_traj_supra"]
        if _has_own_recalage(correspondance, c)
    ]
    mismatches = []

    for code in codes:
        excel_recal = _excel_val(excel_data, code, etape, annee)
        if excel_recal is None:
            continue

        py_recal_mt = _python_sum(python_data, code, "valeur_recalee", annee)
        if py_recal_mt is None:
            continue

        py_recal_kt = py_recal_mt * 1000

        if abs(excel_recal) < 0.01:
            if abs(py_recal_kt - excel_recal) > 1.0:
                mismatches.append(
                    f"{code}: Excel={excel_recal:.4f} Python={py_recal_kt:.4f}"
                )
            continue

        ratio = py_recal_kt / excel_recal
        rtol = _rtol_for(correspondance, code)
        if abs(ratio - 1) > rtol:
            mismatches.append(
                f"{code}: Excel={excel_recal:.2f} Python={py_recal_kt:.2f} "
                f"ratio={ratio:.4f} (tol={rtol:.0%})"
            )
    return mismatches


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBrutRefYear:
    """
    Bruts à l'année de référence.
    Tolerance adaptée : stricte sans corrections, relâchée avec.
    """

    def test_brut_ref_year(self, excel_data, python_data, correspondance):
        mismatches = _compare_brut(excel_data, python_data, correspondance, ANNEE_REF)
        assert not mismatches, (
            f"{len(mismatches)} ecart(s) brut hors tolerance en {ANNEE_REF}:\n"
            + "\n".join(mismatches)
        )


class TestRecaleOneToOne:
    """
    Recalés à l'année de référence, uniquement pour les sous-secteurs dont
    le code_recalage est propre (one-to-one). Pour ceux-là, le recalé = obs,
    et Python et Excel utilisent la même donnée source.
    """

    def test_recale_ref_year_one_to_one(self, excel_data, python_data, correspondance):
        mismatches = _compare_recale_one_to_one(excel_data, python_data, correspondance, ANNEE_REF)
        if mismatches is None:
            pytest.skip("Etape recale introuvable dans Excel")

        assert not mismatches, (
            f"Ecarts recale (one-to-one) en {ANNEE_REF}:\n"
            + "\n".join(mismatches)
        )


class TestAdditifConsistency:
    """
    Mode additif : Sum_EPCI recalé(t) = ALDO_total + |f| × delta_national, avec
    f = ALDO_total / national(ref) (f = 1 sans recalage). Propriété
    mathématique exacte ; pour f > 0, elle équivaut à Σ recalé(t) = f × national(t).
    """

    def test_additif_delta_conservation(self, python_data, correspondance):
        add_codes = correspondance.loc[
            correspondance.get("mode_additif", pd.Series(dtype=bool)).fillna(False),
            "code_traj_supra",
        ].tolist()

        if not add_codes:
            pytest.skip("Aucun sous-secteur en mode additif")

        failures = []
        for code in add_codes:
            sub = python_data[python_data["code_traj_supra"] == code]
            ref = sub[sub["annee"] == ANNEE_REF]
            if ref.empty:
                continue
            aldo_total = ref["valeur_recalee"].sum()
            brut_ref = ref["valeur_brute"].sum()
            facteur = abs(aldo_total / brut_ref) if brut_ref else 1.0

            for year in YEARS_TO_CHECK:
                if year == ANNEE_REF:
                    continue
                sy = sub[sub["annee"] == year]
                if sy.empty:
                    continue
                brut_t = sy["valeur_brute"].sum()
                recal_t = sy["valeur_recalee"].sum()
                expected = aldo_total + facteur * (brut_t - brut_ref)
                if abs(recal_t - expected) > 1e-6:
                    failures.append(
                        f"{code} {year}: recale={recal_t:.6f} "
                        f"attendu={expected:.6f} delta={recal_t - expected:.9f}"
                    )

        assert not failures, (
            "Conservation du delta national violee :\n" + "\n".join(failures)
        )


class TestAdditifVsExcel:
    """
    Recalés UTCATF (mode additif) à l'année de référence : le recalé = obs ALDO,
    identique dans les deux approches. Pas de correction DJU/démo.
    """

    def test_additif_recale_ref_matches_excel(
        self, excel_data, python_data, correspondance
    ):
        etape = _find_etape(excel_data, "recal", exclude="ratio")
        if etape is None:
            pytest.skip("Etape recale introuvable dans Excel")

        add_codes = correspondance.loc[
            correspondance.get("mode_additif", pd.Series(dtype=bool)).fillna(False),
            "code_traj_supra",
        ].tolist()

        mismatches = []
        for code in add_codes:
            excel_val = _excel_val(excel_data, code, etape, ANNEE_REF)
            if excel_val is None:
                continue

            py_val = _python_sum(python_data, code, "valeur_recalee", ANNEE_REF)
            if py_val is None:
                continue

            py_kt = py_val * 1000
            if abs(excel_val) < 0.01:
                if abs(py_kt - excel_val) > 1.0:
                    mismatches.append(f"{code}: Excel={excel_val:.4f} Python={py_kt:.4f}")
                continue

            ratio = py_kt / excel_val
            if abs(ratio - 1) > RTOL_STRICT:
                mismatches.append(
                    f"{code}: Excel={excel_val:.2f} Python={py_kt:.2f} "
                    f"ratio={ratio:.4f}"
                )

        assert not mismatches, (
            f"Ecarts recale additif en {ANNEE_REF}:\n" + "\n".join(mismatches)
        )

    def test_additif_future_years_logged(
        self, excel_data, python_data, correspondance
    ):
        """
        Compare les années futures (mode additif). Ne fait PAS échouer le test :
        l'écart est structurel (poids territoire-level vs somme de poids EPCI).
        """
        etape = _find_etape(excel_data, "recal", exclude="ratio")
        if etape is None:
            pytest.skip("Etape recale introuvable dans Excel")

        add_codes = correspondance.loc[
            correspondance.get("mode_additif", pd.Series(dtype=bool)).fillna(False),
            "code_traj_supra",
        ].tolist()

        log = logging.getLogger(__name__)
        for code in add_codes:
            for year in YEARS_TO_CHECK:
                if year == ANNEE_REF:
                    continue
                excel_val = _excel_val(excel_data, code, etape, year)
                py_val = _python_sum(python_data, code, "valeur_recalee", year)
                if excel_val is None or py_val is None:
                    continue
                py_kt = py_val * 1000
                if abs(excel_val) > 0.01:
                    ratio = py_kt / excel_val
                    log.info(
                        f"[additif] {code} {year}: "
                        f"Excel={excel_val:.2f} Python={py_kt:.2f} "
                        f"ratio={ratio:.4f}"
                    )
