"""
sources/geo_epci.py
===================
Chargement du référentiel géographique des EPCI et de la correspondance
commune→EPCI depuis les API publiques.

Sources :
  - Insee, « EPCI au 01/01/AAAA » : liste des EPCI et composition communale
    du millésime, source du rattachement commune→EPCI et de la liste de
    référence des EPCI
  - geo.api.gouv.fr : centroïdes et noms courts des EPCI, centroïdes communaux

Note millésime :
  Le rattachement commune→EPCI change chaque année (fusions d'EPCI,
  communes qui changent d'EPCI). geo.api.gouv.fr ne fournit aucun millésime
  et suit le COG avec retard : en novembre 2026, elle servait encore les
  EPCI de 2025 (deux fusions et trois changements de rattachement au
  01/01/2026 manquants). Le fichier Insee, millésimé dans son URL, fait donc
  référence ; geo.api.gouv.fr ne sert plus qu'aux centroïdes et aux noms
  courts (« CC de ... ») des EPCI qu'elle connaît, les autres recevant le
  libellé Insee abrégé et le centroïde moyen de leurs communes.

Note fusions d'EPCI :
  Les sources publiées directement par EPCI (OREC) peuvent porter les codes
  d'EPCI disparus au 1er janvier. load_fusions_epci compare la composition
  communale du millésime à celle du millésime précédent pour retrouver, pour
  chaque EPCI issu d'une fusion, les EPCI qu'il réunit : la source peut alors
  recomposer le nouvel EPCI par somme (cf. sources/energie_climat.py).
"""

import io
import json
import logging
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from config.settings import INTERIM_DIR

URL_EPCI_API = "https://geo.api.gouv.fr/epcis?fields=code,nom,centre"

# Page Insee "Intercommunalite - Telechargement des fichiers relatifs aux
# EPCI a fiscalite propre et aux EPT" : volet EPT disponible depuis 2023.
URL_EPCI_TEMPLATE = "https://www.insee.fr/fr/statistiques/fichier/2510634/EPCI_au_01-01-{annee}.zip"
URL_EPT_TEMPLATE = "https://www.insee.fr/fr/statistiques/fichier/2510634/ept_au_01-01-{annee}.zip"
# Commune sans EPCI (îles mono-communales) ou hors MGP (ou Paris) dans les
# fichiers Insee.
_CODE_SANS_OBJET = "ZZZZZZZZZ"
_EPT_CODE_SANS_OBJET = _CODE_SANS_OBJET

# Abréviation des libellés Insee, sur le modèle des noms geo.api.gouv.fr.
_ABREVIATIONS_EPCI = (
    ("Communauté de communes", "CC"),
    ("Communauté d'agglomération", "CA"),
    ("Communauté urbaine", "CU"),
)

# Paris n'appartient a aucun EPT (statut particulier) : traitee comme sa
# propre "commune-territoire" partout ou une decomposition infra-MGP est
# necessaire (agregation EPCI/EPT/commune, centroides...).
CODE_COMMUNE_PARIS = "75056"
NOM_COMMUNE_PARIS  = "Ville de Paris"

log = logging.getLogger(__name__)

SOURCE_META = {
    "id": "GEO_EPCI",
    "nom": "INSEE EPCI + INSEE EPT + geo.api.gouv.fr",
    "description": (
        "Referentiel geographique : liste des EPCI et table communes-EPCI du "
        "millesime (Insee), centroides EPCI et communaux (geo.api.gouv.fr), "
        "table communes-EPT (11 etablissements publics territoriaux de la "
        "Metropole du Grand Paris)"
    ),
    "urls": (
        "https://www.insee.fr/fr/statistiques/fichier/2510634/EPCI_au_01-01-{annee}.zip\n"
        "https://www.insee.fr/fr/statistiques/fichier/2510634/ept_au_01-01-{annee}.zip\n"
        "https://geo.api.gouv.fr/epcis?fields=code,nom,centre\n"
        "https://geo.api.gouv.fr/communes?fields=code,centre"
    ),
    "niveau_geo": "EPCI (via communes) + EPT (Metropole du Grand Paris)",
    "annees": "Millesime COG de l'annee (Insee, publie vers mars ; volet EPT disponible depuis 2023)",
    "format": "XLSX (Insee, dans un zip) + JSON (API)",
    "acces": "Automatique (telechargement + API)",
    "frequence_maj": "Annuelle",
    "licence": "Etalab 2.0",
    "theme": "Territoire",
    "notes": (
        "Rattachement commune-EPCI et liste des EPCI : fichier Insee du millesime "
        "(geo.api.gouv.fr suit le COG avec retard). Fusions d'EPCI au 1er janvier "
        "detectees par comparaison avec le millesime precedent. "
        "EPT : Paris (75056) n'appartient a aucun EPT (statut particulier), "
        "code Insee 'ZZZZZZZZZ'/'Sans objet' exclu de la correspondance. "
        "Le fichier xlsx Insee a un styles.xml qui fait planter openpyxl : "
        "lu directement via son XML (cf. _read_xlsx_sheet_rows)."
    ),
    "indicateurs": [],
}


