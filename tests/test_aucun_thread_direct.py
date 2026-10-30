"""Aucun `threading.Thread` (ni `Timer`) créé hors des points prévus.

Règle « Concurrence » de PROJET.md, mécanisée le 2026-10-12 : un fil créé à la
main n'est ni enregistré ni joint à la fermeture (`lifecycle.shutdown_workers`)
— l'app se fige en quittant, ou un fil survit à la boucle qu'il appelle. Le
travail de fond passe par `lifecycle.run_worker` (ce qui touche la boucle) ou
`lifecycle.start_worker` (le reste).

La liste ci-dessous est FERMÉE, avec la raison de chaque entrée : une nouvelle
création de fil fait rougir ce test, et l'ajouter ici est une décision.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

AUTORISES = {
    # L'infrastructure elle-même : la boucle unique, le registre des workers,
    # le fil sériel des ressources liées à un thread.
    "concurrency/async_loop.py": "le fil de LA boucle asyncio",
    "concurrency/lifecycle.py": "start_worker / run_worker : fils enregistrés",
    "concurrency/serial_worker.py": "fil dédié aux ressources thread-locales",
    # Écrivain de l'observabilité : le vidage part parfois DE la boucle, qui
    # écrit déjà dans la même base — un fil dédié évite qu'elle se bloque.
    "observability/repository.py": "BackgroundWriter (daemon, documenté)",
}


def _cree_un_fil(noeud: ast.Call) -> bool:
    f = noeud.func
    if isinstance(f, ast.Attribute) and f.attr in ("Thread", "Timer"):
        return isinstance(f.value, ast.Name) and f.value.id == "threading"
    return isinstance(f, ast.Name) and f.id in ("Thread", "Timer")


def _sites() -> dict[str, int]:
    sites: dict[str, int] = {}
    for chemin in SRC.rglob("*.py"):
        arbre = ast.parse(chemin.read_text(encoding="utf-8"))
        n = sum(1 for x in ast.walk(arbre) if isinstance(x, ast.Call) and _cree_un_fil(x))
        if n:
            sites[chemin.relative_to(SRC).as_posix()] = n
    return sites


def test_aucun_fil_hors_des_points_prevus():
    hors_liste = {f: n for f, n in _sites().items() if f not in AUTORISES}
    assert not hors_liste, (
        "Fil créé à la main — passer par lifecycle.run_worker / start_worker "
        f"(ou justifier une entrée dans AUTORISES) : {hors_liste}"
    )


def test_la_liste_ne_garde_pas_de_mort():
    """Une entrée qui ne crée plus de fil sort de la liste (cliquet)."""
    morts = set(AUTORISES) - set(_sites())
    assert not morts, f"Plus aucun fil créé ici, retirer de AUTORISES : {morts}"


def test_le_crible_voit_les_deux_formes():
    code = "import threading\nfrom threading import Thread\nthreading.Thread()\nThread()\n"
    appels = [x for x in ast.walk(ast.parse(code)) if isinstance(x, ast.Call)]
    assert all(_cree_un_fil(a) for a in appels) and len(appels) == 2
