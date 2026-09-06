"""Rapport de nettoyage d'un CSV de certifications — rendu COMMUN aux 3 sources.

Le nettoyeur SNEP rendait un compte-rendu détaillé (ce qui a été re-cassé,
dédoublonné, retiré, avec des exemples) tandis que ceux de BRMA et RIAA se
contentaient d'une ligne : « X → Y lignes ». À l'usage, la différence n'est pas
cosmétique — sans le détail, on ne peut pas juger si un nettoyage a fait ce
qu'il fallait, et l'utilisateur doit valider à l'aveugle une opération qui
réécrit un fichier de plusieurs dizaines de milliers de lignes.

Ce module ne contient QUE la mise en forme, partagée par les trois sources : un
second formateur « du même genre mais pas tout à fait » finirait par diverger,
et c'est précisément l'écart qu'on répare ici. Chaque nettoyeur reste maître de
CE qu'il compte ; il décrit son rapport en trois listes (compteurs, sections de
détail, exemples) et le rendu est le même partout.

Convention de rapport (clés communes) :
    path      chemin du fichier nettoyé
    applied   False = DRY-RUN (rien n'a été écrit)
    backup    chemin du backup, quand il y en a un
    rows_in   / rows_out    lignes avant / après
    error     message si le nettoyage n'a pas pu avoir lieu
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

_LARGEUR = 52


def rapport_vierge(path: str) -> dict:
    """Squelette commun d'un rapport de nettoyage."""
    return {
        "path": str(path),
        "applied": False,
        "backup": None,
        "rows_in": 0,
        "rows_out": 0,
    }


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
        lignes.append(f"── {titre_section} ──")
        for cle, n in sorted(detail.items(), key=lambda kv: -kv[1]):
            lignes.append(f"  • {cle}  ×{n}")

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
