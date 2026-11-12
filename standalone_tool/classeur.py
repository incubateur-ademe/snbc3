"""
standalone_tool/classeur.py
===========================
Couche d'accès au classeur pour `update_standalone_tool`, sur openpyxl (sans
Excel, toutes plateformes).

Elle reprend le sous-ensemble de l'API xlwings que le générateur utilisait
(`sheet.range(...).value/.formula/.color/.number_format`, tableaux structurés,
plages nommées, graphiques) avec la même sémantique que sous Excel :

- une chaîne numérique écrite comme valeur devient un nombre (« 74 » -> 74),
  une chaîne vide efface la cellule, NaN aussi ;
- une formule unique affectée à une plage de plusieurs cellules est recopiée
  avec décalage des références relatives (comme `Range.Formula` d'Excel) ;
- une liste à une dimension s'écrit en ligne, ou en colonne avec
  `options(transpose=True)`.

Les graphiques sont décrits par `Graphique` et construits à l'enregistrement,
au format des graphiques créés par Excel (style 2, couleurs automatiques).
Les formes (flèches, zones de texte), qu'openpyxl ne sait pas conserver, sont
recopiées depuis le fichier d'origine après l'enregistrement (`finaliser`).
Le classeur est enregistré sans valeurs calculées : Excel recalcule tout à
l'ouverture (`fullCalcOnLoad`).
"""

from __future__ import annotations

import math
import re
import zipfile
from copy import copy
from pathlib import Path

import openpyxl
from openpyxl.formula.translate import Translator
from openpyxl.styles import Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.worksheet.table import TableColumn

_NUM = re.compile(r"^\s*[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?\s*$")


# ── Valeurs ──────────────────────────────────────────────────────────────────

def _valeur(v):
    """Valeur Python telle qu'Excel la stockerait après une saisie COM."""
    if v is None:
        return None
    try:
        import numpy as np
        if isinstance(v, np.generic):
            v = v.item()
    except ImportError:
        pass
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        import pandas as pd
        if v is pd.NA or v is pd.NaT:
            return None
    except ImportError:
        pass
    if isinstance(v, str):
        if v == "":
            return None
        if not v.startswith("=") and _NUM.match(v):
            f = float(v)
            return int(f) if f.is_integer() and abs(f) < 1e15 and "." not in v and "e" not in v.lower() else f
    return v


# Formule dont la cellule prend le format de la 1re cellule référencée, comme
# Excel à la saisie : référence seule, ou SUM/MAX/MIN/PRODUCT/AVERAGE dont le
# 1er argument est une référence de la même feuille.
_FORMULE_HERITANTE = re.compile(
    r"^=(?:(?:SUM|MAX|MIN|PRODUCT|AVERAGE)\()?\$?([A-Z]{1,3})\$?(\d+)(?![\d(!])")


def _heriter_format(ws, cell):
    v = cell.value
    if not isinstance(v, str) or cell.number_format != "General":
        return
    m = _FORMULE_HERITANTE.match(v)
    if not m:
        return
    from openpyxl.utils import column_index_from_string
    src = ws._cells.get((int(m.group(2)), column_index_from_string(m.group(1))))
    if src is not None and src.number_format != "General":
        cell.number_format = src.number_format


def nom_feuille(nom: str) -> str:
    """Nom d'onglet tel qu'Excel l'écrit dans une référence (guillemets si besoin)."""
    if re.fullmatch(r"[^\W\d][\w.]*", nom) and not re.fullmatch(r"[A-Za-z]{1,3}\d+", nom):
        return nom
    return "'" + nom.replace("'", "''") + "'"


def _rgb(couleur) -> str:
    """(r, g, b) -> 'FFRRGGBB'."""
    r, g, b = couleur
    return f"FF{r:02X}{g:02X}{b:02X}"


def bgr(v: int) -> str:
    """Couleur au format COM (BGR, entier) -> 'FFRRGGBB'."""
    return _rgb((v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF))


# ── Plages ───────────────────────────────────────────────────────────────────

