"""Deux fiches, deux ID Spotify, UN compteur : Spotify cumule les écoutes.

Constats utilisateur du 2026-10-27 : Spotify montre le même compteur pour les
parutions d'un même enregistrement (« Coldest Winter » sur l'album et sur le
best-of), et parfois pour une version proche (« The Corner » et son
instrumental, « Keep My Spirit Alive » et « pt 2 », 98 % de paroles communes
mais deux ISRC). Les deux ID sont justes ; seul le compteur, lu sur chacun,
entre deux fois dans les totaux de l'artiste.

Deux lectures prises à des dates différentes ne sont pas égales : on juge la
TRAJECTOIRE. La lecture de l'une, à sa date, doit tomber sur la courbe de
l'autre au rythme quotidien que donne Kworb (± 1,5 jour : Spotify compte par
paliers) ; sans rythme connu, il faut l'égalité exacte à moins de 2 jours.
Garde contre le hasard : les titres sont parents (le titre principal de
l'un, mots entiers, dans celui de l'autre) et le compteur est au-dessus du
plancher Kworb.

Le compteur va ensuite à UNE fiche, par ordre (décision utilisateur
2026-10-27) : celle que Kworb a rattachée (il ne liste qu'un ID par compteur
partagé), puis la page principale (titre de l'original, avec paroles, première
parution). Module PUR.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime

from src.utils.title_matching import contains_as_words, normalize_title
from src.utils.version_descriptors import Kind, parse_variant

#: Spotify ne met pas ses compteurs à jour en continu : un écart d'un jour et
#: demi d'écoutes entre deux lectures reste « la même courbe ».
TOLERANCE_JOURS = 1.5
#: Sans rythme quotidien connu : égalité exacte, lue à moins de ce délai.
DELAI_EGALITE_JOURS = 2
#: Plancher Kworb : en dessous, deux compteurs égaux peuvent être un hasard
#: (« DON'T KILL THE PARTY (Sped Up) » / « Unbreakable (Live) », 49 207).
PLANCHER = 100_000

_PARENTHESE = re.compile(r"[\(\[]")


def lectures(observations: Iterable) -> list[tuple[int, datetime]]:
    """`[(compteur, lu le)]` d'une fiche : ses observations `spotify_streams`."""
    sortie = []
    for o in observations or ():
        if o.field != "spotify_streams" or o.value is None or o.seen_at is None:
            continue
        try:
            valeur = int(o.value)
        except (TypeError, ValueError):
            continue
        vu = (
            o.seen_at if isinstance(o.seen_at, datetime) else datetime.fromisoformat(str(o.seen_at))
        )
        sortie.append((valeur, vu.replace(tzinfo=None)))
    return sortie


def meme_compteur(a: Sequence, b: Sequence, rythme: int | None) -> bool:
    """PUR. Toutes les lectures de `a` et de `b` sont-elles sur UNE courbe ?"""
    if not a or not b:
        return False
    if min(v for v, _ in (*a, *b)) < PLANCHER:
        return False
    for va, ta in a:
        for vb, tb in b:
            jours = (tb - ta).total_seconds() / 86400
            if rythme:
                if abs((vb - va) - rythme * jours) > TOLERANCE_JOURS * rythme:
                    return False
            elif vb != va or abs(jours) > DELAI_EGALITE_JOURS:
                return False
    return True


def _titre_principal(titre: str | None) -> str:
    return normalize_title(_PARENTHESE.split(titre or "", maxsplit=1)[0])


def titres_parents(a: str | None, b: str | None) -> bool:
    """PUR. « The Corner » / « The Corner (Last Poet Reprise) », « Keep My
    Spirit Alive » / « … pt 2 » : le titre principal de l'un, mots entiers,
    dans le titre de l'autre."""
    pa, pb = _titre_principal(a), _titre_principal(b)
    na, nb = normalize_title(a or ""), normalize_title(b or "")
    return bool(pa and pb) and (contains_as_words(pa, nb) or contains_as_words(pb, na))


def _nb_mots(titre: str | None) -> int:
    return len(normalize_title(titre or "").split())


def _instrumental(page) -> bool:
    return bool(page.lyrics.instrumental) or (
        parse_variant(page.title).kind is Kind.RENDITION
        and "instrumental" in normalize_title(parse_variant(page.title).descriptor or "")
    )


def _sortie(page) -> date | None:
    d = page.release_date
    if isinstance(d, datetime):
        return d.date()
    return d if isinstance(d, date) else None


#: Paroles Genius au moins aussi proches : une version proche (« Keep My
#: Spirit Alive » / « pt 2 » 0,98 ; mesuré le 2026-10-27, les versions proches
#: sont à 0,92-1,0, les autres paires à 0,01-0,69).
PAROLES_PROCHES = 0.9
#: … et au plus aussi proches : deux morceaux différents (les 7 paires du
#: 2026-10-27 hors versions : 0,01-0,39 ; « Tree » / « That Tree » 0,39 reste
#: à l'humain).
PAROLES_DIFFERENTES = 0.3


def version_proche(a, b, recouvrement: float | None) -> str | None:
    """PUR. Ce qui MONTRE que les deux fiches sont des versions proches d'un
    même morceau, dont Spotify peut partager le compteur ; None sinon. Deux
    paroles qui diffèrent disent plutôt qu'un des ID est celui de l'autre
    morceau (Diam's « Freestyle Dans ma bulle ») : c'est à l'humain."""
    if a.isrc and a.isrc == b.isrc:
        return "même ISRC"
    if _instrumental(a) != _instrumental(b) and titres_parents(a.title, b.title):
        return "l'une est l'instrumental de l'autre"
    if recouvrement is not None and recouvrement >= PAROLES_PROCHES:
        return f"paroles communes à {recouvrement:.0%}"
    return None


def page_du_compteur(pages: Sequence, rattachees_par_kworb: set) -> tuple[str, object] | None:
    """PUR. `(critère, page)` qui garde le compteur partagé ; None : l'humain.
    `rattachees_par_kworb` : les ID des fiches qui ont une lecture Kworb."""
    kworb = [p for p in pages if p.id in rattachees_par_kworb]
    if len(kworb) == 1:
        return "rattachée par Kworb", kworb[0]
    # Original : sans descripteur de version lu, pas un instrumental, au titre
    # le plus court (« Keep My Spirit Alive » avant « … pt 2 »).
    sans_version = [
        p for p in pages if parse_variant(p.title).kind is Kind.NONE and not _instrumental(p)
    ]
    if sans_version:
        court = min(_nb_mots(p.title) for p in sans_version)
        originaux = [p for p in sans_version if _nb_mots(p.title) == court]
        if len(originaux) == 1:
            return "titre de l'original", originaux[0]
    avec_paroles = [p for p in pages if not _instrumental(p)]
    if len(avec_paroles) == 1:
        return "avec paroles", avec_paroles[0]
    sorties = [(s, p) for p in pages if (s := _sortie(p))]
    if len(sorties) == len(pages):
        premiere = min(s for s, _ in sorties)
        retenues = [p for s, p in sorties if s == premiere]
        if len(retenues) == 1:
            return "première parution", retenues[0]
    return None