def _load_geo_api_epcis(cache_dir: Path = INTERIM_DIR, force: bool = False) -> dict[str, dict]:
    """
    EPCI connus de geo.api.gouv.fr : {code: {"nom": ..., "lat": ..., "lon": ...}}.

    Le cache est nommé par année de téléchargement : l'API ne fournit aucun
    millésime, donc ce nommage garantit au moins un retéléchargement par an.
    """
    cache_path = cache_dir / f"epci_centroids_{date.today().year}.json"

    if not cache_path.exists() or force:
        url = URL_EPCI_API + ",codesDepartements,codesRegions"
        log.info(f"Téléchargement référentiel EPCI depuis {url}")
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(r.content)

    with open(cache_path, encoding="utf-8") as f:
        data = json.load(f)

    epcis = {}
    for item in data:
        centre = item.get("centre") or {}
        lat = lon = None
        if centre.get("type") == "Point":
            lon, lat = centre["coordinates"]
        epcis[item.get("code", "")] = {"nom": item.get("nom", ""), "lat": lat, "lon": lon}
    return epcis


def _abreger_libelle_epci(libelle: str) -> str:
    """« Communauté de communes du X » -> « CC du X » (cf. _ABREVIATIONS_EPCI)."""
    for long, court in _ABREVIATIONS_EPCI:
        if libelle.startswith(long):
            return court + libelle[len(long):]
    return libelle


def _download_epci_zip(cache_dir: Path, annee: int | None = None, force: bool = False) -> tuple[Path, int]:
    """
    Zip Insee « EPCI au 01/01/AAAA » et son millésime. Sans `annee` :
    millésime de l'année courante, ou à défaut (pas encore publié, vers mars)
    celui de l'année précédente.
    """
    annees = (annee,) if annee else (date.today().year, date.today().year - 1)
    return _download_insee_zip(URL_EPCI_TEMPLATE, "epci_insee", "EPCI", annees, cache_dir, force)


def load_composition_epci(
    annee: int | None = None,
    cache_dir: Path = INTERIM_DIR,
    force: bool = False,
) -> tuple[pd.DataFrame, int]:
    """
    Composition communale des EPCI au 1er janvier, d'après l'Insee.

    Sans `annee`, millésime le plus récent publié (cf. _download_epci_zip).
    Les communes sans EPCI (îles mono-communales, code Insee 'ZZZZZZZZZ') sont
    exclues.

    Retourne (DataFrame codgeo, code_epci, libelle_insee, code_dep, code_reg ;
    millésime).
    """
    zip_path, millesime = _download_epci_zip(cache_dir, annee, force)
    cache_path = cache_dir / f"commune_epci_insee_{millesime}.parquet"
    if cache_path.exists() and not force:
        return pd.read_parquet(cache_path), millesime

    with zipfile.ZipFile(zip_path) as outer:
        xlsx_name = next(n for n in outer.namelist() if n.endswith(".xlsx"))
        xlsx_bytes = outer.read(xlsx_name)
    rows = _read_xlsx_sheet_rows(xlsx_bytes, "Composition_communale")
    df = _xlsx_rows_to_df(rows, {"CODGEO", "EPCI", "LIBEPCI", "DEP", "REG"})
    df = df.rename(columns={
        "CODGEO": "codgeo", "EPCI": "code_epci", "LIBEPCI": "libelle_insee",
        "DEP": "code_dep", "REG": "code_reg",
    })
    df = df.dropna(subset=["codgeo", "code_epci"])
    df = df[df["code_epci"] != _CODE_SANS_OBJET]
    df["codgeo"] = df["codgeo"].astype(str).str.zfill(5)
    df = df[["codgeo", "code_epci", "libelle_insee", "code_dep", "code_reg"]].reset_index(drop=True)

    cache_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path, index=False)
    log.info(
        f"Composition communale des EPCI au 01/01/{millesime} (Insee) : "
        f"{df['codgeo'].nunique()} communes, {df['code_epci'].nunique()} EPCI"
    )
    return df, millesime


