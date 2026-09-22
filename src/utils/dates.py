"""Parseur de date unique et permissif.

Remplace les implémentations dupliquées qui traînaient dans `Track`
(`update_release_date`, `calculate_certification_duration`) et dans la
présentation des certifications. Les dates du projet arrivent sous des formes
hétérogènes : objets `datetime`, ISO avec heure/`Z` (`2020-05-01T12:30:00Z`),
date simple (`2020-05-01`), ou variantes avec heure séparée par un espace.
"""

import re
from datetime import datetime

#: Rangs de précision d'une date de sortie. Une date PLUS PRÉCISE l'emporte
#: sur une moins précise, quelle que soit la source (règle du 2026-09-22) :
#: Genius fabrique `datetime(année, 1, 1)` dès qu'il n'a que l'année, et cette
#: date-là ne doit pas battre le « 2018-05-02 » de Deezer.
ANNEE, MOIS, JOUR = 1, 2, 3

_ANNEE_RE = re.compile(r"^\d{4}$")
_MOIS_RE = re.compile(r"^\d{4}-\d{2}$")


def parse_flexible(value) -> datetime | None:
    """Convertit une valeur en `datetime`, ou `None` si non interprétable.

    - `None` / chaîne vide → `None`
    - `datetime` → renvoyé tel quel
    - chaîne ISO (`YYYY-MM-DD`, avec `T`/espace + heure, suffixe `Z`) → parsée
    - tout autre type ou chaîne illisible → `None`

    Un `Z` final est traité comme `+00:00` (le datetime résultant est alors
    *timezone-aware*, comme l'ancienne logique de `update_release_date`).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None

    value = value.strip()
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass

    # Dernier recours : les 10 premiers caractères en YYYY-MM-DD
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError:
        return None


def precision(valeur) -> int | None:
    """`ANNEE` / `MOIS` / `JOUR`, ou None si illisible.

    ⚠️ **Un `datetime` ne porte PAS sa précision** : il vaut toujours `JOUR`.
    C'est au PRODUCTEUR de déclarer « année seule » en écrivant `"2018"` dans
    son observation — aucun résolveur ne peut le deviner après coup, et c'est
    pourquoi les 719 dates déjà en base au 1ᵉʳ janvier ne sont pas retronquées.
    """
    if valeur is None:
        return None
    if isinstance(valeur, datetime):
        return JOUR
    if not isinstance(valeur, str):
        return None
    texte = valeur.strip()
    if _ANNEE_RE.match(texte):
        return ANNEE
    if _MOIS_RE.match(texte):
        return MOIS
    return JOUR if parse_flexible(texte) else None


def normaliser_observation(valeur, prec: int | None = None) -> str | None:
    """Forme canonique d'une observation de date : `"2018"`, `"2018-05"` ou
    `"2018-05-14"`. C'est la FORME qui porte la précision — pas une colonne de
    plus, pas un champ sur `Observation`."""
    if valeur is None:
        return None
    prec = prec or precision(valeur)
    if prec is None:
        return None
    if isinstance(valeur, datetime):
        texte = valeur.strftime("%Y-%m-%d")
    else:
        texte = str(valeur).strip()
        if prec == JOUR:
            complet = parse_flexible(texte)
            if complet is None:
                return None
            texte = complet.strftime("%Y-%m-%d")
    return {ANNEE: texte[:4], MOIS: texte[:7], JOUR: texte[:10]}[prec]


def completer(valeur) -> str | None:
    """Toujours `"YYYY-MM-DD"` — la COLONNE `tracks.release_date` reste une
    date complète : la GUI, le tri, `albums_grouping`, `artist_loader` (`[:4]`)
    et la Timeline du dépôt privé la lisent ainsi."""
    prec = precision(valeur)
    if prec is None:
        return None
    canonique = normaliser_observation(valeur, prec)
    if canonique is None:
        return None
    return {ANNEE: canonique + "-01-01", MOIS: canonique + "-01", JOUR: canonique}[prec]


def meme_jour(a, b) -> bool:
    """Deux dates désignent-elles le même jour ? Tolérant aux précisions et aux
    formats (`datetime`, `"2018-05-14"`, `"2018-05-14 00:00:00"`)."""
    ca, cb = completer(a), completer(b)
    return bool(ca) and ca == cb


def la_plus_ancienne(a, b) -> str | None:
    """La plus ANCIENNE des deux — la règle demandée pour Deezer, dont chaque
    ÉDITION porte sa date (la colonne du morceau doit garder celle de
    l'ENREGISTREMENT, donc la première).

    ⚠️ **Une date moins précise n'est pas une date antérieure.** « 2018 » et
    « 2018-05-14 » ne sont pas deux sorties dont la première serait au 1ᵉʳ
    janvier : la seconde RAFFINE la première, elles désignent la même. Comparer
    leurs formes complétées ferait gagner « 2018 » et perdrait le jour. Quand
    l'une est un préfixe de l'autre, c'est donc la plus PRÉCISE qui l'emporte ;
    l'antériorité ne tranche qu'entre des périodes DISJOINTES (2017 vs 2018-05).
    """
    na, nb = normaliser_observation(a), normaliser_observation(b)
    if na is None:
        return nb
    if nb is None:
        return na
    if na.startswith(nb) or nb.startswith(na):
        return na if len(na) >= len(nb) else nb
    return na if completer(na) <= completer(nb) else nb