class Plage:
    """Plage rectangulaire d'une feuille (coordonnées 1-indexées)."""

    def __init__(self, feuille: "Feuille", r1, c1, r2=None, c2=None, transpose=False):
        self.feuille = feuille
        self.ws = feuille.ws
        r2 = r1 if r2 is None else r2
        c2 = c1 if c2 is None else c2
        self.r1, self.c1 = min(r1, r2), min(c1, c2)
        self.r2, self.c2 = max(r1, r2), max(c1, c2)
        self._transpose = transpose

    # Repères
    @property
    def row(self):
        return self.r1

    @property
    def column(self):
        return self.c1

    @property
    def shape(self):
        return (self.r2 - self.r1 + 1, self.c2 - self.c1 + 1)

    @property
    def rows(self):
        n = self.shape[0]

        class _R:
            count = n
        return _R()

    @property
    def address(self):
        a = f"{get_column_letter(self.c1)}{self.r1}"
        b = f"{get_column_letter(self.c2)}{self.r2}"
        return a if a == b else f"{a}:{b}"

    @property
    def left(self):
        return self.feuille.gauche_pt(self.c1)

    @property
    def top(self):
        return self.feuille.haut_pt(self.r1)

    def options(self, transpose=False):
        return Plage(self.feuille, self.r1, self.c1, self.r2, self.c2, transpose=transpose)

    def cellules(self):
        for r in range(self.r1, self.r2 + 1):
            for c in range(self.c1, self.c2 + 1):
                yield self.ws.cell(r, c)

    def _lire(self, r, c):
        """Valeur sans créer de cellule vide (une lecture large en créerait des milliers)."""
        cell = self.ws._cells.get((r, c))
        return cell.value if cell is not None else None

    # Lecture / écriture
    @property
    def value(self):
        lignes = [[self._lire(r, c) for c in range(self.c1, self.c2 + 1)]
                  for r in range(self.r1, self.r2 + 1)]
        h, w = self.shape
        if h == 1 and w == 1:
            return lignes[0][0]
        if h == 1:
            return lignes[0]
        if w == 1:
            return [ligne[0] for ligne in lignes]
        return lignes

    @value.setter
    def value(self, v):
        self._ecrire(v)

    @property
    def formula(self):
        v = self._lire(self.r1, self.c1)
        return v if v is not None else ""

    @formula.setter
    def formula(self, v):
        self._ecrire(v)

    def _ecrire(self, v):
        h, w = self.shape
        if isinstance(v, (list, tuple)):
            if v and isinstance(v[0], (list, tuple)):
                matrice = [list(x) for x in v]
            elif self._transpose:
                matrice = [[x] for x in v]
            else:
                matrice = [list(v)]
            if h == 1 and w == 1:
                # Une cellule de départ : la plage s'étend à la taille des données
                for i, ligne in enumerate(matrice):
                    for j, x in enumerate(ligne):
                        self._set(self.r1 + i, self.c1 + j, x)
                return
            # Plage explicite : écriture dans ses limites (recyclage d'une ligne/colonne unique)
            for i in range(h):
                ligne = matrice[i if len(matrice) > 1 else 0]
                for j in range(w):
                    self._set(self.r1 + i, self.c1 + j, ligne[j if len(ligne) > 1 else 0])
            return
        # Scalaire : recopié sur toute la plage, formule décalée comme sous Excel
        x = _valeur(v)
        origine = f"{get_column_letter(self.c1)}{self.r1}"
        for r in range(self.r1, self.r2 + 1):
            for c in range(self.c1, self.c2 + 1):
                cell = self.ws.cell(r, c)
                if isinstance(x, str) and x.startswith("=") and (r, c) != (self.r1, self.c1):
                    dest = f"{get_column_letter(c)}{r}"
                    cell.value = Translator(x, origin=origine).translate_formula(dest)
                else:
                    cell.value = x
                _heriter_format(self.ws, cell)

    def _set(self, r, c, x):
        cell = self.ws.cell(r, c)
        cell.value = _valeur(x)
        _heriter_format(self.ws, cell)

    def clear_contents(self):
        for cell in self.cellules():
            cell.value = None

    # Mise en forme
    @property
    def color(self):
        f = self.ws.cell(self.r1, self.c1).fill
        if f is None or f.fill_type != "solid":
            return None
        h = f.fgColor.rgb[-6:]
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    @color.setter
    def color(self, couleur):
        from openpyxl.styles.colors import Color
        if couleur is None:
            fill = PatternFill()
        elif isinstance(couleur, Color):
            fill = PatternFill("solid", fgColor=copy(couleur))
        else:
            fill = PatternFill("solid", fgColor=_rgb(couleur))
        for cell in self.cellules():
            cell.fill = fill

    @property
    def number_format(self):
        return self.ws.cell(self.r1, self.c1).number_format

    @number_format.setter
    def number_format(self, fmt):
        for cell in self.cellules():
            cell.number_format = fmt

    @property
    def column_width(self):
        return self.feuille.largeur(self.c1)

    @column_width.setter
    def column_width(self, largeur):
        """Largeur telle que stockée dans le fichier (marge comprise), et non
        `ColumnWidth` d'Excel, dont la conversion dépend de la résolution de
        l'écran."""
        for c in range(self.c1, self.c2 + 1):
            self.feuille.colonne(c).width = largeur

    def police(self, gras=None, italique=None, couleur=None):
        """`couleur` : 'FFRRGGBB' (cf. `bgr`)."""
        for cell in self.cellules():
            f = copy(cell.font)
            if gras is not None:
                f.b = gras
            if italique is not None:
                f.i = italique
            if couleur is not None:
                f.color = couleur
            cell.font = f

    def alignement(self, horizontal=None, vertical=None, wrap=None, shrink=None):
        for cell in self.cellules():
            a = copy(cell.alignment)
            if horizontal is not None:
                a.horizontal = horizontal
            if vertical is not None:
                a.vertical = vertical
            if wrap is not None:
                a.wrap_text = wrap
            if shrink is not None:
                a.shrink_to_fit = shrink
            cell.alignment = a

    def bordures(self):
        """Traits fins sur tout le quadrillage (`Borders.Weight = xlThin`)."""
        fin = Side(style="thin")
        for cell in self.cellules():
            cell.border = Border(left=fin, right=fin, top=fin, bottom=fin)

    def fusionner(self):
        for m in list(self.ws.merged_cells.ranges):
            r1, c1, r2, c2 = m.min_row, m.min_col, m.max_row, m.max_col
            if not (r2 < self.r1 or r1 > self.r2 or c2 < self.c1 or c1 > self.c2):
                self.ws.unmerge_cells(str(m))
        self.ws.merge_cells(start_row=self.r1, start_column=self.c1,
                            end_row=self.r2, end_column=self.c2)

    def masquer_colonnes(self, masquer=True):
        for c in range(self.c1, self.c2 + 1):
            self.feuille.colonne(c).hidden = masquer

    def hauteur_ligne(self, h):
        for r in range(self.r1, self.r2 + 1):
            self.ws.row_dimensions[r].height = h

    # Règles
    def mfc(self, formule, fond=None, police=None, gras=None, italique=None, premier=False):
        """Mise en forme conditionnelle sur formule (`formule` sans « = »)."""
        from openpyxl.formatting.rule import Rule
        from openpyxl.styles.differential import DifferentialStyle
        font = None
        if police is not None or gras is not None or italique is not None:
            font = Font(color=police, b=gras, i=italique)
        fill = PatternFill(bgColor=fond) if fond is not None else None
        rule = Rule(type="expression", formula=[formule], stopIfTrue=True,
                    dxf=DifferentialStyle(font=font, fill=fill))
        self.ws.conditional_formatting.add(self.address, rule)
        if premier:
            # Priorité 1, les autres règles de la feuille décalées d'un rang
            for rng in self.ws.conditional_formatting:
                for r in rng.rules:
                    if r is not rule:
                        r.priority += 1
            rule.priority = 1
        return rule

    def supprimer_mfc(self):
        """Retire les règles portant exactement sur cette plage (priorités des
        autres conservées)."""
        from openpyxl.formatting.formatting import ConditionalFormattingList
        neuf = ConditionalFormattingList()
        for rng in self.ws.conditional_formatting:
            if str(rng.sqref) == self.address:
                continue
            for r in rng.rules:
                neuf.add(str(rng.sqref), r)
        self.ws.conditional_formatting = neuf

    def validation_liste(self, source: str):
        """Liste déroulante : `source` = formule de plage (« =$Z$7:$Z$90 ») ou
        valeurs séparées par « ; » (comme `Validation.Add` d'Excel en français)."""
        from openpyxl.worksheet.datavalidation import DataValidation
        if source.startswith("="):
            f1 = source[1:]
        else:
            f1 = '"' + ",".join(s.strip() for s in source.split(";")) + '"'
        dv = DataValidation(type="list", formula1=f1, allow_blank=True,
                            showErrorMessage=True, showInputMessage=True)
        dv.add(self.address)
        self.supprimer_validation()
        self.ws.add_data_validation(dv)
        return dv

    def validation_formule(self, formule, titre, message):
        """Validation personnalisée bloquante (`formule` sans « = »)."""
        from openpyxl.worksheet.datavalidation import DataValidation
        dv = DataValidation(type="custom", formula1=formule, allow_blank=True,
                            showErrorMessage=True, showInputMessage=True,
                            errorTitle=titre, error=message)
        dv.add(self.address)
        self.supprimer_validation()
        self.ws.add_data_validation(dv)
        return dv

    def supprimer_validation(self):
        dvs = self.ws.data_validations
        dvs.dataValidation = [dv for dv in dvs.dataValidation if str(dv.sqref) != self.address]

    def note(self, texte, auteur="Adrien Jacob (nW)"):
        from openpyxl.comments import Comment
        c = Comment(texte, auteur)
        c.width, c.height = taille_note(texte)
        self.ws.cell(self.r1, self.c1).comment = c


