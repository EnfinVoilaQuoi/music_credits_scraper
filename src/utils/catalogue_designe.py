"""Deux pages Genius portent le même ID de catalogue : laquelle désigne-t-il ?

Né des 6 ID Spotify / Deezer partagés du 2026-10-27 (JOURNAL 2026-10-27 (6)) :
la même recherche de titre avait posé le même ID sur deux pages au même titre
(« Forever » de Lil Wayne et celui de Drake, l'« Intro » d'*Akademiks* et celle
de *College Dropout*, « Deuces (Remix) » et son long remix…). Ce que le
catalogue SERT pour cet ID désigne la page, par ordre : titre complet (mots,
crédits « feat. » compris), titre sans les crédits, interprète principal,
album, date — le premier critère qui ne retient QU'UNE page tranche. Aucun
critère seul : on ne conclut pas (« On ne retire que ce qu'on peut montrer »).

Simulé à la main le 2026-10-27 : 6 / 6 tranchés, chacun confirmé par
l'utilisateur ou un témoin (Deuces : 274 s servi contre 338-339 s du long
remix). Module PUR : la lecture du catalogue (embed Spotify, `GET /track` de
Deezer) est faite par l'appelant.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from src.utils.title_matching import cle_album, normalize_title

#: Mots de liaison d'un crédit, ignorés des deux côtés.
_MOTS_DE_CREDIT = frozenset({"feat", "ft", "featuring", "and", "with"})
_CREDIT = re.compile(r"[\(\[]\s*(?:feat|ft|featuring|with)\b\.?[^\)\]]*[\)\]]", re.IGNORECASE)


@dataclass(frozen=True)
class Servi:
    """Ce que le catalogue sert pour un ID."""

    titre: str
    principal: str | None = None
    album: str | None = None
    #: AAAA-MM-JJ.
    date: str | None = None


def servi_spotify(identite: dict | None) -> Servi | None:
    """Depuis l'identité de l'embed (`SpotifyIDScraper._identite_depuis_embed`) :
    l'embed ne nomme pas l'album."""
    if not identite or not identite.get("name"):
        return None
    artistes = identite.get("artists") or []
    return Servi(
        titre=identite["name"],
        principal=artistes[0] if artistes else None,
        date=(identite.get("release_date") or None),
    )


def servi_deezer(piste: dict | None) -> Servi | None:
    """Depuis la fiche `GET /track/{id}` de Deezer."""
    if not piste or not piste.get("title"):
        return None
    return Servi(
        titre=piste["title"],
        principal=(piste.get("artist") or {}).get("name"),
        album=(piste.get("album") or {}).get("title"),
        date=piste.get("release_date") or None,
    )


def _mots(titre: str | None) -> frozenset[str]:
    t = unicodedata.normalize("NFKD", (titre or "").lower().replace("’", "'"))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return frozenset(re.findall(r"[a-z0-9$']+", t)) - _MOTS_DE_CREDIT


def _sans_credit(titre: str | None) -> frozenset[str]:
    return _mots(_CREDIT.sub(" ", titre or ""))


def _principal(page) -> str:
    nom = page.primary_artist_name or (page.artist.name if page.artist else "")
    return normalize_title(nom or "")


def _date(page) -> str | None:
    d = page.release_date
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return str(d)[:10] if d else None


def _criteres(servi: Servi) -> list[tuple[str, Callable]]:
    criteres = [
        ("titre complet", lambda p: _mots(p.title) == _mots(servi.titre)),
        ("titre sans crédit", lambda p: _mots(p.title) == _sans_credit(servi.titre)),
    ]
    if servi.principal:
        principal = normalize_title(servi.principal)
        criteres.append(("interprète principal", lambda p: _principal(p) == principal))
    if servi.album:
        album = cle_album(servi.album)
        criteres.append(("album", lambda p: bool(p.album) and cle_album(p.album) == album))
    if servi.date:
        criteres.append(("date", lambda p: _date(p) == servi.date[:10]))
    return criteres


#: Paroles Genius communes (bigrammes) à partir desquelles deux pages disent le
#: même texte, et écart de durée d'un même fichier.
PAROLES_MEME_ENREGISTREMENT = 0.9
ECART_MEME_ENREGISTREMENT_S = 2


def meme_enregistrement_prouve(a, b) -> bool:
    """PUR. Deux pages Genius sont UN enregistrement, preuves prises hors de
    l'ID qu'elles partagent : le même ISRC sur deux pages Deezer DIFFÉRENTES,
    les mêmes paroles Genius, la même durée (décision utilisateur 2026-11-04 :
    « Real Niggas Stand Up » et « Real n***** stand up », dont la règle du
    catalogue avait retiré l'ID et l'ISRC à l'une). Ni « à quelle page est cet
    ID ? », ni retrait : à fusionner, décision humaine."""
    from src.utils.concordance_paroles import paroles_de_reference, recouvrement_bigrammes

    if not a.isrc or str(a.isrc).strip().upper() != str(b.isrc or "").strip().upper():
        return False
    if not a.deezer_id or not b.deezer_id or int(a.deezer_id) == int(b.deezer_id):
        return False
    if not a.duration or not b.duration:
        return False
    if abs(int(a.duration) - int(b.duration)) > ECART_MEME_ENREGISTREMENT_S:
        return False
    communes = recouvrement_bigrammes(
        paroles_de_reference(a.lyrics.text, a.lyrics.source),
        paroles_de_reference(b.lyrics.text, b.lyrics.source),
    )
    return communes is not None and communes >= PAROLES_MEME_ENREGISTREMENT


def page_designee(servi: Servi | None, pages: Sequence) -> tuple[str, object] | None:
    """PUR. `(critère, page)` : la SEULE page que ce que sert le catalogue
    retient, au premier critère qui n'en retient qu'une ; None sinon."""
    if servi is None or len(pages) < 2:
        return None
    for nom, convient in _criteres(servi):
        retenues = [p for p in pages if convient(p)]
        if len(retenues) == 1:
            return nom, retenues[0]
    return None
