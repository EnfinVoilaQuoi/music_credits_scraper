"""Le code-barres d'une ÉDITION, écrit pareil par toutes les sources (2026-10-14).

Le même code s'écrit de plusieurs façons : Deezer rend un UPC-A à 12 chiffres
(« 859733291991 », *AVLD* de SDM), Discogs et MusicBrainz l'écrivent souvent en
EAN-13 avec le 0 de tête (« 0859733291991 ») et Discogs l'imprime avec des
espaces (« 3 596974 281424 »). La CLÉ retire tout sauf les chiffres, puis les
zéros de tête : deux écritures du même code ont la même clé.

Module PUR : aucune source, aucun réseau.
"""

from __future__ import annotations

import re

_NON_CHIFFRE = re.compile(r"\D")


def cle_upc(code: str | int | None) -> str | None:
    """La clé de comparaison d'un code-barres, ou None s'il n'en est pas un.

    UPC-E (8), UPC-A (12), EAN-13, GTIN-14 : de 8 à 14 chiffres une fois les
    espaces et tirets retirés. Le reste (texte, numéro de catalogue) n'est pas
    un code-barres."""
    chiffres = _NON_CHIFFRE.sub("", str(code or ""))
    if not 8 <= len(chiffres) <= 14:
        return None
    return chiffres.lstrip("0") or None


def code_barres_discogs(identifiants: list[dict] | None) -> str | None:
    """Le code-barres d'un disque Discogs (`release.identifiers`), ou None.

    Un disque l'écrit souvent deux fois, « Printed » et « Scanned »
    (*Civilisation Édition Ultime* : « 3 596974 281424 » et « 3596974281424 »)
    — une seule clé. Deux codes DIFFÉRENTS (coffret et disque, deux pressages
    notés ensemble) : on ne sait pas lequel est l'édition, None."""
    cles = {
        cle
        for i in identifiants or []
        if isinstance(i, dict) and str(i.get("type") or "").casefold() == "barcode"
        if (cle := cle_upc(i.get("value")))
    }
    return cles.pop() if len(cles) == 1 else None
