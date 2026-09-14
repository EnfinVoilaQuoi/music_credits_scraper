"""Les services et la CLI ne dépendent JAMAIS de la GUI (crible AST).

Le chantier « piloter sans GUI » (2026-09-14) extrait la logique des workers
vers `src/services`. Un `from src.gui import …` qui s'y glisserait rendrait la
CLI incapable de tourner sur une machine sans affichage — et personne ne s'en
apercevrait avant le premier run à distance.
"""

import ast
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1] / "src"
PERIMETRE = ["services", "concurrency", "cli.py"]
INTERDITS = ("src.gui", "tkinter", "customtkinter")


def _fichiers():
    for nom in PERIMETRE:
        p = RACINE / nom
        if p.is_file():
            yield p
        elif p.is_dir():
            yield from sorted(p.rglob("*.py"))


def _modules_importes(source: str):
    arbre = ast.parse(source)
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            for alias in noeud.names:
                yield alias.name
        elif isinstance(noeud, ast.ImportFrom) and noeud.module:
            yield noeud.module


@pytest.mark.parametrize("fichier", list(_fichiers()), ids=lambda p: str(p.relative_to(RACINE)))
def test_aucun_import_gui(fichier):
    fautifs = [
        m
        for m in _modules_importes(fichier.read_text(encoding="utf-8"))
        if m.split(".")[0] in ("tkinter", "customtkinter") or m.startswith("src.gui")
    ]
    assert not fautifs, f"{fichier.name} importe la GUI : {fautifs}"


def test_le_package_services_est_vide():
    """Y importer un service tirerait tout le pipeline à l'import du package
    (piège de `src/utils/__init__` → DataEnricher)."""
    source = (RACINE / "services" / "__init__.py").read_text(encoding="utf-8")
    assert not list(_modules_importes(source))
