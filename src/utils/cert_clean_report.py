"""Rapport de nettoyage d'un CSV de certifications — rendu COMMUN aux 3 sources.

Le nettoyeur SNEP rendait un compte-rendu détaillé (ce qui a été re-cassé,
dédoublonné, retiré, avec des exemples) tandis que ceux de BRMA et RIAA se
contentaient d'une ligne : « X → Y lignes ». À l'usage, la différence n'est pas
cosmétique — sans le détail, on ne peut pas juger si un nettoyage a fait ce
qu'il fallait, et l'utilisateur doit valider à l'aveugle une opération qui
réécrit un fichier de plusieurs dizaines de milliers de lignes.

Ce module ne contient QUE la mise en forme, partagée par les quatre sources : un
second formateur « du même genre mais pas tout à fait » finirait par diverger,
et c'est précisément l'écart qu'on répare ici. Chaque nettoyeur reste maître de
CE qu'il compte ; il décrit son rapport en trois listes (compteurs, sections de
détail, exemples) et le rendu est le même partout.

Convention de rapport (clés communes) :
    path      chemin du fichier nettoyé
    applied   False = DRY-RUN (rien n'a été écrit)
    backup    chemin du backup, quand il y en a un
    rows_in   / rows_out    lignes avant / après
    deja_propre  True = le résultat est IDENTIQUE au fichier en place
    lignes_modifiees  nombre de lignes qui changeraient
    error     message si le nettoyage n'a pas pu avoir lieu
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

_LARGEUR = 52

#: Au-delà, une section de détail est tronquée. Ces listes décrivent ce que la
#: DÉRIVATION fait depuis le brut ; elles sont donc identiques à chaque
#: nettoyage et n'ont pas à occuper trente lignes.
_MAX_DETAIL = 8


def rapport_vierge(path: str) -> dict:
    """Squelette commun d'un rapport de nettoyage."""
    return {
        "path": str(path),
        "applied": False,
        "backup": None,
        "rows_in": 0,
        "rows_out": 0,
        "deja_propre": False,
        "lignes_modifiees": 0,
    }


def comparer_au_fichier(derive, chemin) -> tuple[bool, int]:
    """(le résultat est-il identique au fichier en place ?, lignes qui changent).

    C'est la seule question à laquelle l'utilisateur veut une réponse avant
    d'accepter une réécriture. Le reste du rapport décrit la DÉRIVATION depuis
    le brut : par construction, elle rapporte les mêmes transformations à chaque
    exécution, ce qui ne dit rien de l'utilité de celle-ci.
    """
    import pandas as pd

    chemin = Path(chemin)
    if not chemin.exists():
        return False, len(derive)
    try:
        actuel = pd.read_csv(chemin, encoding="utf-8-sig", dtype=str).fillna("")
    except (OSError, ValueError):
        return False, len(derive)

    gauche = derive.astype(str).fillna("").reset_index(drop=True)
    droite = actuel.astype(str).fillna("").reset_index(drop=True)
    if list(gauche.columns) != list(droite.columns) or len(gauche) != len(droite):
        return False, abs(len(gauche) - len(droite)) or len(gauche)
    differentes = int((gauche.values != droite.values).any(axis=1).sum())
    return differentes == 0, differentes


def render(
    report: Mapping,
    *,
    titre: str,
    counters: Iterable[tuple[str, int]] = (),
    sections: Iterable[tuple[str, Mapping[str, int]]] = (),
    examples: Iterable[tuple[str, Iterable[str]]] = (),
    note_dry_run: str = "",
) -> str:
    """Rend le rapport en texte.

    `counters` : bilan chiffré, une puce par ligne (les libellés sont alignés).
    `sections` : détails « quoi → quoi ×combien » (niveaux normalisés…).
    `examples` : échantillons de lignes touchées, pour l'œil humain.
    """
    lignes = ["=" * _LARGEUR]
    etat = "  (APPLIQUÉ)" if report.get("applied") else "  (DRY-RUN)"
    lignes.append(titre + etat)
    lignes.append("=" * _LARGEUR)

    if report.get("error"):
        lignes.append(f"❌ {report['error']}")
        return "\n".join(lignes)

    lignes.append(f"Fichier : {report['path']}")
    if report.get("backup"):
        lignes.append(f"Backup  : {report['backup']}")
    entrees, sorties = report.get("rows_in", 0), report.get("rows_out", 0)
    lignes.append(f"Lignes : {entrees} → {sorties} ({sorties - entrees:+d})")

    # LE verdict : le nettoyage change-t-il quelque chose au fichier en place ?
    # Sans lui, le rapport détaille ce que la dérivation fait depuis le brut —
    # toujours la même chose, à chaque exécution — sans jamais dire si le
    # fichier, lui, en sort différent.
    if report.get("deja_propre"):
        lignes.append("")
        lignes.append("✅ Le fichier est DÉJÀ à jour : ce nettoyage ne changerait rien.")
        lignes.append("   (le détail ci-dessous décrit ce que la dérivation depuis le brut")
        lignes.append("    applique à chaque fois — ce ne sont pas des changements en attente)")
    elif report.get("lignes_modifiees"):
        lignes.append("")
        lignes.append(f"⚠️ {report['lignes_modifiees']} ligne(s) du fichier seraient modifiées.")

    compteurs = list(counters)
    if compteurs:
        largeur = max(len(libelle) for libelle, _ in compteurs)
        lignes.append("")
        for libelle, valeur in compteurs:
            lignes.append(f"  • {libelle.ljust(largeur)} : {valeur}")

    for titre_section, detail in sections:
        if not detail:
            continue
        lignes.append("")
        classes = sorted(detail.items(), key=lambda kv: -kv[1])
        lignes.append(f"── {titre_section} ──")
        for cle, n in classes[:_MAX_DETAIL]:
            lignes.append(f"  • {cle}  ×{n}")
        if len(classes) > _MAX_DETAIL:
            reste = sum(n for _, n in classes[_MAX_DETAIL:])
            lignes.append(
                f"  … et {len(classes) - _MAX_DETAIL} autre(s) forme(s), {reste} ligne(s)"
            )

    for titre_exemple, echantillon in examples:
        echantillon = list(echantillon)
        if not echantillon:
            continue
        lignes.append("")
        lignes.append(f"── {titre_exemple} ──")
        for ex in echantillon:
            lignes.append(f"  • {ex}")

    if not report.get("applied") and note_dry_run:
        lignes.append("")
        lignes.append(note_dry_run)
    lignes.append("=" * _LARGEUR)
    return "\n".join(lignes)
