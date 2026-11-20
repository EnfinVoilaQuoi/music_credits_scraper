"""Année de sortie des morceaux que vise une relation Genius (sample, reprise…).

Genius la sert dans chaque morceau de `song_relationships`
(`release_date_components.year`) : `GeniusAPI._extract_relationships` la garde
sous la clé `year`. Les relations enregistrées avant (2026-11-20) ne l'ont pas,
et la relecture d'une page ne les réécrit pas (le producteur est ADDITIF) :
`dater_relations` complète les relations déjà posées, sans rien changer d'autre.
Le page « Samples / Reprises » de l'Analyse du studio l'affiche.
"""

from __future__ import annotations

from typing import Any


def annee_de(morceau: dict) -> int | None:
    """PUR. L'année de sortie d'un morceau de l'API Genius, ou None."""
    composantes = morceau.get("release_date_components") or {}
    annee = composantes.get("year") if isinstance(composantes, dict) else None
    return annee if isinstance(annee, int) and annee > 0 else None


def dater_relations(posees: list[dict[str, Any]] | None, lues: list[dict[str, Any]]) -> list | None:
    """PUR. Les relations `posees` dont l'année manque, complétée par celle de la
    relation `lues` de même adresse Genius. Rend la NOUVELLE liste si au moins une
    année a été ajoutée, sinon None. Une année déjà posée n'est jamais remplacée,
    une relation sans adresse (posée à la main) n'est pas touchée."""
    annees = {r.get("url"): r["year"] for r in lues if r.get("url") and r.get("year")}
    if not posees or not annees:
        return None
    sortie, ajout = [], False
    for r in posees:
        if isinstance(r, dict) and not r.get("year") and r.get("url") in annees:
            r = {**r, "year": annees[r["url"]]}
            ajout = True
        sortie.append(r)
    return sortie if ajout else None