def taille_note(texte: str) -> tuple[int, int]:
    """
    Taille (largeur, hauteur en px) d'une note affichant tout son texte, en
    police de note par défaut (Tahoma 9) : openpyxl ne relit pas la taille des
    notes et les réécrit sinon toutes en 144 x 79 px, où un texte long est
    tronqué.
    """
    largeur = 144 if len(texte) <= 120 else 360
    car_par_ligne = (largeur - 12) / 6.5
    lignes = sum(max(1, math.ceil(len(p) / car_par_ligne)) for p in texte.splitlines() or [""])
    return largeur, max(79, 15 * (lignes + 1) + 12)


def ajuster_notes(wb) -> None:
    """Redimensionne toutes les notes du classeur openpyxl selon leur texte."""
    for ws in wb.worksheets:
        for cell in ws._cells.values():
            if cell.comment is not None:
                cell.comment.width, cell.comment.height = taille_note(cell.comment.text or "")


# ── Feuilles ─────────────────────────────────────────────────────────────────

# Largeur maximale d'un chiffre de la police par défaut (Calibri 11), en pixels
_MDW = 7


def _px_colonne(largeur_fichier_: float) -> int:
    return math.trunc(((256 * largeur_fichier_ + math.trunc(128 / _MDW)) / 256) * _MDW)


class Feuille:
    def __init__(self, classeur: "Classeur", ws):
        self.book = classeur
        self.ws = ws
        self.charts = _Graphiques(self)
        self._degrouper_colonnes()

    @property
    def name(self):
        return self.ws.title

    @name.setter
    def name(self, v):
        self.ws.title = v

    def range(self, a, b=None):
        if isinstance(a, str):
            c1, r1, c2, r2 = range_boundaries(a)
            return Plage(self, r1, c1, r2, c2)
        if b is None:
            return Plage(self, a[0], a[1])
        return Plage(self, a[0], a[1], b[0], b[1])

    def __getitem__(self, adresse):
        return self.range(adresse)

    @property
    def tables(self):
        return [Tableau(self, t) for t in self.ws.tables.values()]

    # Colonnes : les groupes <col min max> sont éclatés pour pouvoir régler
    # une colonne isolément sans produire de plages qui se chevauchent.
    def _degrouper_colonnes(self):
        dims = self.ws.column_dimensions
        for key, d in list(dims.items()):
            lo, hi = d.min or 0, d.max or 0
            if lo and hi and hi > lo and hi - lo < 1000:
                for i in range(lo + 1, hi + 1):
                    nd = copy(d)
                    nd.index = get_column_letter(i)
                    nd.min = nd.max = i
                    dims[get_column_letter(i)] = nd
                d.max = lo

    def colonne(self, c):
        lettre = get_column_letter(c)
        dims = self.ws.column_dimensions
        if lettre not in dims:
            # Colonne couverte par une plage large (ex. jusqu'à XFD) : l'en sortir
            for key, d in list(dims.items()):
                if (d.min or 0) <= c <= (d.max or 0) and d.min != d.max:
                    if d.min == c:
                        nd = copy(d)
                        nd.min = nd.max = c
                        d.min = c + 1
                        dims[lettre] = nd
                        dims[get_column_letter(c + 1)] = d
                        del dims[key]
                        d.index = get_column_letter(c + 1)
                        return nd
                    nd = copy(d)
                    nd.min, nd.max = c, c
                    reste = copy(d)
                    reste.min = c + 1
                    d.max = c - 1
                    dims[lettre] = nd
                    if reste.min <= reste.max:
                        reste.index = get_column_letter(c + 1)
                        dims[get_column_letter(c + 1)] = reste
                    return nd
        d = dims[lettre]
        d.min = d.max = c
        return d

    def largeur(self, c):
        """Largeur stockée de la colonne c (None : largeur par défaut)."""
        lettre = get_column_letter(c)
        d = self.ws.column_dimensions.get(lettre)
        if d is None:
            for x in self.ws.column_dimensions.values():
                if (x.min or 0) <= c <= (x.max or 0):
                    d = x
                    break
        if d is not None and d.hidden:
            return 0.0
        if d is not None and d.width:
            return d.width
        fmt = self.ws.sheet_format
        if fmt.defaultColWidth:
            return fmt.defaultColWidth
        base = fmt.baseColWidth or 8
        return math.trunc((base * _MDW + 5) / _MDW * 256) / 256

    def gauche_pt(self, c):
        return sum(_px_colonne(self.largeur(i)) for i in range(1, c)) * 0.75

    def hauteur(self, r):
        d = self.ws.row_dimensions.get(r)
        if d is not None and d.hidden:
            return 0.0
        if d is not None and d.height:
            return d.height
        return self.ws.sheet_format.defaultRowHeight or 15.0

    def haut_pt(self, r):
        return sum(self.hauteur(i) for i in range(1, r))

    def ancre(self, gauche_pt, haut_pt):
        """(col, décalage EMU, ligne, décalage EMU), 0-indexés, d'un point de la feuille."""
        px = round(gauche_pt / 0.75)
        c = 1
        while True:
            w = _px_colonne(self.largeur(c))
            if px < w or c > 16000:
                break
            px -= w
            c += 1
        pt = haut_pt
        r = 1
        while True:
            h = self.hauteur(r)
            if pt < h or r > 1_000_000:
                break
            pt -= h
            r += 1
        return c - 1, px * 9525, r - 1, round(pt * 12700)

    def clear(self):
        """Vide la feuille (contenu, formats, fusions, règles, graphiques),
        sans toucher aux largeurs, hauteurs, volets et couleur d'onglet."""
        from openpyxl.formatting.formatting import ConditionalFormattingList
        from openpyxl.worksheet.datavalidation import DataValidationList
        ws = self.ws
        ws._cells.clear()
        for m in list(ws.merged_cells.ranges):
            ws.merged_cells.remove(m)
        ws.conditional_formatting = ConditionalFormattingList()
        ws.data_validations = DataValidationList()
        ws._charts = []
        ws._hyperlinks = []
        self.charts._liste.clear()

    def reinitialiser_filtre(self):
        """Vide les critères du filtre automatique et réaffiche les lignes
        (`ShowAllData`), sans retirer le filtre lui-même."""
        af = self.ws.auto_filter
        if af.ref:
            af.filterColumn = []
            af.sortState = None
            for d in self.ws.row_dimensions.values():
                d.hidden = False

    def couleur_onglet(self):
        """Couleur d'onglet, à affecter telle quelle à `Plage.color` (une
        couleur de thème reste une couleur de thème, avec sa teinte)."""
        return copy(self.ws.sheet_properties.tabColor)


