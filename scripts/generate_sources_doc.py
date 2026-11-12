"""
Genere le fichier "Sources donnees Traj GES.xlsx" a partir des
metadonnees SOURCE_META declarees dans chaque module sources/*.py.
"""

import sys
from datetime import date
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# Permettre l'import de sources/ depuis scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sources import get_all_sources_meta

OUTPUT_PATH = Path("docs") / "Sources donnees.xlsx"

# --- Palette couleurs --------------------------------------------------------

BLEU_TITRE   = "1F3864"
BLEU_EN_TETE = "2E75B6"
GRIS_CLAIR   = "D9E2F3"
BLANC        = "FFFFFF"

THEME_COLORS = {
    "Territoire":                ("2E75B6", "D9E2F3"),
    "Demographie":               ("375623", "E2EFDA"),
    "Demographie / Logement / Economie": ("375623", "E2EFDA"),
    "Demographie (projections)": ("375623", "E2EFDA"),
    "Logement":                  ("7030A0", "EAE0F5"),
    "Economie":                  ("833C00", "FCE4D6"),
    "Industrie":                 ("BF8F00", "FFF2CC"),
    "Agriculture":               ("375623", "E2EFDA"),
    "Climat":                    ("1F3864", "D9E2F3"),
    "Energie / Climat":          ("1F3864", "D9E2F3"),
    "Mobilite":                  ("C00000", "FCE4D6"),
    "UTCATF":                    ("375623", "E2EFDA"),
}


# --- Helpers styles ----------------------------------------------------------

def style_header(cell, bg=BLEU_EN_TETE, fg=BLANC, bold=True, size=10):
    cell.font = Font(bold=bold, color=fg, size=size, name="Calibri")
    cell.fill = PatternFill("solid", fgColor=bg)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

def style_title(cell, bg=BLEU_TITRE, fg=BLANC, size=13):
    cell.font = Font(bold=True, color=fg, size=size, name="Calibri")
    cell.fill = PatternFill("solid", fgColor=bg)
    cell.alignment = Alignment(horizontal="center", vertical="center")

def style_section(cell, bg, fg=BLANC):
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
    for row in ws.iter_rows(min_row=min_row, max_row=max_row,
                            min_col=min_col, max_col=max_col):
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


# --- Generation ---------------------------------------------------------------

def main():
    ALL_SOURCES = get_all_sources_meta()
    ALL_SOURCES.sort(key=lambda s: s.get("id", ""))

    TODAY = date.today().isoformat()

    wb = openpyxl.Workbook()

    # -- Feuille 1 : Sources --------------------------------------------------

    ws1 = wb.active
    ws1.title = "Sources"
    ws1.sheet_view.showGridLines = False
    ws1.freeze_panes = "A3"

    ws1.merge_cells("A1:L1")
    style_title(ws1["A1"], size=13)
    ws1["A1"] = f"Sources de donnees - Traj GES  (mise a jour {TODAY})"
    ws1.row_dimensions[1].height = 28

    headers1 = [
        "ID source", "Nom de la source", "Module Python",
        "Description", "URL / Origine", "Niveau geographique",
        "Annees disponibles", "Format", "Acces", "Frequence MaJ",
        "Licence", "Theme", "Notes methodologiques",
    ]
    col_widths1 = [14, 36, 26, 52, 52, 22, 22, 18, 18, 15, 14, 18, 60]

    for col, (h, w) in enumerate(zip(headers1, col_widths1), 1):
        cell = ws1.cell(row=2, column=col, value=h)
        style_header(cell)
        ws1.column_dimensions[get_column_letter(col)].width = w

    ws1.row_dimensions[2].height = 30

    for i, src in enumerate(ALL_SOURCES):
        row = i + 3
        bg = GRIS_CLAIR if i % 2 == 0 else BLANC
        vals = [
            src.get("id", ""),
            src.get("nom", ""),
            src.get("module", ""),
            src.get("description", ""),
            src.get("urls", ""),
            src.get("niveau_geo", ""),
            src.get("annees", ""),
            src.get("format", ""),
            src.get("acces", ""),
            src.get("frequence_maj", ""),
            src.get("licence", ""),
            src.get("theme", ""),
            src.get("notes", ""),
        ]
        for col, val in enumerate(vals, 1):
            cell = ws1.cell(row=row, column=col, value=val)
            style_cell(cell, bg=bg, wrap=True)
        ws1.row_dimensions[row].height = 72

    set_border(ws1, 2, len(ALL_SOURCES) + 2, 1, len(headers1))

    # -- Feuille 2 : Indicateurs ----------------------------------------------

    ws2 = wb.create_sheet("Indicateurs")
    ws2.sheet_view.showGridLines = False
    ws2.freeze_panes = "A3"

    ws2.merge_cells("A1:G1")
    style_title(ws2["A1"], size=13)
    ws2["A1"] = f"Catalogue des indicateurs - Traj GES  (mise a jour {TODAY})"
    ws2.row_dimensions[1].height = 28

    headers2 = [
        "Code indicateur", "Libelle", "Unite", "Source(s)",
        "Theme", "Methode d'agregation", "Annees disponibles",
    ]
    col_widths2 = [22, 46, 14, 18, 26, 28, 24]

    for col, (h, w) in enumerate(zip(headers2, col_widths2), 1):
        cell = ws2.cell(row=2, column=col, value=h)
        style_header(cell)
        ws2.column_dimensions[get_column_letter(col)].width = w

    ws2.row_dimensions[2].height = 26

    all_indicators = []
    for src in ALL_SOURCES:
        theme = src.get("theme", "")
        src_id = src.get("id", "")
        for ind in src.get("indicateurs", []):
            all_indicators.append({
                "code": ind["code"],
                "label": ind["label"],
                "unite": ind["unite"],
                "source": src_id,
                "theme": theme,
                "methode": ind.get("methode_agregation", ""),
                "annees": ind.get("annees", ""),
            })

    current_theme = None
    data_row = 3
    for ind in all_indicators:
        theme = ind["theme"]
        if theme != current_theme:
            current_theme = theme
            tc, _ = THEME_COLORS.get(theme, ("595959", "ECECEC"))
            ws2.merge_cells(f"A{data_row}:G{data_row}")
            cell = ws2.cell(row=data_row, column=1, value=f"  {theme.upper()}")
            style_section(cell, bg=tc)
            ws2.row_dimensions[data_row].height = 18
            data_row += 1

        _, bg = THEME_COLORS.get(theme, ("595959", "ECECEC"))
        vals = [
            ind["code"], ind["label"], ind["unite"],
            ind["source"], ind["theme"], ind["methode"], ind["annees"],
        ]
        for col, val in enumerate(vals, 1):
            cell = ws2.cell(row=data_row, column=col, value=val)
            style_cell(cell, bg=bg if data_row % 2 == 1 else BLANC, wrap=False)
        ws2.row_dimensions[data_row].height = 16
        data_row += 1

    set_border(ws2, 2, data_row - 1, 1, len(headers2))

    # --- Sauvegarde ----------------------------------------------------------

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _save_workbook(wb, OUTPUT_PATH):
        return
    print(f"Fichier genere : {OUTPUT_PATH}  ({len(ALL_SOURCES)} sources, {len(all_indicators)} indicateurs)")


if __name__ == "__main__":
    main()