def millesime_epci(cache_dir: Path = INTERIM_DIR) -> int:
    """Millésime Insee du rattachement commune→EPCI en vigueur dans le pipeline."""
    return load_composition_epci(cache_dir=cache_dir)[1]


def _noms_epci(compo: pd.DataFrame, geo_api: dict[str, dict]) -> dict[str, str]:
    """Nom court geo.api.gouv.fr si l'EPCI y figure, sinon libellé Insee abrégé."""
    libelles = compo.drop_duplicates("code_epci").set_index("code_epci")["libelle_insee"]
    return {
        code: geo_api[code]["nom"] if geo_api.get(code, {}).get("nom") else _abreger_libelle_epci(lib)
        for code, lib in libelles.items()
    }


def load_epci_centroids(cache_dir: Path = INTERIM_DIR, force: bool = False) -> pd.DataFrame:
    """
    Liste de référence des EPCI du millésime Insee le plus récent, avec nom,
    centroïde, régions et départements.

    Retourne un DataFrame : code_epci, nom_epci, lat, lon, codes_regions, codes_departements
      - codes_regions      : liste de codes région (ex. ["84"]) ; DROM = {"01","02","03","04","06"}
      - codes_departements : liste de codes département (ex. ["74"])

    Régions et départements viennent des communes membres (Insee). Le nom et
    le centroïde viennent de geo.api.gouv.fr ; pour un EPCI qu'elle ne
    connaît pas encore (fusion au 1er janvier), libellé Insee abrégé et
    moyenne des centroïdes de ses communes.
    """
    compo, millesime = load_composition_epci(cache_dir=cache_dir, force=force)
    geo_api = _load_geo_api_epcis(cache_dir, force)
    noms = _noms_epci(compo, geo_api)

    grp = compo.groupby("code_epci")
    df = pd.DataFrame({
        "code_epci":          list(grp.groups),
        "codes_regions":      [sorted(set(g["code_reg"])) for _, g in grp],
        "codes_departements": [sorted(set(g["code_dep"])) for _, g in grp],
    })
    df["nom_epci"] = df["code_epci"].map(noms)
    df["lat"] = df["code_epci"].map(lambda c: geo_api.get(c, {}).get("lat"))
    df["lon"] = df["code_epci"].map(lambda c: geo_api.get(c, {}).get("lon"))

    sans_centre = df["lat"].isna() | df["lon"].isna()
    if sans_centre.any():
        corr = compo.loc[compo["code_epci"].isin(df.loc[sans_centre, "code_epci"]), ["codgeo", "code_epci"]]
        corr = corr.assign(nom_epci=corr["code_epci"].map(noms))
        centres = compute_infra_centroids(corr, cache_dir=cache_dir).set_index("code_epci")
        df.loc[sans_centre, "lat"] = df.loc[sans_centre, "code_epci"].map(centres["lat"])
        df.loc[sans_centre, "lon"] = df.loc[sans_centre, "code_epci"].map(centres["lon"])
        log.info(
            f"{int(sans_centre.sum())} EPCI absents de geo.api.gouv.fr (millésime {millesime}), "
            f"centroïde calculé depuis leurs communes : "
            + ", ".join(f"{c} {noms[c]}" for c in df.loc[sans_centre, "code_epci"])
        )

    df = df[["code_epci", "nom_epci", "lat", "lon", "codes_regions", "codes_departements"]]
    log.info(f"{len(df)} EPCI chargés (Insee, millésime {millesime})")
    return df