# ── Tableaux structurés ──────────────────────────────────────────────────────

class Tableau:
    """Tableau structuré (ListObject) : en-têtes, corps, redimensionnement."""

    def __init__(self, feuille: Feuille, table):
        self.feuille = feuille
        self.table = table

    @property
    def name(self):
        return self.table.name

    def _bornes(self):
        c1, r1, c2, r2 = range_boundaries(self.table.ref)
        return r1, c1, r2, c2

    @property
    def range(self):
        r1, c1, r2, c2 = self._bornes()
        return Plage(self.feuille, r1, c1, r2, c2)

    @property
    def header_row_range(self):
        r1, c1, _, c2 = self._bornes()
        return Plage(self.feuille, r1, c1, r1, c2)

    @property
    def data_body_range(self):
        r1, c1, r2, c2 = self._bornes()
        return Plage(self.feuille, r1 + 1, c1, r2, c2) if r2 > r1 else None

    def entetes(self):
        v = self.header_row_range.value
        return v if isinstance(v, list) else [v]

    def redimensionner(self, r2, c2):
        """Nouvelle dernière ligne / colonne ; styles du dernier rang recopiés
        sur les lignes ajoutées (comme Excel quand un tableau s'étend)."""
        r1, c1, ancien_r2, ancien_c2 = self._bornes()
        ws = self.feuille.ws
        if r2 > ancien_r2 and ancien_r2 > r1:
            for r in range(ancien_r2 + 1, r2 + 1):
                for c in range(c1, max(c2, ancien_c2) + 1):
                    src = ws.cell(ancien_r2, c)
                    if src.has_style:
                        ws.cell(r, c)._style = copy(src._style)
        ref = f"{get_column_letter(c1)}{r1}:{get_column_letter(c2)}{r2}"
        self.table.ref = ref
        if self.table.autoFilter is not None:
            self.table.autoFilter.ref = ref
        self._synchroniser_colonnes()

    def _synchroniser_colonnes(self):
        """Noms des colonnes du tableau = cellules d'en-tête (sinon Excel répare)."""
        noms = [("" if h is None else str(h)) for h in self.entetes()]
        anciennes = {c.name: c for c in self.table.tableColumns}
        cols = []
        for i, nom in enumerate(noms, start=1):
            col = anciennes.get(nom)
            if col is None:
                col = TableColumn(id=i, name=nom)
            else:
                col = copy(col)
                col.id = i
            cols.append(col)
        self.table.tableColumns = cols

    def update(self, df):
        """Équivalent de `xlwings.Table.update(df, index=False)`."""
        r1, c1, r2, c2 = self._bornes()
        ws = self.feuille.ws
        n, m = len(df), len(df.columns)
        # Effacement de l'ancien contenu hors de la nouvelle emprise
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                if r > r1 + max(n, 1) or c > c1 + m - 1:
                    ws.cell(r, c).value = None
        for j, h in enumerate(df.columns):
            ws.cell(r1, c1 + j).value = str(h)
        valeurs = df.astype(object).where(df.notna(), None).values.tolist()
        for i, ligne in enumerate(valeurs):
            for j, x in enumerate(ligne):
                ws.cell(r1 + 1 + i, c1 + j).value = _valeur(x)
        self.redimensionner(r1 + max(n, 1), c1 + m - 1)

    def ajouter_colonne(self, nom):
        r1, c1, r2, c2 = self._bornes()
        ws = self.feuille.ws
        ws.cell(r1, c2 + 1).value = nom
        for r in range(r1, r2 + 1):
            src = ws.cell(r, c2)
            if src.has_style:
                ws.cell(r, c2 + 1)._style = copy(src._style)
        self.redimensionner(r2, c2 + 1)

    def supprimer_colonne(self, nom):
        """Supprime une colonne du tableau, cellules du tableau décalées à gauche."""
        r1, c1, r2, c2 = self._bornes()
        ws = self.feuille.ws
        j = c1 + self.entetes().index(nom)
        for r in range(r1, r2 + 1):
            for c in range(j, c2):
                src, dst = ws.cell(r, c + 1), ws.cell(r, c)
                dst.value = src.value
                dst._style = copy(src._style)
            ws.cell(r, c2).value = None
        self.redimensionner(r2, c2 - 1)

    def supprimer_lignes(self, indices):
        """Supprime des lignes de données (indices 1-based dans le corps),
        cellules du tableau remontées."""
        r1, c1, r2, c2 = self._bornes()
        ws = self.feuille.ws
        a_suppr = {r1 + i for i in indices}
        gardees = [r for r in range(r1 + 1, r2 + 1) if r not in a_suppr]
        contenu = [[(ws.cell(r, c).value, copy(ws.cell(r, c)._style)) for c in range(c1, c2 + 1)]
                   for r in gardees]
        for k, ligne in enumerate(contenu):
            for j, (v, st) in enumerate(ligne):
                cell = ws.cell(r1 + 1 + k, c1 + j)
                cell.value = v
                cell._style = st
        nouveau_r2 = r1 + max(len(gardees), 1)
        for r in range(r1 + 1 + len(gardees), r2 + 1):
            for c in range(c1, c2 + 1):
                ws.cell(r, c).value = None
        self.redimensionner(nouveau_r2, c2)

    def ajouter_ligne(self) -> int:
        """Ajoute une ligne en fin de tableau, renvoie son numéro de ligne."""
        r1, c1, r2, c2 = self._bornes()
        self.redimensionner(r2 + 1, c2)
        return r2 + 1


