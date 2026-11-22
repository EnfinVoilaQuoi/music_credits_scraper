"""Crible : toute fusion faite par `src/` se consigne (`memoriser_fusion`).

Règle du 2026-11-19 (« une fusion passe par `fiches.json` ») : non consignée,
une fusion n'est ni exportée (`notes/scraper/decisions/corrections.json`) ni lue
par les juges des fusions (`genius_ids_absorbes`, `titres_absorbes`).
`editions.absorber` ne la consignait pas : 130 fusions d'édition sans ligne
`fusionner_dans` le 2026-11-22. Une fonction de `src/` qui appelle
`merge_tracks` appelle aussi `memoriser_fusion`, sauf la liste FERMÉE
ci-dessous.
"""

import ast
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]

#: (fichier, fonction) → raison.
EXCEPTIONS = {
    # CTk gelée hors correctifs : note la suppression (`add_deleted`), pas la
    # fusion — à rebrancher (CORRECTIFS).
    ("src/gui/dialogs/merge_tracks.py", "_do_merge"): "CTk gelée",
}


def _fusions_sans_consignation():
    trouves = set()
    for fichier in (RACINE / "src").rglob("*.py"):
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        relatif = fichier.relative_to(RACINE).as_posix()
        for fonction in ast.walk(arbre):
            if not isinstance(fonction, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            appels = {
                c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")
                for c in ast.walk(fonction)
                if isinstance(c, ast.Call)
            }
            if "merge_tracks" in appels and "memoriser_fusion" not in appels:
                trouves.add((relatif, fonction.name))
    return trouves


def test_toute_fusion_de_src_se_consigne():
    reste = _fusions_sans_consignation() - set(EXCEPTIONS)
    assert not reste, f"Fusions sans `memoriser_fusion` : {sorted(reste)}"


def test_les_exceptions_sont_vivantes():
    morts = set(EXCEPTIONS) - _fusions_sans_consignation()
    assert not morts, f"Exceptions sans objet : {sorted(morts)}"
