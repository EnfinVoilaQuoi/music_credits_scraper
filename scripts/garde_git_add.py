"""Hook Claude Code (PreToolUse, Bash / PowerShell) : pas de `git add` d'un dossier.

Règle de travail « Git » (PROJET.md) : `git status` avant d'indexer, jamais
`git add <dossier>` — le travail d'une AUTRE session (ou d'un autre outil)
posé dans ce dossier partirait dans le commit. La règle était une « leçon
seule » ; elle a été enfreinte le 2026-10-19 (`git add tests/fixtures/cassettes/`,
`git add -A .`), sans dégât ce jour-là, d'où ce hook.

Refusé : un argument qui est un DOSSIER (`.`, `src/`, `tests/fixtures`), les
options qui indexent tout (`-A`, `--all`, `-u`, `--update`,
`--no-ignore-removal`) et les pathspecs magiques de la racine (`:/`,
`:(top)`). Permis : des fichiers nommés, un motif (`*.py`). Une commande
illisible (guillemets déséquilibrés) passe : le hook ne doit jamais bloquer
autre chose que ce qu'il sait lire.

    {"tool_input": {"command": "git add -A"}, "cwd": "..."}  →  refus (JSON)
"""

from __future__ import annotations

import json
import os
import shlex
import sys

#: Options de `git add` qui indexent tout l'arbre.
_OPTIONS_GLOBALES = {"-A", "--all", "-u", "--update", "--no-ignore-removal"}
#: Options de `git add` suivies d'une valeur (à ne pas lire comme un chemin).
_OPTIONS_A_VALEUR = {"--chmod", "--pathspec-from-file"}
#: Options de `git` (avant la sous-commande) suivies d'une valeur.
_OPTIONS_GIT_A_VALEUR = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
_SEPARATEURS = {";", "&&", "||", "|", "&", "(", ")"}
_CHANGEMENTS_DE_DOSSIER = {"cd", "pushd", "set-location", "sl"}


def _segments(commande: str) -> list[list[str]]:
    """Les commandes simples d'une ligne shell, en jetons. Le corps d'un
    heredoc (message de commit) n'est pas du shell : coupé au premier `<<`."""
    commande = commande.split("<<", 1)[0]
    lexer = shlex.shlex(commande, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    segments, courant = [], []
    for jeton in lexer:
        if jeton in _SEPARATEURS or set(jeton) <= set(";&|()"):
            if courant:
                segments.append(courant)
            courant = []
        else:
            courant.append(jeton)
    if courant:
        segments.append(courant)
    return segments


def _chemins_de_git_add(jetons: list[str], cwd: str) -> tuple[list[str], str] | None:
    """Pour `git [-C d] add …` : (arguments de `add`, dossier où git tourne)."""
    if not jetons or os.path.basename(jetons[0]).lower() not in ("git", "git.exe"):
        return None
    i, ici = 1, cwd
    while i < len(jetons) and jetons[i].startswith("-"):
        if jetons[i] == "-C" and i + 1 < len(jetons):
            ici = os.path.join(ici, jetons[i + 1])
        i += 2 if jetons[i] in _OPTIONS_GIT_A_VALEUR else 1
    if i >= len(jetons) or jetons[i] != "add":
        return None
    return jetons[i + 1 :], ici


def refus(commande: str, cwd: str) -> str | None:
    """PUR (hors `os.path.isdir`). Le motif du refus, ou None si la commande
    n'indexe aucun dossier."""
    try:
        segments = _segments(commande or "")
    except ValueError:
        return None
    ici = cwd
    for jetons in segments:
        if jetons[0].lower() in _CHANGEMENTS_DE_DOSSIER and len(jetons) > 1:
            ici = os.path.join(ici, jetons[-1])
            continue
        trouve = _chemins_de_git_add(jetons, ici)
        if trouve is None:
            continue
        arguments, dossier = trouve
        apres_tirets, sauter = False, False
        for arg in arguments:
            if sauter:
                sauter = False
                continue
            if not apres_tirets and arg == "--":
                apres_tirets = True
                continue
            if not apres_tirets and arg.startswith("-"):
                if arg in _OPTIONS_GLOBALES:
                    return f"`git add {arg}` indexe tout l'arbre"
                sauter = arg in _OPTIONS_A_VALEUR
                continue
            if arg in (":/", ":(top)") or arg.startswith(":/"):
                return f"`git add {arg}` indexe depuis la racine"
            if arg.startswith(":"):
                continue  # exclusion `:!fichier` : ne désigne rien à indexer
            if os.path.isdir(os.path.join(dossier, arg)):
                return f"`git add {arg}` indexe un DOSSIER"
    return None


def main() -> int:
    # Claude Code lit la réponse en UTF-8 ; la console Windows écrirait en cp1252.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        entree = json.load(sys.stdin)
    except ValueError:
        return 0
    commande = (entree.get("tool_input") or {}).get("command") or ""
    motif = refus(commande, entree.get("cwd") or os.getcwd())
    if motif:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"{motif} — règle « Git » de PROJET.md : `git status`, puis "
                        "indexer les fichiers un par un (le travail d'une autre "
                        "session passerait dans le commit)."
                    ),
                }
            },
            sys.stdout,
            ensure_ascii=False,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
