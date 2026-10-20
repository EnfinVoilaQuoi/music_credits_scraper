"""Une base SQLite ne se copie pas fichier par fichier.

Règle « Pièges SQLite » de PROJET.md, mécanisée le 2026-10-20 : la base est en
WAL (`src/persistence/moteur.py`), ses dernières écritures peuvent n'être que
dans le `-wal` — un `shutil.copy*` les laisse derrière. Une base se copie par
l'API de sauvegarde (`sqlite3.Connection.backup`, `DatabaseBackupManager`).

Le crible ne lit que les NOMS : il refuse un `shutil.copy*` dont un argument
parle d'une base (`.db`, `db_path`, `gabarit`, `brut`…). Une copie de base
sous un nom neutre (`chemin`, `fichier`) lui échappe — c'est une garde
partielle. La liste des exceptions est FERMÉE, avec leur raison.
"""

import ast
import re
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DOSSIERS = ("src", "scripts", "tests")

COPIES = {"copy", "copy2", "copyfile", "copyfileobj"}

# Ce qui, dans un argument, désigne une base SQLite.
NOM_DE_BASE = re.compile(
    r"\.db\b|\bdb\b|\bdb_|_db\b|database|gabarit|music_credits|brut", re.IGNORECASE
)

AUTORISES: dict[str, str] = {}


def _copie_shutil(noeud: ast.Call) -> bool:
    f = noeud.func
    if isinstance(f, ast.Attribute) and f.attr in COPIES:
        return isinstance(f.value, ast.Name) and f.value.id == "shutil"
    return isinstance(f, ast.Name) and f.id in COPIES


def _copie_de_base(noeud: ast.Call) -> bool:
    args = [*noeud.args, *(k.value for k in noeud.keywords)]
    return _copie_shutil(noeud) and any(NOM_DE_BASE.search(ast.unparse(a)) for a in args)


def _sites() -> dict[str, list[int]]:
    sites: dict[str, list[int]] = {}
    for dossier in DOSSIERS:
        for chemin in (RACINE / dossier).rglob("*.py"):
            arbre = ast.parse(chemin.read_text(encoding="utf-8"))
            lignes = [
                x.lineno for x in ast.walk(arbre) if isinstance(x, ast.Call) and _copie_de_base(x)
            ]
            if lignes:
                sites[chemin.relative_to(RACINE).as_posix()] = lignes
    return sites


def test_aucune_base_copiee_fichier_par_fichier():
    hors_liste = {f: lignes for f, lignes in _sites().items() if f not in AUTORISES}
    assert not hors_liste, (
        "Base copiée par shutil — passer par l'API de sauvegarde "
        f"(sqlite3.Connection.backup), ou justifier une entrée dans AUTORISES : {hors_liste}"
    )


def test_la_liste_ne_garde_pas_de_mort():
    """Une entrée qui ne copie plus de base sort de la liste (cliquet)."""
    morts = set(AUTORISES) - set(_sites())
    assert not morts, f"Plus aucune copie de base ici, retirer de AUTORISES : {morts}"


def test_le_crible_voit_les_formes_courantes():
    refuses = [
        "shutil.copyfile(_gabarit_base_migree, neuve)",
        "shutil.copy2(self.db_path, cible)",
        "copyfile(src, 'data/music_credits.db')",
        "shutil.copy(BRUT, dest)",
        "shutil.copyfile(src=database, dst=x)",
    ]
    acceptes = [
        "shutil.copy2(FICHIER, copie)",
        "shutil.copy2(RIAA_RAW, _backup())",
        "src.backup(dest)",
        "shutil.copytree(db_dir, x)",
    ]
    appel = lambda code: ast.parse(code).body[0].value  # noqa: E731
    assert all(_copie_de_base(appel(c)) for c in refuses)
    assert not any(_copie_de_base(appel(c)) for c in acceptes)