def load_commune_epci_cog(
    cache_dir: Path = INTERIM_DIR,
    force: bool = False,
) -> pd.DataFrame:
    """
    Rattachement commune→EPCI du millésime Insee le plus récent
    (cf. load_composition_epci).

    Retourne un DataFrame : codgeo (INSEE commune), code_epci, nom_epci
    (noms identiques à ceux de load_epci_centroids).
    """
    compo, millesime = load_composition_epci(cache_dir=cache_dir, force=force)
    noms = _noms_epci(compo, _load_geo_api_epcis(cache_dir))
    df = compo[["codgeo", "code_epci"]].copy()
    df["nom_epci"] = df["code_epci"].map(noms)
    log.info(f"{len(df)} correspondances commune→EPCI chargées (Insee, millésime {millesime})")
    return df


def load_fusions_epci(cache_dir: Path = INTERIM_DIR, force: bool = False) -> dict[str, list[str]]:
    """
    EPCI issus d'une fusion au 1er janvier du millésime en vigueur.

    Compare la composition communale du millésime à celle du millésime
    précédent. Un EPCI disparu dont toutes les communes rejoignent le même
    EPCI est rattaché à ce dernier ; s'il s'est scindé, il ne peut pas être
    recomposé et un avertissement le signale.

    Retourne {code EPCI du millésime: [codes du millésime précédent qu'il
    réunit]}. La liste comprend le code lui-même quand il existait déjà
    (absorption d'un EPCI par un autre). Les communes qui changent d'EPCI
    sans fusion ne sont pas concernées.
    """
    compo, millesime = load_composition_epci(cache_dir=cache_dir, force=force)
    compo_prec, _ = load_composition_epci(millesime - 1, cache_dir=cache_dir, force=force)

    codes_prec = set(compo_prec["code_epci"])
    disparus = codes_prec - set(compo["code_epci"])
    devenir = (
        compo_prec.loc[compo_prec["code_epci"].isin(disparus), ["codgeo", "code_epci"]]
        .merge(compo[["codgeo", "code_epci"]], on="codgeo", suffixes=("_ancien", ""))
    )

    fusions: dict[str, list[str]] = {}
    for ancien, grp in devenir.groupby("code_epci_ancien"):
        destinations = sorted(grp["code_epci"].unique())
        if len(destinations) == 1:
            fusions.setdefault(destinations[0], []).append(ancien)
        else:
            log.warning(
                f"EPCI {ancien} ({millesime - 1}) réparti entre {len(destinations)} EPCI au "
                f"01/01/{millesime} ({', '.join(destinations)}) : non recomposable"
            )
    for nouveau, anciens in fusions.items():
        if nouveau in codes_prec:
            anciens.append(nouveau)
        anciens.sort()
    if fusions:
        log.info(
            f"Fusions d'EPCI au 01/01/{millesime} : "
            + " ; ".join(f"{n} <- {', '.join(a)}" for n, a in sorted(fusions.items()))
        )
    return fusions


# ---------------------------------------------------------------------------
# EPT (établissements publics territoriaux) de la Métropole du Grand Paris
# ---------------------------------------------------------------------------
#
# L'Insee publie ce fichier dans la même série que l'EPCI (volet EPT
# disponible depuis 2023), mais son styles.xml fait planter openpyxl
# ("Colors must be aRGB hex values" - constaté sur le millésime 2026). On
# contourne en lisant les cellules directement dans le XML du xlsx, sans
# jamais passer par les styles.

_XLSX_NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_XLSX_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _read_xlsx_sheet_rows(xlsx_bytes: bytes, sheet_name: str) -> list[dict]:
    """
    Lit une feuille xlsx cellule par cellule via le XML brut (contourne les
    bugs de parsing openpyxl liés aux styles). Retourne une liste de dicts
    {lettre_colonne: valeur} par ligne, dans l'ordre du fichier.
    """
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as z:
        wb_root = ET.fromstring(z.read("xl/workbook.xml"))
        sheet_el = next(
            s for s in wb_root.find("s:sheets", _XLSX_NS)
            if s.get("name") == sheet_name
        )
        r_id = sheet_el.get(f"{{{_XLSX_NS_R}}}id")

        rels_root = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = next(rel.get("Target") for rel in rels_root if rel.get("Id") == r_id)

        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            sst_root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in sst_root.findall("s:si", _XLSX_NS):
                shared.append("".join(t.text or "" for t in si.findall(".//s:t", _XLSX_NS)))

        sheet_root = ET.fromstring(z.read(f"xl/{target}"))
        rows = []
        for row in sheet_root.findall(".//s:row", _XLSX_NS):
            cells = {}
            for c in row.findall("s:c", _XLSX_NS):
                col = "".join(ch for ch in c.get("r") if ch.isalpha())
                t = c.get("t")
                v = c.find("s:v", _XLSX_NS)
                val = v.text if v is not None else None
                if t == "s" and val is not None:
                    val = shared[int(val)]
                cells[col] = val
            rows.append(cells)
    return rows


