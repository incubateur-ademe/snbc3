"""
api/excel_export.py
====================
Génère, à la demande, une copie du classeur maître "Trajectoire GES de
référence" pointée sur un seul territoire (+ overrides éventuels), sans
jamais relancer `update_satool` (xlwings/COM) - uniquement des écritures de
cellules ponctuelles avec openpyxl sur une copie du classeur déjà publié en
batch. Les formules du classeur (onglet CALCUL, blocs ②/③) sont préservées
telles quelles : Excel les recalcule à l'ouverture.

Structure de l'onglet "Territoire" (construite par
`standalone_tool/update_standalone_tool.py::_build_caract_territoire`,
constantes HC/HN/HL), vérifiée sur un classeur généré par
tests/test_classeur.py::test_generation_complete :
  - Bloc ① sélection : lignes 7..N, colonne A = libellé choisi (dropdown),
    colonne B = code (formule), colonnes masquées X=code / Y=nom / Z=libellé
    désambiguïsé sur les mêmes lignes.
  - Bloc ② pivots : lignes 7..N, colonne D = code_pivot, H = Correction
    manuelle (éditable), I = Retenu (formule).
  - Bloc ③ recalage : lignes 7..N, colonne N = code_recalage, R = Correction
    manuelle (éditable), S = Retenu (formule).
"""

import io
import logging
from pathlib import Path

import openpyxl

log = logging.getLogger(__name__)

SHEET_TERRITOIRE = "Territoire"
ROW_DEBUT = 7
COL_SELECTION = 1        # A
COL_CODE_SELECTION = 24     # X (masquée)
COL_LIBELLE_SELECTION = 26  # Z (masquée)
COL_PIVOT_CODE = 4          # D
COL_PIVOT_CORRECTION = 8    # H
COL_RECALAGE_CODE = 14       # N
COL_RECALAGE_CORRECTION = 18  # R


class ExcelExportError(RuntimeError):
    """Levée quand le territoire est introuvable dans le classeur maître."""


def _find_row_by_code(ws, column: int, code: str) -> int | None:
    r = ROW_DEBUT
    while True:
        val = ws.cell(row=r, column=column).value
        if val is None:
            return None
        if str(val) == str(code):
            return r
        r += 1


def select_territoire(ws, code_epci: str) -> None:
    """Isole une sélection à un seul territoire dans le bloc ① (colonne A)."""
    codes_labels = []
    r = ROW_DEBUT
    while True:
        code_cell = ws.cell(row=r, column=COL_CODE_SELECTION).value
        if code_cell is None:
            break
        codes_labels.append((str(code_cell), ws.cell(row=r, column=COL_LIBELLE_SELECTION).value))
        r += 1
    last_row = r - 1

    label = next((lbl for code, lbl in codes_labels if code == str(code_epci)), None)
    if label is None:
        raise ExcelExportError(f"territoire {code_epci!r} introuvable dans le classeur maître")

    for rr in range(ROW_DEBUT, last_row + 1):
        ws.cell(row=rr, column=COL_SELECTION).value = None
    ws.cell(row=ROW_DEBUT, column=COL_SELECTION).value = label


def apply_overrides_to_sheet(ws, overrides: list[dict]) -> list[str]:
    """
    Écrit chaque override dans la cellule "Correction manuelle" du bloc ②
    (pivots) ou ③ (recalage) correspondant à son `code_indicateur`.

    Retourne la liste des `code_indicateur` sans ligne correspondante dans le
    classeur (indicateur valide au sens du catalogue mais non utilisé comme
    pivot/recalage - l'override JSON l'aurait accepté, l'export Excel ne
    peut matériellement pas l'exposer).
    """
    non_trouves = []
    for o in overrides:
        code, valeur = o["code_indicateur"], o["valeur"]
        row = _find_row_by_code(ws, COL_PIVOT_CODE, code)
        if row is not None:
            ws.cell(row=row, column=COL_PIVOT_CORRECTION).value = valeur
            continue
        row = _find_row_by_code(ws, COL_RECALAGE_CODE, code)
        if row is not None:
            ws.cell(row=row, column=COL_RECALAGE_CORRECTION).value = valeur
            continue
        non_trouves.append(code)
    return non_trouves


def load_master(path: Path) -> openpyxl.Workbook:
    """
    Ouvre le classeur maître pour réécriture. `rich_text=True` conserve la
    mise en forme partielle des cellules (passages en gras des consignes de
    `Notice`/`INTRODUCTION`), sinon aplatie. Les images ne sont conservées
    qu'avec pillow installé. La taille des notes n'est pas relue par
    openpyxl : elle est recalculée d'après leur texte. Les formes et le style
    des graphiques, perdus au chargement, sont rétablis à l'enregistrement
    (`save_master`).
    """
    from standalone_tool.classeur import ajuster_notes
    wb = openpyxl.load_workbook(path, rich_text=True)
    ajuster_notes(wb)
    return wb


def save_master(wb: openpyxl.Workbook, master_path: Path) -> bytes:
    """Octets du classeur, formes et graphiques rétablis d'après le classeur
    maître (`standalone_tool.classeur.finaliser`)."""
    from standalone_tool.classeur import finaliser
    buf = io.BytesIO()
    wb.save(buf)
    return finaliser(buf.getvalue(), Path(master_path).read_bytes())


def generate_territoire_workbook(
    master_path: Path, code_epci: str, overrides: list[dict],
) -> tuple[bytes, list[str]]:
    """
    Charge une copie du classeur maître, sélectionne `code_epci` et applique
    `overrides`, retourne (octets du classeur, codes d'override non trouvés).
    """
    wb = load_master(master_path)
    ws = wb[SHEET_TERRITOIRE]

    select_territoire(ws, code_epci)
    non_trouves = apply_overrides_to_sheet(ws, overrides)
    if non_trouves:
        log.warning(f"Overrides sans ligne pivot/recalage dans le classeur : {non_trouves}")

    return save_master(wb, master_path), non_trouves
