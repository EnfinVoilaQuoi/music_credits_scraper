"""Forme d'une parution : un single n'est pas l'album du même titre.

Le titre seul ne fait pas l'édition. Cas d'origine (2026-11-13) : la parution
de l'album Genius *Vinyl Days* de Logic (28 pistes) avait adopté, au seul titre,
le single Deezer « Vinyl Days » (1 piste) — ID Deezer, code-barres et type
« single » compris. 9 parutions ainsi fondues, dont 6 dont le vrai album
Deezer existait à côté, en double.

Un seul juge, partagé par la pose (`TrackRepository._parution_compatible`),
l'audit (famille `single_fondu`) et la réparation
(`scripts/separer_singles_fondus.py`).
"""

from __future__ import annotations

#: Au-delà, ce n'est plus la forme d'un single (titre, instrumental, remix…).
PISTES_SINGLE_MAX = 3

#: Types Deezer d'une édition à plusieurs morceaux.
TYPES_LONGS = frozenset({"album", "ep", "compile"})


def _type(valeur: str | None) -> str | None:
    return str(valeur).strip().lower() or None if valeur else None


def forme_de_single(record_type: str | None, nb_pistes: int | None) -> bool:
    """L'édition observée a la forme d'un single : déclarée single, ou d'au
    plus `PISTES_SINGLE_MAX` pistes."""
    if _type(record_type) == "single":
        return True
    return nb_pistes is not None and 0 < nb_pistes <= PISTES_SINGLE_MAX


def parution_compatible(
    type_connu: str | None,
    pistes_connues: int,
    type_observe: str | None,
    nb_observe: int | None,
) -> bool:
    """La parution connue (son type, ses pistes liées) peut-elle être
    l'édition observée (son type, son nombre de pistes) ?

    Refusé : une édition à la forme de single face à une parution qui a déjà
    plus de `PISTES_SINGLE_MAX` pistes, et plus que l'édition n'en compte ;
    un single face à un album / EP déclaré, et l'inverse. Une édition plus
    longue que la parution connue reste admise (deluxe, pistes bonus : la
    page Genius n'a pas toujours toutes les pistes)."""
    type_connu, type_observe = _type(type_connu), _type(type_observe)
    if type_observe == "single" and type_connu in TYPES_LONGS:
        return False
    if type_connu == "single" and type_observe in TYPES_LONGS:
        return False
    if forme_de_single(type_observe, nb_observe):
        plafond = max(PISTES_SINGLE_MAX, nb_observe or 0)
        if pistes_connues > plafond:
            return False
    return True


def single_fondu(record_type: str | None, pistes_liees: int) -> bool:
    """Forme d'audit : une parution déclarée single qui porte plus de
    `PISTES_SINGLE_MAX` pistes — un single fondu avec l'album de son titre."""
    return _type(record_type) == "single" and pistes_liees > PISTES_SINGLE_MAX
