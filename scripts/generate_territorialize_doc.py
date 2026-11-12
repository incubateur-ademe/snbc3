"""
Genere "docs/Territorialisation.xlsx" : dictionnaire des colonnes du parquet
de sortie (resultats_territorialises.parquet) et liste des sous-secteurs
code_traj_supra, tous deux lus depuis le code/la config en place pour rester
a jour sans double maintenance manuelle.
"""

import sys
from datetime import date
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# Permettre l'import de pipeline/ depuis scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.territorialize import (
    QUALITE_LEGENDE,
    TERRITORIALISE_COLUMNS,
    load_correspondance,
)

OUTPUT_PATH = Path("docs") / "Territorialisation.xlsx"

# --- Descriptions des colonnes du parquet ------------------------------------
# Pas de metadonnees "description" en langage naturel dans le code : ce
# dictionnaire est la seule source pour ce texte. La liste des colonnes elle-
# meme (l'ordre, l'existence) vient de TERRITORIALISE_COLUMNS, pas d'ici -
# un ajout/retrait de colonne fait echouer la generation (cf. assert ci-dessous)
# plutot que de produire une doc silencieusement incomplete.
COLONNES_META = {
    "code_epci": ("str", "Code SIREN de l'EPCI (identifiant INSEE)", "200000172"),
    "nom_epci": ("str", "Nom de l'EPCI", "CC Faucigny - Glieres"),
    "niveau_geo": ("str", "EPCI, EPT ou COMMUNE (Paris) : EPT et Paris subdivisent la MGP, a exclure d'une somme de territoires", "EPCI"),
    "zone": ("str", "Zone de territorialisation (HEXAGONE, GUADELOUPE, MARTINIQUE, GUYANE, REUNION)", "HEXAGONE"),
    "code_region": ("str", "Code region INSEE de l'EPCI", "84"),
    "code_departement": ("str", "Code departement INSEE de l'EPCI", "74"),
    "code_traj_supra": ("str", "Sous-secteur de la trajectoire nationale SNBC3 - voir onglet Sous-secteurs", "emi_UTCATF_for"),
    "type": ("str", "GES (emissions) ou conso (consommation d'energie)", "GES"),
    "secteur": ("str", "Secteur d'activite", "UTCATF"),
    "usage": ("str", "Usage / sous-categorie au sein du secteur (peut etre vide)", "Forets"),
    "code_pivot": ("str", "Indicateur EPCI utilise comme cle de repartition de la trajectoire nationale", "ALDO_UTCATF_FOR"),
    "unite": ("str", "MtCO2e ou TWh", "MtCO2e"),
    "annee": ("int", "2020 a 2050", "2020"),
    "valeur_brute": ("float", "Resultat avant recalage : national x pivot_ratio x corrections", "-0.008855"),
    "valeur_recalee": ("float", "valeur_brute recalee pour recoller a l'observe en annee de reference - colonne a utiliser par defaut", "-0.006333"),
    "code_recalage": ("str", "Code de l'indicateur observe (OREC dans l'hexagone, CITEPA IGT au DROM) servant de recalage ; vide si aucun ne s'applique", "ALDO_UTCATF_FOR"),
    "recal_factor": ("float", "Facteur applique = observe(annee ref) / somme(valeur_brute)(annee ref)", "0.690459"),
    "ecart_conservation": ("float", "Ecart relatif somme EPCI vs national, par sous-secteur/annee (diagnostic)", ""),
    "qualite": ("str", "Fiabilite du recalage pour la ligne - voir tableau Legende qualite", "ok"),
}

assert set(COLONNES_META) == set(TERRITORIALISE_COLUMNS), (
    "COLONNES_META desynchronise de TERRITORIALISE_COLUMNS - "
    f"manquantes: {set(TERRITORIALISE_COLUMNS) - set(COLONNES_META)}, "
    f"en trop: {set(COLONNES_META) - set(TERRITORIALISE_COLUMNS)}. "
    "Mettre a jour scripts/generate_territorialize_doc.py."
)

