"""La doc ne cite que ce qui existe (garde de la règle « une info, un seul fichier qui fait foi »).

Contrôle mécanique de `README.md` (dépôt public) et de `PROJET.md` (mémoire courte,
dépôt privé `therapie_studio`, ignoré quand il n'est pas à côté — en CI par exemple) :
- chaque chemin de code cité entre backticks (`src/…`, `scripts/…`, `tests/…`,
  `alembic/…`) existe ;
- chaque test cité par son nom (`test_xxx`, `TestXxx`) existe, en fichier ou en définition ;
- la liste des sous-commandes `python -m src.cli {a|b|…}` est exactement celle du parseur.

Ce test n'attrape pas une phrase fausse, seulement une référence morte : la
relecture du sens reste l'étape « cohérence de la doc » de `/bilan`.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from src.cli import build_parser

RACINE = Path(__file__).resolve().parents[1]
README = RACINE / "README.md"
PROJET = RACINE.parent / "therapie_studio" / "notes" / "scraper" / "PROJET.md"

_BACKTICKS = re.compile(r"`([^`\n]+)`")
_CHEMIN = re.compile(r"(?<![\w/.])((?:src|scripts|tests|alembic)/[\w./-]*[\w/])")
_NOM_DE_TEST = re.compile(r"^(test_\w+|Test[A-Z]\w+)$")
_SOUS_COMMANDES = re.compile(r"src\.cli \{([^}]+)\}")


def _docs() -> list[Path]:
    docs = [README]
    if PROJET.exists():
        docs.append(PROJET)
    return docs


def _segments(doc: Path) -> list[str]:
    return _BACKTICKS.findall(doc.read_text(encoding="utf-8"))


def _noms_definis_dans_les_tests() -> set[str]:
    noms: set[str] = set()
    for fichier in (RACINE / "tests").rglob("*.py"):
        noms.add(fichier.stem)
        texte = fichier.read_text(encoding="utf-8", errors="replace")
        noms.update(re.findall(r"^\s*(?:def|class)\s+(\w+)", texte, flags=re.MULTILINE))
    return noms


@pytest.mark.parametrize("doc", _docs(), ids=lambda p: p.name)
def test_chemins_cites_existent(doc: Path) -> None:
    morts = sorted(
        {
            chemin
            for segment in _segments(doc)
            for chemin in _CHEMIN.findall(segment)
            if not (RACINE / chemin).exists()
        }
    )
    assert not morts, f"{doc.name} cite des chemins qui n'existent pas : {morts}"


@pytest.mark.parametrize("doc", _docs(), ids=lambda p: p.name)
def test_tests_cites_existent(doc: Path) -> None:
    connus = _noms_definis_dans_les_tests()
    cites = {s.strip() for s in _segments(doc) if _NOM_DE_TEST.match(s.strip())}
    morts = sorted(cites - connus)
    assert not morts, f"{doc.name} cite des tests qui n'existent pas : {morts}"


def _sous_commandes_du_parseur() -> set[str]:
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError("le parseur de src.cli n'a plus de sous-commandes")


@pytest.mark.parametrize("doc", _docs(), ids=lambda p: p.name)
def test_sous_commandes_cli_a_jour(doc: Path) -> None:
    attendues = _sous_commandes_du_parseur()
    for liste in _SOUS_COMMANDES.findall(doc.read_text(encoding="utf-8")):
        citees = {nom.strip() for nom in liste.split("|")}
        assert citees == attendues, (
            f"{doc.name} : sous-commandes en trop {sorted(citees - attendues)}, "
            f"manquantes {sorted(attendues - citees)}"
        )
