"""
Cohérence documentation <-> code.

Garde-fou contre la dérive de la doc Markdown (README, README des
sous-dossiers, docs/*.md) quand le code évolue : modules, commandes CLI et
options cités, liens relatifs, identifiants de code entre backticks. Rapide et
sans données : à lancer après toute modification, de code comme de doc.

Ne couvre pas le rapport PDF de docs/ (relecture manuelle).
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

DOCS_MD = [
    ROOT / "README.md",
    ROOT / "api" / "README.md",
    ROOT / "standalone_tool" / "README.md",
    *sorted((ROOT / "docs").glob("*.md")),
]

# Dossiers dont chaque module doit être cité dans le README principal.
DOSSIERS_README = ["sources", "pipeline", "scripts", "tests"]

# Code dans lequel les identifiants cités doivent exister.
CODE_GLOBS = [
    "main.py", "dodo.py",
    "config/*.py", "config/*.yaml",
    "sources/*.py", "pipeline/*.py", "api/**/*.py",
    "scripts/*.py", "standalone_tool/*.py", "tests/*.py",
]

# Chemins cités entre backticks qui n'existent pas forcément localement
# (générés, gitignorés, ou fournis manuellement).
PREFIXES_NON_VERIFIES = (
    "data/", "standalone_tool/output", "archives",
    "docs/Sources donnees.xlsx", "docs/Territorialisation.xlsx",
)


def _texte(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def corpus_code() -> set[str]:
    """Ensemble des mots (identifiants, clés yaml, chaînes) du code."""
    mots: set[str] = set()
    for motif in CODE_GLOBS:
        for f in ROOT.glob(motif):
            if f.name == "test_docs.py":
                continue
            mots.update(re.findall(r"[A-Za-z_][\w]*", _texte(f)))
    return mots


def test_fichiers_doc_presents():
    for md in DOCS_MD:
        assert md.exists(), md


@pytest.mark.parametrize("dossier", DOSSIERS_README)
def test_modules_cites_dans_readme(dossier):
    readme = _texte(ROOT / "README.md")
    manquants = [
        f.name for f in sorted((ROOT / dossier).glob("*.py"))
        if f.name not in ("__init__.py", "conftest.py") and f.name not in readme
    ]
    assert not manquants, f"{dossier}/ : modules absents du README : {manquants}"


def test_commandes_main_dans_readme():
    commandes = re.findall(r'add_parser\(\s*"(\w+)"', _texte(ROOT / "main.py"))
    assert commandes
    readme = _texte(ROOT / "README.md")
    manquantes = [c for c in commandes if f"main.py {c}" not in readme]
    assert not manquantes, f"Commandes main.py absentes du README : {manquantes}"


def test_options_standalone_tool_documentees():
    src = _texte(ROOT / "standalone_tool" / "update_standalone_tool.py")
    options = set(re.findall(r'add_argument\(\s*"(--[\w-]+)"', src))
    assert options
    doc = _texte(ROOT / "standalone_tool" / "README.md")
    manquantes = sorted(o for o in options if f"`{o}" not in doc)
    assert not manquantes, f"Options absentes de standalone_tool/README.md : {manquantes}"


@pytest.mark.parametrize("md", DOCS_MD, ids=lambda p: str(p.relative_to(ROOT)))
def test_liens_relatifs(md):
    casses = []
    for cible in re.findall(r"\]\(<?([^)>]+)>?\)", _texte(md)):
        if re.match(r"[a-z]+://|mailto:|#", cible):
            continue
        chemin = cible.split("#", 1)[0]
        if chemin and not (md.parent / chemin).exists():
            casses.append(cible)
    assert not casses, f"Liens cassés : {casses}"


def _identifiants_cites(texte: str) -> set[str]:
    """
    Identifiants de code cités entre backticks, hors blocs de code : appels
    `f()`, références `module.f` / `fichier.py::Classe`, constantes et noms
    en snake_case. Les motifs génériques (`EC_CONSO_*`, `<zone>`) sont ignorés.
    """
    texte = re.sub(r"```.*?```", "", texte, flags=re.S)
    cites = set()
    for tok in re.findall(r"`([^`\s]+)`", texte):
        if re.search(r"[*<>|/\\]", tok) or re.search(r"\.(py|xlsx|yaml|parquet|json|csv|md)\b", tok):
            continue
        tok = tok.removesuffix("()")
        for part in re.split(r"::|\.", tok):
            if re.fullmatch(r"[A-Za-z]\w*", part) and "_" in part:
                cites.add(part)
    return cites


def _existe(ident: str, corpus: set[str]) -> bool:
    # Variables d'environnement pydantic-settings : préfixe + nom du champ
    # (cf. api/config.py).
    if ident.startswith("TRAJGES_API_") and ident != "TRAJGES_API_":
        return ident.removeprefix("TRAJGES_API_").lower() in corpus
    return ident in corpus


@pytest.mark.parametrize("md", DOCS_MD, ids=lambda p: str(p.relative_to(ROOT)))
def test_identifiants_existent_dans_le_code(md, corpus_code):
    absents = sorted(i for i in _identifiants_cites(_texte(md)) if not _existe(i, corpus_code))
    assert not absents, f"Identifiants cités mais introuvables dans le code : {absents}"


@pytest.mark.parametrize("md", DOCS_MD, ids=lambda p: str(p.relative_to(ROOT)))
def test_chemins_cites_existent(md):
    texte = re.sub(r"```.*?```", "", _texte(md), flags=re.S)
    absents = []
    for tok in re.findall(r"`([\w./ -]+/[\w./ -]*\.\w+)`", texte):
        if tok.startswith(PREFIXES_NON_VERIFIES):
            continue
        if not ((ROOT / tok).exists() or (md.parent / tok).exists()):
            absents.append(tok)
    assert not absents, f"Chemins cités introuvables : {absents}"
