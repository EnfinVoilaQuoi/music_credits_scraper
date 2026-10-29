"""Poser la relation `contient` (medleys, chantier du 2026-10-29).

Le modèle et la règle sont PURS (`utils.contenants`) ; ici, l'écriture : la
relation va dans la colonne `relationships` du contenant par l'écrivain dédié
(`record_relationships`, forme unique, jamais de clé locale), marquée de sa
provenance — `manuel` (décision consignée en table `corrections`, exportée) ou
`regle:titres_joints` — pour survivre à l'alignement des lignes sœurs sur une
page Genius relue (`track_soeurs.avec_relations_manuelles`).

La règle n'est pas branchée sur le cycle : simulée sur toute la base et
montrée d'abord (`scripts/poser_contenants.py --regle`).
"""

from __future__ import annotations

from src.utils import contenants


def relations_avec(track, contenus, artiste: str, origine: str) -> tuple[list, list]:
    """PUR. `(relations de la fiche, contenus ajoutés)` : la relation vers
    chaque contenu qu'elle n'a pas déjà."""
    relations = [dict(r) for r in track.relationships or []]
    deja = set(contenants.ids_contenus(track))
    ajoutes = []
    for c in contenus:
        if c.id in deja or c.id == track.id:
            continue
        relations.append(contenants.relation_vers(c, artiste, origine))
        deja.add(c.id)
        ajoutes.append(c)
    return relations, ajoutes


def lier(dm, track, contenus, artiste: str, origine: str, *, apply: bool) -> list:
    """Lie `track` à ses `contenus` ; rend les contenus ajoutés (à blanc :
    ceux qui le seraient)."""
    relations, ajoutes = relations_avec(track, contenus, artiste, origine)
    if apply and ajoutes:
        dm.record_relationships(track.id, relations)
        track.relationships = relations
    return ajoutes


def propositions(fiches) -> list[tuple[object, list]]:
    """PUR. `[(contenant, contenus)]` que la règle des titres joints propose
    parmi les fiches d'UN artiste, hors liens déjà posés."""
    resultat = []
    for t in fiches:
        contenus = contenants.proposer_contenus(t, fiches)
        if not contenus:
            continue
        deja = set(contenants.ids_contenus(t))
        manquants = [c for c in contenus if c.id not in deja]
        if manquants:
            resultat.append((t, manquants))
    return resultat