def _xlsx_rows_to_df(rows: list[dict], header_values: set[str]) -> pd.DataFrame:
    """
    Convertit les lignes brutes (lettre_colonne → valeur) en DataFrame, en
    repérant automatiquement la ligne d'en-tête technique (celle qui contient
    exactement `header_values`) plutôt qu'un numéro de ligne fixe.
    """
    header_idx = next(
        i for i, r in enumerate(rows)
        if header_values <= {str(v).strip() for v in r.values() if v is not None}
    )
    header = {letter: str(name).strip() for letter, name in rows[header_idx].items() if name}
    data = [
        {header[k]: v for k, v in r.items() if k in header}
        for r in rows[header_idx + 1:]
    ]
    return pd.DataFrame(data)


def _download_insee_zip(
    url_template: str, prefixe_cache: str, libelle: str,
    annees: tuple[int, ...], cache_dir: Path, force: bool = False,
) -> tuple[Path, int]:
    """
    Télécharge un zip Insee millésimé dans son URL (publication vers mars de
    chaque année), en essayant les millésimes `annees` dans l'ordre : le
    premier en cache ou téléchargeable est retenu. Retourne (chemin, millésime).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for annee in annees:
        cache_path = cache_dir / f"{prefixe_cache}_{annee}.zip"
        if cache_path.exists() and not force:
            return cache_path, annee
        url = url_template.format(annee=annee)
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
        except requests.RequestException as e:
            last_error = e
            continue
        cache_path.write_bytes(r.content)
        log.info(f"Fichier {libelle} Insee {annee} téléchargé : {cache_path.name}")
        return cache_path, annee
    raise RuntimeError(
        f"Impossible de télécharger le fichier {libelle} Insee (millésimes "
        f"{', '.join(map(str, annees))}) : {last_error}"
    )


def _download_ept_zip(cache_dir: Path, force: bool = False) -> Path:
    """Zip Insee des EPT : millésime courant ou, s'il n'est pas encore publié, précédent."""
    annees = (date.today().year, date.today().year - 1)
    return _download_insee_zip(URL_EPT_TEMPLATE, "ept_insee", "EPT", annees, cache_dir, force)[0]


def _load_ept_xlsx_bytes(cache_dir: Path, force: bool = False) -> bytes:
    zip_path = _download_ept_zip(cache_dir, force)
    with zipfile.ZipFile(zip_path) as outer:
        xlsx_name = next(n for n in outer.namelist() if n.endswith(".xlsx"))
        return outer.read(xlsx_name)


def load_ept_list(cache_dir: Path = INTERIM_DIR, force: bool = False) -> pd.DataFrame:
    """
    Charge la liste des 11 EPT de la Métropole du Grand Paris (Insee, même
    série que l'EPCI).

    Retourne un DataFrame : code_ept, nom_ept, nb_communes
    """
    cache_path = cache_dir / f"ept_list_{date.today().year}.parquet"
    if cache_path.exists() and not force:
        return pd.read_parquet(cache_path)

    xlsx_bytes = _load_ept_xlsx_bytes(cache_dir, force)
    rows = _read_xlsx_sheet_rows(xlsx_bytes, "EPT")
    df = _xlsx_rows_to_df(rows, {"EPT", "LIBEPT", "NB_COM"})
    df = df.rename(columns={"EPT": "code_ept", "LIBEPT": "nom_ept", "NB_COM": "nb_communes"})
    df = df.dropna(subset=["code_ept"])
    df = df[df["code_ept"] != _EPT_CODE_SANS_OBJET]
    df["nb_communes"] = pd.to_numeric(df["nb_communes"], errors="coerce").astype("Int64")
    df = df[["code_ept", "nom_ept", "nb_communes"]]

    cache_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path, index=False)
    log.info(f"{len(df)} EPT chargés (Insee)")
    return df


def load_commune_ept_cog(cache_dir: Path = INTERIM_DIR, force: bool = False) -> pd.DataFrame:
    """
    Charge la correspondance commune → EPT de la Métropole du Grand Paris
    (Insee, même série/millésime COG que l'EPCI).

    Paris (75056) et toute commune hors MGP sont absentes (codées
    'ZZZZZZZZZ' / 'Sans objet' côté Insee : Paris n'appartient à aucun EPT).

    Retourne un DataFrame : codgeo, code_ept, nom_ept, code_dep
    """
    cache_path = cache_dir / f"commune_ept_cog_{date.today().year}.parquet"
    if cache_path.exists() and not force:
        return pd.read_parquet(cache_path)

    xlsx_bytes = _load_ept_xlsx_bytes(cache_dir, force)
    rows = _read_xlsx_sheet_rows(xlsx_bytes, "Composition_communale")
    df = _xlsx_rows_to_df(rows, {"CODGEO", "LIBGEO", "EPT", "LIBEPT", "DEP", "REG"})
    df = df.rename(columns={"CODGEO": "codgeo", "EPT": "code_ept", "LIBEPT": "nom_ept", "DEP": "code_dep"})
    df = df.dropna(subset=["codgeo", "code_ept"])
    df = df[df["code_ept"] != _EPT_CODE_SANS_OBJET]
    df["codgeo"] = df["codgeo"].astype(str).str.zfill(5)
    df = df[["codgeo", "code_ept", "nom_ept", "code_dep"]]

    cache_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path, index=False)
    log.info(f"{df['codgeo'].nunique()} communes → {df['code_ept'].nunique()} EPT chargées (Insee)")
    return df


# ---------------------------------------------------------------------------
# Communes à arrondissements municipaux (Paris, Lyon, Marseille)
# ---------------------------------------------------------------------------
#
# Certaines sources (FLORES, mobilité domicile-travail) publient leurs
# données au niveau "arrondissement municipal" pour ces trois villes plutôt
# qu'au niveau commune COG. La correspondance commune->EPCI (load_commune_epci_cog)
# ne connaît que le code commune (75056/69123/13055), jamais les
# arrondissements : sans regroupement préalable, ces trois villes sont
# silencieusement absentes de toute agrégation EPCI/EPT/commune sur ces
# sources (constaté : 0 commune trouvée pour Paris sur FLORES et la mobilité
# domicile-travail, alors que 75056 existe bien dans la correspondance).

ARRONDISSEMENTS_PLM: dict[str, str] = {
    **{f"751{i:02d}": "75056" for i in range(1, 21)},   # Paris (20 arrondissements)
    **{f"6938{i}": "69123" for i in range(1, 10)},        # Lyon (9 arrondissements)
    **{f"132{i:02d}": "13055" for i in range(1, 17)},     # Marseille (16 arrondissements)
}


def regrouper_arrondissements_plm(df: pd.DataFrame) -> pd.DataFrame:
    """
    Regroupe les arrondissements municipaux de Paris/Lyon/Marseille sous le
    code de leur commune (75056/69123/13055), par somme.

    Paramètre : DataFrame indexé sur le code commune/arrondissement INSEE
    (5 caractères), colonnes = indicateurs numériques. Retourne un DataFrame
    de même forme, avec les codes d'arrondissement fondus dans leur commune.
    """
    idx_name = df.index.name
    n_arrondissements = df.index.astype(str).isin(ARRONDISSEMENTS_PLM).sum()
    if not n_arrondissements:
        return df

    regroupe = df.index.to_series().astype(str).map(lambda c: ARRONDISSEMENTS_PLM.get(c, c))
    result = df.groupby(regroupe).sum(min_count=1)
    result.index.name = idx_name
    log.info(
        f"{n_arrondissements} arrondissements Paris/Lyon/Marseille regroupés "
        f"sous leur commune (75056/69123/13055)"
    )
    return result


# ---------------------------------------------------------------------------
# Centroïdes infra-EPCI (EPT, Paris) - pour le DJU (sources/dju.py)
# ---------------------------------------------------------------------------
#
# geo.api.gouv.fr ne connaît pas les EPT : contrairement à load_epci_centroids
# (centroïde EPCI fourni directement par l'API), le centroïde d'un EPT ou de
# Paris se calcule ici, comme la moyenne pondérée par population des
# centroïdes de ses communes membres.

URL_COMMUNES_CENTROIDS = "https://geo.api.gouv.fr/communes?fields=code,centre"


def load_commune_centroids(cache_dir: Path = INTERIM_DIR, force: bool = False) -> pd.DataFrame:
    """
    Charge les centroïdes (lat, lon) de toutes les communes de France depuis
    geo.api.gouv.fr.

    Retourne un DataFrame : codgeo, lat, lon
    """
    cache_path = cache_dir / f"commune_centroids_{date.today().year}.parquet"
    if cache_path.exists() and not force:
        return pd.read_parquet(cache_path)

    log.info(f"Téléchargement centroïdes communaux depuis {URL_COMMUNES_CENTROIDS}")
    r = requests.get(URL_COMMUNES_CENTROIDS, timeout=120)
    r.raise_for_status()
    data = r.json()

    records = []
    for item in data:
        centre = item.get("centre") or {}
        if centre.get("type") == "Point":
            lon, lat = centre["coordinates"]
            records.append({"codgeo": item["code"], "lat": lat, "lon": lon})

    df = pd.DataFrame(records)
    cache_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path, index=False)
    log.info(f"{len(df)} centroïdes communaux chargés (geo.api.gouv.fr)")
    return df


def compute_infra_centroids(
    correspondance: pd.DataFrame,
    pop_communes: pd.DataFrame | None = None,
    cache_dir: Path = INTERIM_DIR,
) -> pd.DataFrame:
    """
    Calcule le centroïde pondéré par population de chaque territoire d'une
    correspondance commune->territoire générique (EPT, Paris...) - même
    mécanique que load_epci_centroids mais pour des mailles que
    geo.api.gouv.fr ne connaît pas.

    Paramètres :
      correspondance : DataFrame (codgeo, code_epci, nom_epci) - même forme
                        que la correspondance commune->EPCI, réutilisable
                        pour un EPT ou une commune-territoire (Paris).
      pop_communes    : DataFrame (codgeo, population). Si None, poids égal
                        par commune (comme insee_omphale._build_epci_dep_weights).

    Retourne : code_epci, nom_epci, lat, lon
    """
    centroids = load_commune_centroids(cache_dir)
    df = correspondance.merge(centroids, on="codgeo", how="inner")

    if pop_communes is not None:
        pop = pop_communes[["codgeo", "population"]].copy()
        pop["population"] = pd.to_numeric(pop["population"], errors="coerce").fillna(0)
        df = df.merge(pop, on="codgeo", how="left")
        df["population"] = df["population"].fillna(0)
    else:
        df["population"] = 1.0

    df["w_lat"] = df["lat"] * df["population"]
    df["w_lon"] = df["lon"] * df["population"]
    grp = (
        df.groupby(["code_epci", "nom_epci"], as_index=False)
        .agg(w_lat=("w_lat", "sum"), w_lon=("w_lon", "sum"), poids=("population", "sum"))
    )
    # Repli en moyenne simple si aucune population connue pour le territoire
    # (poids total nul) - évite une division par zéro plutôt qu'un centroïde
    # manquant.
    sans_poids = grp["poids"] == 0
    if sans_poids.any():
        moyennes = (
            df[df["code_epci"].isin(grp.loc[sans_poids, "code_epci"])]
            .groupby("code_epci")[["lat", "lon"]].mean()
        )
        grp.loc[sans_poids, "w_lat"] = grp.loc[sans_poids, "code_epci"].map(moyennes["lat"])
        grp.loc[sans_poids, "w_lon"] = grp.loc[sans_poids, "code_epci"].map(moyennes["lon"])
        grp.loc[sans_poids, "poids"] = 1.0

    grp["lat"] = grp["w_lat"] / grp["poids"]
    grp["lon"] = grp["w_lon"] / grp["poids"]
    return grp[["code_epci", "nom_epci", "lat", "lon"]]
