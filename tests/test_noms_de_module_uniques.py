"""Crible : un nom de module (constante, fonction, classe) n'est défini qu'UNE
fois au premier niveau d'un module de `src/`.

2026-11-24 : un `_JOKER` ajouté plus bas dans `version_descriptors` remplaçait
celui des titres censurés (« Motherf***** ») sans la moindre erreur ; le crible,
posé le lendemain, a trouvé un second cas, `_GROUPE`, dont la première
définition ne servait jamais. Une redéfinition voulue se renomme.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def _noms_definis(arbre: ast.Module) -> dict[str, list[int]]:
    vus: dict[str, list[int]] = {}
    for n in arbre.body:
        noms = []
        if isinstance(n, ast.Assign):
            noms = [c.id for c in n.targets if isinstance(c, ast.Name)]
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value:
            noms = [n.target.id]
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            noms = [n.name]
        for nom in noms:
            vus.setdefault(nom, []).append(n.lineno)
    return vus


def test_un_nom_de_module_n_est_defini_qu_une_fois():
    doublons = []
    for chemin in sorted(SRC.rglob("*.py")):
        arbre = ast.parse(chemin.read_text(encoding="utf-8"))
        for nom, lignes in _noms_definis(arbre).items():
            if len(lignes) > 1:
                doublons.append(f"{chemin.relative_to(SRC.parent)} : {nom} lignes {lignes}")
    assert not doublons, "\n".join(doublons)


def test_le_crible_voit_une_redefinition():
    arbre = ast.parse("_X = 1\n\ndef f():\n    return _X\n\n_X = 2\n")
    assert _noms_definis(arbre)["_X"] == [1, 6]
