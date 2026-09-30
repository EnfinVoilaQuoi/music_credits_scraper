"""Une brique de bibliothèque journalise, elle n'imprime pas (lot 4 des journaux,
2026-09-30).

`getsongbpm_api` signalait « API Key invalide ou expirée » par `print` : une clé
morte ne laissait AUCUNE trace dans les journaux, et l'avertissement se perdait
dans la console au milieu du reste. Seul un SCRIPT (un module doté d'un `main()`
ou d'un bloc `__main__`, dont la sortie est justement ce qu'on lit — les mises à
jour de certifs, relayées ligne à ligne par la fenêtre) a le droit d'imprimer.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def _est_un_script(arbre: ast.Module) -> bool:
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.FunctionDef) and noeud.name == "main":
            return True
        if isinstance(noeud, ast.If) and "__main__" in ast.unparse(noeud.test):
            return True
    return False


def _prints_de_bibliotheque() -> list[str]:
    fautifs = []
    for chemin in SRC.rglob("*.py"):
        arbre = ast.parse(chemin.read_text(encoding="utf-8"), filename=str(chemin))
        if _est_un_script(arbre):
            continue
        for noeud in ast.walk(arbre):
            if (
                isinstance(noeud, ast.Call)
                and isinstance(noeud.func, ast.Name)
                and noeud.func.id == "print"
            ):
                fautifs.append(f"{chemin.relative_to(SRC).as_posix()}:{noeud.lineno}")
    return fautifs


def test_aucun_print_hors_des_scripts():
    fautifs = _prints_de_bibliotheque()
    assert fautifs == [], f"print() dans du code de bibliothèque (passer au logger) : {fautifs}"


def test_le_crible_reconnait_un_script():
    """Garde contre le crible muet : les scripts de certifs impriment, et
    doivent être reconnus comme tels (sinon le test ci-dessus ne verrait rien
    pour de mauvaises raisons ou crierait pour de mauvaises raisons)."""
    arbre = ast.parse((SRC / "utils" / "update_bpi.py").read_text(encoding="utf-8"))
    assert _est_un_script(arbre)
    arbre = ast.parse((SRC / "api" / "getsongbpm_api.py").read_text(encoding="utf-8"))
    assert not _est_un_script(arbre)
