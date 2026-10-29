"""Garde STRUCTURELLE de la règle « un ID retiré à la main se REFUSE ».

Un ID Spotify ou Deezer retiré sans refus revient au run suivant : l'identité
le cherche de nouveau, trouve le même, et le repose. « Forever » de Lil Wayne
a perdu le 27/10 l'ID du « Forever » de Drake, retiré par un script sans
refus ; l'identité du 28 l'a reposé. Les deux boutons « rejeter » de l'IHM,
`repair_spotify_ids`, `repair_deezer_ids` et `deplacer_ids_spotify` faisaient
de même (corrigés le 2026-10-29).

Ce crible interdit le MOTIF, dans `src/` et `scripts/` : une fonction qui
appelle une primitive de retrait appelle aussi, dans son propre corps, le
refus du même catalogue — sauf une liste FERMÉE d'exceptions, chacune avec
sa raison. Les fonctions imbriquées sont jugées à part (un bouton défini dans
une méthode est une fonction à lui seul).
"""

import ast
from pathlib import Path

_RACINE = Path(__file__).resolve().parents[1]

#: primitive de retrait → le refus qui doit l'accompagner.
_REFUS_ATTENDU = {
    "clear_track_spotify_id": "memoriser_id_refuse",
    "rejeter_spotify_id": "memoriser_id_refuse",
    "clear_track_deezer_id": "memoriser_deezer_refuse",
}

#: (fichier, fonction) qui retirent un ID SANS le refuser. Liste FERMÉE : une
#: entrée nouvelle dit pourquoi l'ID ne doit pas revenir refusé.
EXCEPTIONS: dict[tuple[str, str], str] = {
    ("src/utils/spotify_audit.py", "rejeter_spotify_id"): (
        "la primitive elle-même : ses appelants sont jugés"
    ),
    ("src/services/revue_auto.py", "retablir"): (
        "ANNULE la pose d'un ID par une règle (`pose_spotify`) : rien n'a été "
        "jugé faux, l'ID peut revenir"
    ),
    ("scripts/ponctuels/make_her_say_originale.py", "reparer"): (
        "l'ID retiré devient une ÉDITION de la même fiche (gardé en "
        "`track_spotify_ids`) : le refuser l'interdirait"
    ),
}


def _nom(appel: ast.Call) -> str | None:
    f = appel.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _appels_propres(fonction: ast.AST) -> set[str]:
    """Les noms appelés dans le corps de `fonction`, hors fonctions imbriquées."""
    noms: set[str] = set()
    pile = list(ast.iter_child_nodes(fonction))
    while pile:
        noeud = pile.pop()
        if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(noeud, ast.Call) and (nom := _nom(noeud)):
            noms.add(nom)
        pile.extend(ast.iter_child_nodes(noeud))
    return noms


def _fichiers():
    for dossier in ("src", "scripts"):
        yield from sorted((_RACINE / dossier).rglob("*.py"))


def _retraits_sans_refus() -> dict[tuple[str, str], list[str]]:
    trouves: dict[tuple[str, str], list[str]] = {}
    for chemin in _fichiers():
        relatif = chemin.relative_to(_RACINE).as_posix()
        arbre = ast.parse(chemin.read_text(encoding="utf-8"))
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            appels = _appels_propres(noeud)
            manques = sorted(
                f"{retrait} sans {refus}"
                for retrait, refus in _REFUS_ATTENDU.items()
                if retrait in appels and refus not in appels
            )
            if manques:
                trouves[(relatif, noeud.name)] = manques
    return trouves


def test_un_id_retire_est_refuse():
    fautifs = {cle: m for cle, m in _retraits_sans_refus().items() if cle not in EXCEPTIONS}
    assert not fautifs, (
        "Ces fonctions retirent un ID sans le refuser : la prochaine identité le "
        "reposera. Appeler le refus du même catalogue dans la même fonction, ou "
        "ajouter une exception COMMENTÉE :\n  "
        + "\n  ".join(f"{f}::{n} — {', '.join(m)}" for (f, n), m in sorted(fautifs.items()))
    )


def test_les_exceptions_servent_encore():
    """Une exception qui ne couvre plus rien se retire : la liste reste fermée."""
    perimees = set(EXCEPTIONS) - set(_retraits_sans_refus())
    assert not perimees, f"Exceptions qui ne servent plus : {sorted(perimees)}"