# ── Plages nommées ───────────────────────────────────────────────────────────

class _Nom:
    def __init__(self, classeur, nom):
        self.classeur, self.nom = classeur, nom

    @property
    def refers_to_range(self):
        dn = self.classeur.wb.defined_names[self.nom]
        onglet, coord = next(iter(dn.destinations))
        return self.classeur.sheets[onglet].range(coord.replace("$", ""))

    def delete(self):
        del self.classeur.wb.defined_names[self.nom]


class _Noms:
    def __init__(self, classeur):
        self.classeur = classeur

    def __getitem__(self, nom):
        if nom not in self.classeur.wb.defined_names:
            raise KeyError(nom)
        return _Nom(self.classeur, nom)

    def add(self, nom, refers_to):
        from openpyxl.workbook.defined_name import DefinedName
        ref = refers_to.lstrip("=")
        m = re.match(r"^'([^']+)'!(.*)$", ref)
        if m:
            ref = f"{nom_feuille(m.group(1))}!{m.group(2)}"
        self.classeur.wb.defined_names[nom] = DefinedName(nom, attr_text=ref)


# ── Classeur ─────────────────────────────────────────────────────────────────

class _Feuilles:
    def __init__(self, classeur):
        self.classeur = classeur
        self._cache = {}

    def _f(self, ws):
        if id(ws) not in self._cache:
            self._cache[id(ws)] = Feuille(self.classeur, ws)
        return self._cache[id(ws)]

    def __iter__(self):
        return (self._f(ws) for ws in self.classeur.wb.worksheets)

    def __getitem__(self, nom):
        return self._f(self.classeur.wb[nom])

    def add(self, nom, after=None):
        wb = self.classeur.wb
        idx = wb.worksheets.index(after.ws) + 1 if after is not None else None
        return self._f(wb.create_sheet(nom, idx))


class Classeur:
    def __init__(self, path: Path):
        self.path = Path(path)
        # Octets d'origine : formes à recopier après l'enregistrement (cf. `finaliser`)
        self._source = self.path.read_bytes()
        self.wb = openpyxl.load_workbook(self.path, rich_text=True)
        self.sheets = _Feuilles(self)
        self.names = _Noms(self)
        self._accents = _accents_theme(self.wb)

    def couleur_theme(self, index):
        """Couleur de thème (index de `tabColor`/`color theme=`) en (r, g, b)."""
        return self._accents.get(index)

    def save(self, path: Path | None = None):
        path = Path(path or self.path)
        for feuille in self.sheets:
            feuille.charts.construire()
        self.wb.calculation.fullCalcOnLoad = True
        ajuster_notes(self.wb)
        import io
        buf = io.BytesIO()
        self.wb.save(buf)
        path.write_bytes(finaliser(buf.getvalue(), self._source))


def _accents_theme(wb) -> dict[int, tuple]:
    """Couleurs du thème par index Office (0 lt1, 1 dk1, 2 lt2, 3 dk2, 4-9 accents)."""
    xml = wb.loaded_theme
    if not xml:
        return {}
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8")
    m = re.search(r"<a:clrScheme.*?</a:clrScheme>", xml, re.S)
    if not m:
        return {}
    bloc = m.group(0)
    noms = ["dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4",
            "accent5", "accent6", "hlink", "folHlink"]
    vals = {}
    for nom in noms:
        mm = re.search(rf"<a:{nom}>(.*?)</a:{nom}>", bloc, re.S)
        if not mm:
            continue
        c = re.search(r'(?:srgbClr val|lastClr)="([0-9A-Fa-f]{6})"', mm.group(1))
        if c:
            vals[nom] = tuple(int(c.group(1)[i:i + 2], 16) for i in (0, 2, 4))
    ordre = ["lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3", "accent4",
             "accent5", "accent6", "hlink", "folHlink"]
    return {i: vals[n] for i, n in enumerate(ordre) if n in vals}


# ── Graphiques ───────────────────────────────────────────────────────────────