# --- Palette couleurs (coherente avec generate_sources_doc.py) --------------

BLEU_TITRE   = "1F3864"
BLEU_EN_TETE = "2E75B6"
GRIS_CLAIR   = "D9E2F3"
BLANC        = "FFFFFF"
VERT_SECTION = "375623"
VERT_CLAIR   = "E2EFDA"


def style_header(cell, bg=BLEU_EN_TETE, fg=BLANC, bold=True, size=10):
    cell.font = Font(bold=bold, color=fg, size=size, name="Calibri")
    cell.fill = PatternFill("solid", fgColor=bg)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

def style_title(cell, bg=BLEU_TITRE, fg=BLANC, size=13):
    cell.font = Font(bold=True, color=fg, size=size, name="Calibri")
    cell.fill = PatternFill("solid", fgColor=bg)
    cell.alignment = Alignment(horizontal="center", vertical="center")

def style_section(cell, bg=VERT_SECTION, fg=BLANC):
    cell.font = Font(bold=True, color=fg, size=10, name="Calibri")
    cell.fill = PatternFill("solid", fgColor=bg)
    cell.alignment = Alignment(horizontal="left", vertical="center")

def style_cell(cell, bg=BLANC, bold=False, wrap=True, align="left"):
    cell.font = Font(bold=bold, size=9, name="Calibri")
    cell.fill = PatternFill("solid", fgColor=bg)
    cell.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)

def thin_border():
    s = Side(style="thin", color="BFBFBF")
    return Border(left=s, right=s, top=s, bottom=s)

def set_border(ws, min_row, max_row, min_col, max_col):
    b = thin_border()
    for row in ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col):
        for cell in row:
            cell.border = b


def _save_workbook(wb, path: Path) -> bool:
    """
    Sauvegarde en gerant le cas frequent sous Windows ou le fichier est deja
    ouvert (verrouille par Excel) : avertit au lieu de planter, pour ne pas
    faire echouer tout `main.py full` a cause d'un onglet Excel laisse ouvert.
    """
    try:
        wb.save(path)
        return True
    except PermissionError:
        print(
            f"! Impossible d'ecrire {path} : le fichier est probablement "
            f"ouvert dans Excel. Fermez-le puis relancez la commande pour "
            f"regenerer la doc."
        )
        return False


