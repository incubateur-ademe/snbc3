"""
tests/test_classeur.py
======================
Couche openpyxl du générateur de classeur (`standalone_tool/classeur.py`) :
sémantique d'écriture reprise d'Excel, tableaux structurés, graphiques. Sans
Excel. La génération complète du classeur est testée si
`data/output/bdd_epci_wide.xlsx` est présent.

Lancer avec : uv run python -m pytest tests/test_classeur.py -v
"""

import io
import re
import zipfile

import openpyxl
import pytest

from standalone_tool.classeur import Classeur, couleur_auto, nom_feuille

# Thème Office 2013-2022 (celui du template)
ACCENTS = [(0x44, 0x72, 0xC4), (0xED, 0x7D, 0x31), (0xA5, 0xA5, 0xA5),
           (0xFF, 0xC0, 0x00), (0x5B, 0x9B, 0xD5), (0x70, 0xAD, 0x47)]


@pytest.fixture
def classeur(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "F"
    wb.create_sheet("TOUS SECTEURS")
    p = tmp_path / "c.xlsx"
    wb.save(p)
    return Classeur(p)


class TestEcriture:
    def test_chaine_numerique_convertie_comme_excel(self, classeur):
        ws = classeur.sheets["F"]
        ws.range("A1").value = [["74", "2A004", "", "=A1"]]
        assert [ws.ws.cell(1, c).value for c in range(1, 5)] == [74, "2A004", None, "=A1"]

    def test_liste_en_ligne_ou_en_colonne(self, classeur):
        ws = classeur.sheets["F"]
        ws.range("A1").value = [1, 2, 3]
        ws.range("A3").options(transpose=True).value = [4, 5]
        assert ws.range("A1:C1").value == [1, 2, 3]
        assert ws.range("A3:A4").value == [4, 5]

    def test_formule_unique_recopiee_avec_decalage(self, classeur):
        ws = classeur.sheets["F"]
        ws.range((2, 2), (4, 2)).formula = "=$A2*C2+'TOUS SECTEURS'!$D$1"
        assert ws.ws["B4"].value == "=$A4*C4+'TOUS SECTEURS'!$D$1"

    def test_format_herite_de_la_cellule_reprise(self, classeur):
        ws = classeur.sheets["F"]
        ws.range("A1").number_format = "0.0"
        ws.range("B1").formula = "=A1"
        ws.range("C1").formula = "=MAX(A1,0)"
        ws.range("D1").formula = '=IF(A1="",NA(),A1)'
        assert [ws.ws[c].number_format for c in ("B1", "C1", "D1")] == ["0.0", "0.0", "General"]

    def test_nom_feuille(self):
        assert nom_feuille("CALCUL") == "CALCUL"
        assert nom_feuille("Traj_observée") == "Traj_observée"
        assert nom_feuille("TOUS SECTEURS") == "'TOUS SECTEURS'"
        assert nom_feuille("CSC1") == "'CSC1'"


class TestTableau:
    def test_update_redimensionne_et_synchronise_les_colonnes(self, tmp_path):
        import pandas as pd
        from openpyxl.worksheet.table import Table
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["a", "b"])
        ws.append([1, 2])
        ws.add_table(Table(displayName="T", ref="A1:B2"))
        p = tmp_path / "t.xlsx"
        wb.save(p)
        c = Classeur(p)
        t = c.sheets[ws.title].tables[0]
        t.update(pd.DataFrame({"a": [1, 2, 3], "b": [4, None, 6], "c": ["7", "x", "9"]}))
        assert t.table.ref == "A1:C4"
        assert [col.name for col in t.table.tableColumns] == ["a", "b", "c"]
        assert c.sheets[ws.title].range("B3").value is None
        assert c.sheets[ws.title].range("C2").value == 7
        t.supprimer_lignes([2])
        assert t.table.ref == "A1:C3"
        assert c.sheets[ws.title].range("A3").value == 3
        assert t.ajouter_ligne() == 4