def couleur_auto(i: int, n: int, accents: list[tuple]) -> str:
    """
    Couleur automatique d'Excel (style 2) de la série d'indice `i` dans un
    graphique de `n` séries : accent i % 6, assombri ou éclairci selon le rang
    i // 6, en RGB linéaire, d'un facteur 1 - 0,7 |p| où p est réparti
    régulièrement sur ]-1, 1[ entre n // 6 + 1 rangs. Relevé sur Excel (écart
    d'au plus une unité par canal, arrondi).
    """
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def srgb(x):
        v = x * 12.92 if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055
        return max(0, min(255, round(v * 255)))

    t = n // 6 + 1
    p = -1 + 2 * (i // 6 + 1) / (t + 1)
    g = 1 - 0.7 * abs(p)
    base = [lin(x) for x in accents[i % 6]]
    if p < 0:
        c = [x * g for x in base]
    elif p > 0:
        c = [1 - (1 - x) * g for x in base]
    else:
        c = base
    return "".join(f"{srgb(x):02X}" for x in c)


class Graphique:
    """
    Description d'un graphique, construite au format de ceux qu'Excel crée par
    `Charts.Add` + `SetSourceData` : plage source dont la 1re ligne porte les
    catégories (années) et la 1re colonne les noms de séries, une série par
    ligne.

    `series[k]` (k = rang 0-based dans la plage source) accepte : `type`
    ("line"), `axe` (2 = secondaire), `remplissage` / `trait` ('RRGGBB' ou
    "none"), `sans_marqueur`. `legende_supprimee` : positions d'entrées de
    légende à retirer.
    """

    def __init__(self, feuille, left, top, width, height):
        self.feuille = feuille
        self.left, self.top, self.width, self.height = left, top, width, height
        self.source: Plage | None = None
        self.chart_type = "line"
        self.titre = None
        self.legende = None             # None, "r" ou "b"
        self.ymin = self.ymax = None
        self.series: dict[int, dict] = {}
        self.etiquettes_x_bas = False
        self.axe2_masque = False
        self.legende_supprimee: list[int] = []

    def set_source_data(self, plage):
        self.source = plage

    @property
    def n_series(self):
        return self.source.shape[0] - 1 if self.source is not None else 0

    def delete(self):
        self.feuille.charts._liste.remove(self)

    def construire(self):
        from openpyxl.chart import AreaChart, LineChart, Reference
        from openpyxl.chart.axis import ChartLines
        from openpyxl.chart.data_source import (
            AxDataSource, NumData, NumDataSource, NumRef, StrData, StrRef,
        )
        from openpyxl.chart.layout import Layout
        from openpyxl.chart.legend import Legend, LegendEntry
        from openpyxl.chart.marker import Marker
        from openpyxl.chart.series import SeriesLabel
        from openpyxl.chart.shapes import GraphicalProperties
        from openpyxl.chart.text import RichText
        from openpyxl.chart.title import Title
        from openpyxl.drawing.line import LineProperties
        from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
        from openpyxl.drawing.xdr import XDRPositiveSize2D
        from openpyxl.utils.units import pixels_to_EMU  # noqa: F401

        ws = self.feuille.ws
        src = self.source
        nom_ws = ws.title
        accents = [self.feuille.book.couleur_theme(4 + k) or (0, 0, 0) for k in range(6)]
        n = self.n_series

        def ref(r1, c1, r2, c2):
            a = f"${get_column_letter(c1)}${r1}"
            b = f"${get_column_letter(c2)}${r2}"
            return f"{nom_feuille(nom_ws)}!{a}" + ("" if a == b else f":{b}")

        cats = ref(src.r1, src.c1 + 1, src.r1, src.c2)
        fmt_val = ws.cell(src.r1 + 1, src.c1 + 1).number_format if n else "General"

        def serie(k, kind):
            r = src.r1 + 1 + k
            s = _serie_fixe(k)
            # Caches vides (nombre de points seul) : le classeur n'a pas de
            # valeurs calculées, mais sans cache Excel ignore à l'ouverture
            # les entrées de légende supprimées (séries encore inconnues).
            npts = src.c2 - src.c1
            s.tx = SeriesLabel(strRef=StrRef(f=ref(r, src.c1, r, src.c1),
                                             strCache=StrData(ptCount=1)))
            s.cat = AxDataSource(numRef=NumRef(
                f=cats, numCache=NumData(formatCode="General", ptCount=npts)))
            s.val = NumDataSource(numRef=NumRef(
                f=ref(r, src.c1 + 1, r, src.c2),
                numCache=NumData(formatCode=fmt_val, ptCount=npts)))
            opt = self.series.get(k, {})
            gp = None
            if "remplissage" in opt or "trait" in opt:
                gp = GraphicalProperties()
                if opt.get("remplissage") == "none":
                    gp.noFill = True
                elif opt.get("remplissage"):
                    gp.solidFill = opt["remplissage"]
                if opt.get("trait") == "none":
                    gp.line = LineProperties(noFill=True)
                elif opt.get("trait"):
                    gp.line = LineProperties(solidFill=opt["trait"])
            s.spPr = gp
            if kind == "line":
                s.marker = Marker(symbol="none") if opt.get("sans_marqueur") else None
                s.smooth = False
            else:
                s.marker = None
                s.smooth = None
            return s

        # Répartition des séries par groupe (type, axe), dans l'ordre d'Excel :
        # aires primaires, aires secondaires, lignes primaires, lignes secondaires.
        base_kind = "area" if self.chart_type == "area_stacked" else "line"
        groupes = {}
        for k in range(n):
            opt = self.series.get(k, {})
            kind = "line" if opt.get("type") == "line" else base_kind
            groupes.setdefault((kind, opt.get("axe", 1)), []).append(k)

        def nouveau(kind):
            if kind == "area":
                c = AreaChart(grouping="stacked")
            else:
                c = LineChart(grouping="standard")
                c.marker = True
                c.smooth = False
            c.varyColors = False
            return c

        ordre = [("area", 1), ("area", 2), ("line", 1), ("line", 2)]
        presents = [g for g in ordre if g in groupes]
        principal = None
        secondaires = []
        for g in presents:
            c = nouveau(g[0])
            c.series = [serie(k, g[0]) for k in groupes[g]]
            if principal is None and g[1] == 1:
                principal = c
            else:
                secondaires.append((g, c))
        if principal is None:           # tout sur l'axe secondaire : cas non utilisé
            (g, principal), secondaires = secondaires[0], secondaires[1:]

        def axes(c, secondaire):
            c.x_axis.axId = 500 if secondaire else 10
            c.y_axis.axId = 600 if secondaire else 100
            c.x_axis.crossAx = c.y_axis.axId
            c.y_axis.crossAx = c.x_axis.axId
            x, y = c.x_axis, c.y_axis
            x.delete = bool(secondaire)
            x.axPos = "b"
            x.numFmt = None
            x.number_format = "General"
            x.numFmt.sourceLinked = True
            x.majorTickMark = "out"
            x.minorTickMark = "none"
            x.tickLblPos = "low" if (self.etiquettes_x_bas and not secondaire) else "nextTo"
            x.crosses = None if secondaire else "autoZero"
            x.auto = True
            x.lblAlgn = "ctr"
            x.lblOffset = 100
            x.noMultiLvlLbl = False
            x.majorGridlines = None
            x.title = None
            y.delete = False
            y.axPos = "r" if secondaire else "l"
            y.majorGridlines = None if secondaire else ChartLines()
            y.number_format = fmt_val
            y.numFmt.sourceLinked = True
            y.majorTickMark = "none" if secondaire else "out"
            y.minorTickMark = "none"
            y.tickLblPos = "none" if secondaire else "nextTo"
            y.crosses = "max" if secondaire else "autoZero"
            has_line = any(g[0] == "line" for g in presents)
            y.crossBetween = "between" if (has_line or base_kind == "line") else "midCat"
            y.title = None
            if secondaire and self.axe2_masque:
                y.spPr = GraphicalProperties(noFill=True, ln=LineProperties(noFill=True))
            if not secondaire:
                y.scaling.min = self.ymin
                y.scaling.max = self.ymax

        axes(principal, False)
        for g, c in secondaires:
            axes(c, g[1] == 2)
            if g[1] == 1:
                c.x_axis, c.y_axis = principal.x_axis, principal.y_axis
            principal += c
        # Un seul jeu d'axes secondaires partagé entre groupes secondaires
        sec = [c for g, c in secondaires if g[1] == 2]
        for c in sec[1:]:
            c.x_axis, c.y_axis = sec[0].x_axis, sec[0].y_axis

        ch = principal
        ch.style = 2
        ch.roundedCorners = False
        ch.visible_cells_only = False
        ch.display_blanks = "gap"
        if self.titre:
            from openpyxl.drawing.text import CharacterProperties
            ch.title = self.titre
            ch.title.overlay = False
            for para in ch.title.tx.rich.p:
                for run in para.r or []:
                    run.rPr = CharacterProperties(lang="fr-FR")
        if self.legende:
            ch.legend = Legend(legendPos=self.legende)
            ch.legend.overlay = False
            ch.legend.legendEntry = [LegendEntry(idx=i, delete=True)
                                     for i in sorted(self.legende_supprimee)]
        else:
            ch.legend = None
        ch.layout = None
        del Layout, RichText, Title, Reference

        cx = round(self.width * 12700)
        cy = round(self.height * 12700)
        col, colOff, row, rowOff = self.feuille.ancre(self.left, self.top)
        ch.anchor = OneCellAnchor(
            _from=AnchorMarker(col=col, colOff=colOff, row=row, rowOff=rowOff),
            ext=XDRPositiveSize2D(cx, cy),
        )
        ws.add_chart(ch)


def _serie_fixe(idx):
    """Série dont l'indice (couleur automatique, légende) n'est pas renuméroté
    par openpyxl : Excel numérote dans l'ordre de la plage source, quel que
    soit le groupe (aires, lignes, axe secondaire) de la série."""
    from openpyxl.chart.series import Series
    s = Series(idx=idx, order=idx)
    s.to_tree = lambda tagname=None, idx=None: Series.to_tree(s, tagname, None)
    return s


class _Graphiques:
    def __init__(self, feuille):
        self.feuille = feuille
        self._liste: list[Graphique] = []

    def __iter__(self):
        return iter(list(self._liste))

    def add(self, left=0, top=0, width=355, height=211):
        g = Graphique(self.feuille, left, top, width, height)
        self._liste.append(g)
        return g

    def construire(self):
        for g in self._liste:
            g.construire()


_EXT_NA = (
    '<extLst><ext uri="{56B9EC1D-385E-4148-901F-78D8002777C0}" '
    'xmlns:c16r3="http://schemas.microsoft.com/office/drawing/2017/03/chart">'
    '<c16r3:dataDisplayOptions16><c16r3:dispNaAsBlank val="1"/>'
    '</c16r3:dataDisplayOptions16></ext></extLst>'
)


_STYLE_C14 = (
    '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
    '<mc:Choice Requires="c14" xmlns:c14="http://schemas.microsoft.com/office/drawing/2007/8/2/chart">'
    '<c14:style val="102"/></mc:Choice><mc:Fallback><style val="2"/></mc:Fallback>'
    '</mc:AlternateContent>'
)


def _patcher_graphique(x: str) -> str:
    """
    Corrige un graphique tel qu'openpyxl l'écrit (construit ici, ou relu dans
    un classeur puis réécrit) pour qu'Excel l'affiche comme les siens.
    """
    # « Afficher #N/A comme une cellule vide » (défaut des graphiques créés par
    # Excel) : les séries observée et planifiée utilisent NA() pour les années
    # sans valeur, qui doivent rester des trous et non être reliées.
    if "dispNaAsBlank" not in x:
        pos = x.rfind("</chart>")
        if pos >= 0:
            x = x[:pos] + _EXT_NA + x[pos:]
    # Style de graphique d'Excel 2010+ (102), avec le style 2 en repli : seul
    # le style 2 donne le rendu Excel 2007 (quadrillage noir, titre réduit).
    # openpyxl ne relit pas ce bloc : il est remis s'il manque.
    if "c14:style" not in x:
        if '<style val="2" />' in x:
            x = x.replace('<style val="2" />', _STYLE_C14, 1)
        else:
            x = x.replace("<chart>", _STYLE_C14 + "<chart>", 1)
    # Titre posé : pas de titre automatique (comme Excel)
    if "autoTitleDeleted" not in x:
        x = x.replace("</title><plotArea>", '</title><autoTitleDeleted val="0" /><plotArea>', 1)
    # Axe secondaire masqué : openpyxl n'écrit pas tickLblPos="none" (valeur
    # confondue avec l'absence), Excel afficherait alors les étiquettes.
    x = re.sub(
        r'(<valAx><axId val="600" />(?:(?!</valAx>).)*?<minorTickMark val="none" />)'
        r"(?!<tickLblPos)",
        r'\1<tickLblPos val="none" />', x)
    return x


_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_DESSIN = _NS_R + "/drawing"
_CT_DESSIN = "application/vnd.openxmlformats-officedocument.drawing+xml"
_FORMES = re.compile(r"<xdr:(?:sp|cxnSp|grpSp)[ >]")


def _chemin_relatif(base: str, cible: str) -> str:
    """Résout la cible d'une relation (relative au dossier de `base`)."""
    if cible.startswith("/"):
        return cible[1:]
    parts = base.rsplit("/", 1)[0].split("/")
    for seg in cible.split("/"):
        if seg == "..":
            parts.pop()
        elif seg != ".":
            parts.append(seg)
    return "/".join(parts)


def _rels_de(parts: dict, chemin: str) -> str:
    d, f = chemin.rsplit("/", 1)
    return f"{d}/_rels/{f}.rels"


def _relations(parts: dict, chemin: str) -> list[tuple[str, str, str]]:
    """[(Id, Type, chemin cible)] des relations d'une partie."""
    xml = parts.get(_rels_de(parts, chemin))
    if xml is None:
        return []
    out = []
    for m in re.finditer(r"<Relationship\b([^>]*)/?>", xml.decode("utf-8")):
        at = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        out.append((at.get("Id"), at.get("Type"), _chemin_relatif(chemin, at.get("Target", ""))))
    return out


def _onglets(parts: dict) -> dict[str, str]:
    """{nom d'onglet: chemin de sa partie XML}."""
    from html import unescape
    wbx = parts["xl/workbook.xml"].decode("utf-8")
    cibles = {i: c for i, _, c in _relations(parts, "xl/workbook.xml")}
    out = {}
    for m in re.finditer(r"<(?:\w+:)?sheet\b([^>]*)/?>", wbx):
        at = m.group(1)
        nom = re.search(r'\bname="([^"]*)"', at)
        rid = re.search(r'\br:id="([^"]*)"', at) or re.search(r'\bid="([^"]*)"', at)
        if nom and rid and rid.group(1) in cibles:
            out[unescape(nom.group(1))] = cibles[rid.group(1)]
    return out


def _recopier_formes(parts: dict, source: dict) -> list[str]:
    """
    Recopie depuis `source` les dessins portant des formes (flèches, zones de
    texte) qu'openpyxl a abandonnés au chargement, pour les onglets qui n'ont
    plus de dessin. Les dessins de source liés à d'autres parties (images) ne
    sont pas recopiés : openpyxl conserve les images.
    """
    recopies = []
    dest_onglets = _onglets(parts)
    for nom, feuille_src in _onglets(source).items():
        feuille_dst = dest_onglets.get(nom)
        if feuille_dst is None:
            continue
        dessins = [c for _, t, c in _relations(source, feuille_src) if t == _REL_DESSIN]
        if not dessins or dessins[0] not in source:
            continue
        dessin = source[dessins[0]]
        if not _FORMES.search(dessin.decode("utf-8")) or _rels_de(source, dessins[0]) in source:
            continue
        if any(t == _REL_DESSIN for _, t, _ in _relations(parts, feuille_dst)):
            continue                      # dessin déjà présent : pas de fusion
        n = 1 + max([int(m.group(1)) for k in parts
                     if (m := re.match(r"xl/drawings/drawing(\d+)\.xml$", k))] or [0])
        chemin = f"xl/drawings/drawing{n}.xml"
        parts[chemin] = dessin
        # Relation de la feuille vers le dessin
        rels = _rels_de(parts, feuille_dst)
        xml = parts.get(rels, b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                              b'<Relationships xmlns="http://schemas.openxmlformats.org/'
                              b'package/2006/relationships"></Relationships>').decode("utf-8")
        rid = "rIdFormes"
        while f'Id="{rid}"' in xml:
            rid += "x"
        xml = xml.replace("</Relationships>",
                          f'<Relationship Id="{rid}" Type="{_REL_DESSIN}" '
                          f'Target="/{chemin}"/></Relationships>')
        parts[rels] = xml.encode("utf-8")
        # Élément <drawing> à sa place dans la feuille (avant legacyDrawing,
        # tableParts, extLst...)
        f = parts[feuille_dst].decode("utf-8")
        racine = re.search(r"<(?:\w+:)?worksheet\b[^>]*>", f)
        if racine and "xmlns:r=" not in racine.group(0):
            f = f.replace(racine.group(0),
                          racine.group(0)[:-1] + f' xmlns:r="{_NS_R}">', 1)
        el = f'<drawing r:id="{rid}"/>'
        m = re.search(r"<(?:\w+:)?(legacyDrawing|legacyDrawingHF|drawingHF|picture|oleObjects|"
                      r"controls|webPublishItems|tableParts|extLst)\b", f)
        pos = m.start() if m else f.rfind("</")
        parts[feuille_dst] = (f[:pos] + el + f[pos:]).encode("utf-8")
        # Type de contenu
        ct = parts["[Content_Types].xml"].decode("utf-8")
        if f'PartName="/{chemin}"' not in ct:
            ct = ct.replace("</Types>", f'<Override PartName="/{chemin}" '
                                        f'ContentType="{_CT_DESSIN}"/></Types>')
            parts["[Content_Types].xml"] = ct.encode("utf-8")
        recopies.append(nom)
    return recopies


def finaliser(donnees: bytes, source: bytes | None = None) -> bytes:
    """
    Dernière étape après un enregistrement par openpyxl (génération du
    classeur, export `/excel` de l'API) : graphiques corrigés pour Excel, et
    formes des onglets non reconstruits recopiées depuis le classeur `source`
    (template ou classeur maître), openpyxl ne sachant pas les conserver.
    """
    import io
    with zipfile.ZipFile(io.BytesIO(donnees)) as z:
        infos = z.infolist()
        parts = {i.filename: z.read(i.filename) for i in infos}
    for k in list(parts):
        if re.match(r"xl/charts/chart\d+\.xml$", k):
            parts[k] = _patcher_graphique(parts[k].decode("utf-8")).encode("utf-8")
    if source is not None:
        with zipfile.ZipFile(io.BytesIO(source)) as z:
            src = {i.filename: z.read(i.filename) for i in z.infolist()}
        _recopier_formes(parts, src)
    out = io.BytesIO()
    noms = [i.filename for i in infos]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for k in noms + [k for k in parts if k not in noms]:
            z.writestr(k, parts[k])
    return out.getvalue()