def main():
    TODAY = date.today().isoformat()
    wb = openpyxl.Workbook()

    # -- Feuille 1 : Sous-secteurs --------------------------------------------

    hexagone = load_correspondance("HEXAGONE")
    drom = load_correspondance("DROM")
    drom_pivot = dict(zip(drom["code_traj_supra"], drom["code_pivot"]))
    drom_codes = set(drom["code_traj_supra"])

    ws1 = wb.active
    ws1.title = "Sous-secteurs"
    ws1.sheet_view.showGridLines = False
    ws1.freeze_panes = "A3"

    ws1.merge_cells("A1:G1")
    style_title(ws1["A1"])
    ws1["A1"] = f"Sous-secteurs code_traj_supra - Traj GES  (genere le {TODAY})"
    ws1.row_dimensions[1].height = 28

    headers1 = ["code_traj_supra", "secteur", "usage", "code_pivot (HEXAGONE)",
                "code_recalage", "Statut DROM", "Pivot DROM (si different)"]
    col_widths1 = [26, 16, 42, 22, 26, 30, 24]
    for col, (h, w) in enumerate(zip(headers1, col_widths1), 1):
        cell = ws1.cell(row=2, column=col, value=h)
        style_header(cell)
        ws1.column_dimensions[get_column_letter(col)].width = w
    ws1.row_dimensions[2].height = 26

    current_type, data_row = None, 3
    for _, s in hexagone.iterrows():
        if s["type"] != current_type:
            current_type = s["type"]
            label = "GES (emissions)" if current_type == "GES" else "Consommation d'energie"
            ws1.merge_cells(f"A{data_row}:G{data_row}")
            cell = ws1.cell(row=data_row, column=1, value=f"  {label.upper()}")
            style_section(cell)
            ws1.row_dimensions[data_row].height = 18
            data_row += 1

        code = s["code_traj_supra"]
        if code not in drom_codes:
            statut, pivot_drom = f"Desactive (secteur {s['secteur']} hors DROM)", ""
        elif drom_pivot.get(code) != s["code_pivot"]:
            statut, pivot_drom = "Actif, pivot different", drom_pivot.get(code, "")
        else:
            statut, pivot_drom = "Actif, meme pivot", ""

        vals = [code, s["secteur"], s["usage"] or "", s["code_pivot"],
                s.get("code_recalage") or "", statut, pivot_drom]
        bg = GRIS_CLAIR if data_row % 2 == 1 else BLANC
        for col, val in enumerate(vals, 1):
            style_cell(ws1.cell(row=data_row, column=col, value=val), bg=bg, wrap=False)
        ws1.row_dimensions[data_row].height = 16
        data_row += 1

    set_border(ws1, 2, data_row - 1, 1, len(headers1))

    # -- Feuille 2 : Parquet ---------------------------------------------------

    ws2 = wb.create_sheet("Parquet")
    ws2.sheet_view.showGridLines = False
    ws2.freeze_panes = "A3"

    ws2.merge_cells("A1:D1")
    style_title(ws2["A1"])
    ws2["A1"] = f"resultats_territorialises.parquet - dictionnaire ({TODAY})"
    ws2.row_dimensions[1].height = 28

    headers2 = ["Colonne", "Type", "Description", "Exemple"]
    col_widths2 = [22, 10, 70, 20]
    for col, (h, w) in enumerate(zip(headers2, col_widths2), 1):
        cell = ws2.cell(row=2, column=col, value=h)
        style_header(cell)
        ws2.column_dimensions[get_column_letter(col)].width = w
    ws2.row_dimensions[2].height = 22

    row = 3
    for i, col_name in enumerate(TERRITORIALISE_COLUMNS):
        dtype, desc, example = COLONNES_META[col_name]
        bg = GRIS_CLAIR if i % 2 == 0 else BLANC
        for c, val in enumerate([col_name, dtype, desc, example], 1):
            style_cell(ws2.cell(row=row, column=c, value=val), bg=bg)
        row += 1

    set_border(ws2, 2, row - 1, 1, len(headers2))

    # Legende qualite, meme feuille, quelques lignes plus bas
    row += 2
    ws2.merge_cells(f"A{row}:D{row}")
    cell = ws2.cell(row=row, column=1, value="  LEGENDE QUALITE")
    style_section(cell, bg=VERT_SECTION)
    ws2.row_dimensions[row].height = 18
    row += 1

    legend_header_row = row
    for c, h in enumerate(["Valeur", "", "Signification", ""], 1):
        if h:
            style_header(ws2.cell(row=row, column=c, value=h))
        else:
            ws2.cell(row=row, column=c, value="")
    ws2.row_dimensions[row].height = 22
    row += 1

    for i, (valeur, signification) in enumerate(QUALITE_LEGENDE.items()):
        bg = VERT_CLAIR if i % 2 == 0 else BLANC
        ws2.merge_cells(f"C{row}:D{row}")
        style_cell(ws2.cell(row=row, column=1, value=valeur), bg=bg, bold=True)
        ws2.cell(row=row, column=2, value="")
        style_cell(ws2.cell(row=row, column=3, value=signification), bg=bg)
        row += 1

    set_border(ws2, legend_header_row, row - 1, 1, len(headers2))

    # --- Sauvegarde ------------------------------------------------------------

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _save_workbook(wb, OUTPUT_PATH):
        return
    print(
        f"Fichier genere : {OUTPUT_PATH}  "
        f"({len(TERRITORIALISE_COLUMNS)} colonnes, {len(hexagone)} sous-secteurs "
        f"hexagone / {len(drom)} DROM)"
    )


if __name__ == "__main__":
    main()