class TestGraphiques:
    @pytest.mark.parametrize("i, n, attendu", [
        (0, 1, "4472C4"), (0, 6, "3B64AD"), (6, 7, "8FA2D4"),
        (11, 16, "70AD47"), (12, 16, "A7B5DB"), (9, 20, "EFB300"),
    ])
    def test_couleur_auto_excel(self, i, n, attendu):
        # Valeurs relevées sur Excel (style 2) ; écart toléré d'une unité par canal
        got = couleur_auto(i, n, ACCENTS)
        assert all(abs(int(got[k:k + 2], 16) - int(attendu[k:k + 2], 16)) <= 1 for k in (0, 2, 4))

    def test_graphique_combine(self, classeur, tmp_path):
        ws = classeur.sheets["TOUS SECTEURS"]
        ws.range("B1").value = [[2021, 2022, 2023]]
        ws.range("A2").value = [["pos", 1, 2, 3], ["neg", -1, -2, -3],
                                ["sync max", 1, 2, 3], ["sync min", -1, -2, -3]]
        g = ws.charts.add(left=0, top=100, width=300, height=200)
        g.set_source_data(ws.range("A1:D5"))
        g.chart_type = "area_stacked"
        g.titre = "Test"
        g.legende = "r"
        g.series[1] = {"axe": 2, "remplissage": "70AD47", "trait": "none"}
        g.series[2] = {"type": "line", "axe": 2, "trait": "none", "sans_marqueur": True}
        g.series[3] = {"type": "line", "trait": "none", "sans_marqueur": True}
        g.axe2_masque = True
        g.legende_supprimee = [2, 3]
        out = tmp_path / "g.xlsx"
        classeur.save(out)
        with zipfile.ZipFile(out) as z:
            x = z.read("xl/charts/chart1.xml").decode()
        assert "dispNaAsBlank" in x and '<autoTitleDeleted val="0" />' in x
        assert '<roundedCorners val="0" />' in x
        # Style Excel 2010+ (sinon rendu Excel 2007) et caches de séries (sinon
        # Excel ignore les entrées de légende supprimées à l'ouverture)
        assert '<c14:style val="102"/>' in x
        assert x.count("<numCache>") == 8 and "<strCache>" in x
        # Axe secondaire masqué : étiquettes retirées
        assert re.search(r'<valAx><axId val="600" />.*?<tickLblPos val="none" />', x)
        # Indices de série = rang dans la plage source, quel que soit le groupe
        assert re.findall(r'<idx val="(\d+)" />', x)[:4] == ["0", "1", "3", "2"]
        assert "'TOUS SECTEURS'!$B$1:$D$1" in x
        wb = openpyxl.load_workbook(out)
        assert len(wb["TOUS SECTEURS"]._charts) == 1


@pytest.mark.donnees
def test_generation_complete(tmp_path):
    """Classeur complet généré sans Excel, relu par openpyxl."""
    from config.settings import OUTPUT_DIR, TRAJ_GES_TEMPLATE
    from standalone_tool.update_standalone_tool import run

    wide = OUTPUT_DIR / "bdd_epci_wide.xlsx"
    if not wide.exists():
        pytest.skip(f"{wide} absent (main.py export)")
    out = tmp_path / "classeur.xlsx"
    run(out, wide, template_path=TRAJ_GES_TEMPLATE)
    wb = openpyxl.load_workbook(out)
    assert wb["CALCUL"]["K5"].value == "=Annee_ref"
    assert sum(len(ws._charts) for ws in wb.worksheets) >= 30
    assert wb.defined_names["terr_zone"].attr_text == "Territoire!$B$3"
    assert wb["Territoire"].data_validations.dataValidation
    # Export /excel de l'API sur ce classeur : territoire retrouvé, formes conservées
    from api.excel_export import generate_territoire_workbook
    code = str(wb["Territoire"]["X8"].value)
    octets, _ = generate_territoire_workbook(out, code, [])
    exp = openpyxl.load_workbook(io.BytesIO(octets))["Territoire"]
    assert exp["A7"].value == wb["Territoire"]["Z8"].value and exp["A8"].value is None
    assert octets.count(b"xl/drawings/drawing") >= out.read_bytes().count(b"xl/drawings/drawing")
    # Formes des onglets non reconstruits (flèche, zones de texte), recopiées
    # depuis le template après l'enregistrement par openpyxl
    from standalone_tool.classeur import _onglets, _relations
    with zipfile.ZipFile(out) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    onglets = _onglets(parts)
    for nom, n_formes in (("Traj_planifiée", 2), ("Traj_observée", 1)):
        dessins = [c for _, t, c in _relations(parts, onglets[nom]) if t.endswith("/drawing")]
        assert len(dessins) == 1, nom
        assert len(re.findall(rb"<xdr:sp[ >]", parts[dessins[0]])) == n_formes, nom
