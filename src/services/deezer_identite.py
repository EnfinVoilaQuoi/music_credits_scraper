"""Quel artiste Deezer est le nôtre ? — l'oracle d'identité (2026-09-21).

`GET /search/artist?q=Isha` rend d'abord un homonyme à 5 fans (259696952) ;
le nôtre, « ISHA » (1236609, 44 albums, 84 k fans), vient après. « A2H » rend
un artiste UK. Prendre le premier hit — ce que fait `DeezerAPI.search_artist`,
et donc la photo de profil de `media_enricher` — désigne quelqu'un d'autre, et
un mauvais identifiant ne se voit pas : il rend un catalogue parfaitement formé.

Même règle que MusicBrainz et les canaux YTMusic (CLAUDE.md, 2026-09-08) :
candidats au nom EXACT normalisé, départage par recouvrement avec la base,
pluralité NETTE, refus de conclure sur une égalité. Trois votes, du plus sûr au
plus faible :

  ① les `albums.deezer_album_id` déjà en base (posés par le typage d'album)
    que le candidat a dans sa discographie ;
  ② les titres d'albums normalisés en commun ;
  ③ l'artiste principal des morceaux qui ont un `tracks.deezer_id` (rare :
    Isha en a 1 sur 250).

Le verdict est mémorisé dans `artists.deezer_id` (e30) ; une ambiguïté n'écrit
rien et remonte `ArtisteDeezerAmbigu` avec les candidats, pour que la CLI
(`--deezer-id`) ou la fenêtre laisse l'humain trancher. Module sans GUI.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from src.utils.logger import get_logger
from src.utils.title_matching import normalize_name, normalize_title

logger = get_logger(__name__)

#: Nombre maximal de fiches `/track/{id}` lues pour le vote ③.
_MAX_PISTES_VOTE = 10


@dataclass
class CandidatDeezer:
    id: int
    name: str
    nb_album: int = 0
    nb_fan: int = 0
    #: Titres d'albums (normalisés) de la discographie Deezer du candidat.
    albums: set[str] = field(default_factory=set)
    album_ids: set[int] = field(default_factory=set)
    #: Votes : ids d'albums communs ×3 (le signal le plus sûr), titres communs,
    #: morceaux dont la fiche Deezer le crédite.
    recouvrement: int = 0
    detail: str = ""


class ArtisteDeezerAmbigu(Exception):
    """Aucun candidat sûr : `candidats` = les homonymes exacts, avec leur score."""

    def __init__(self, nom: str, candidats: list[CandidatDeezer]):
        super().__init__(f"Artiste Deezer ambigu : {nom!r}")
        self.nom = nom
        self.candidats = candidats


def cle_album(titre: str | None) -> str:
    """Clé de comparaison d'un titre d'album — jamais vide : « … » et « ... »
    normalisent à la chaîne vide (points supprimés), le titre brut sert alors."""
    brut = (titre or "").strip()
    return normalize_title(brut) or unicodedata.normalize("NFKD", brut).lower()


def candidats_exacts(nom: str, hits: list[dict]) -> list[CandidatDeezer]:
    """Les hits dont le nom normalisé est EXACTEMENT le nôtre — jamais par mots
    entiers (« Swing » ⊂ « Swing Out Sister »)."""
    cible = normalize_name(nom)
    out = []
    for h in hits or []:
        if h.get("id") and h.get("name") and normalize_name(h["name"]) == cible:
            out.append(
                CandidatDeezer(
                    id=int(h["id"]),
                    name=h["name"],
                    nb_album=int(h.get("nb_album") or 0),
                    nb_fan=int(h.get("nb_fan") or 0),
                )
            )
    return out


def compter_recouvrement(
    candidat: CandidatDeezer,
    albums_base: set[str],
    album_ids_base: set[int],
    artistes_des_pistes: list[int],
) -> CandidatDeezer:
    """Pose `recouvrement` et `detail` sur le candidat (fonction pure sur ses champs)."""
    ids = len(candidat.album_ids & album_ids_base)
    titres = len(candidat.albums & albums_base)
    pistes = sum(1 for a in artistes_des_pistes if a == candidat.id)
    candidat.recouvrement = 3 * ids + titres + pistes
    candidat.detail = f"{ids} id(s) d'album, {titres} titre(s) d'album, {pistes} piste(s)"
    return candidat


def departager(candidats: list[CandidatDeezer]) -> CandidatDeezer | None:
    """Un seul candidat exact ⇒ accepté ; sinon recouvrement non nul ET pluralité
    NETTE ; une égalité en tête ne se tranche pas (l'ordre y serait arbitraire)."""
    if not candidats:
        return None
    if len(candidats) == 1:
        return candidats[0]
    classes = sorted(candidats, key=lambda c: -c.recouvrement)
    if classes[0].recouvrement == 0 or classes[0].recouvrement == classes[1].recouvrement:
        return None
    return classes[0]


async def resoudre_async(client, http, dm, artist, *, force_id: int | None = None) -> int:
    """L'identifiant Deezer de l'artiste — mémorisé, forcé, ou tranché par l'oracle.

    N'écrit JAMAIS sur une ambiguïté : `ArtisteDeezerAmbigu` remonte les
    candidats. `client` = `DeezerAPI`, `http` = la session async partagée
    (`runtime.data_enricher.http`, propriétaire = l'enricher).
    """
    if force_id:
        fiche = await client.get_artist_async(http, int(force_id))
        if not fiche:
            raise ArtisteDeezerAmbigu(artist.name, [])
        dm.update_artist_deezer_id(artist.id, int(force_id))
        artist.deezer_id = int(force_id)
        return int(force_id)
    if artist.deezer_id:
        return int(artist.deezer_id)

    hits = await client.search_artists_async(http, artist.name)
    candidats = candidats_exacts(artist.name, hits)
    if not candidats:
        raise ArtisteDeezerAmbigu(artist.name, [])

    albums_base = {
        cle_album(a.get("title")) for a in dm.get_albums_for_artist(artist.id) if a.get("title")
    }
    album_ids_base = {
        int(a["deezer_album_id"])
        for a in dm.get_albums_for_artist(artist.id)
        if a.get("deezer_album_id")
    }
    tracks = dm.get_artist_tracks(artist.id)
    for t in tracks:
        if t.album:
            albums_base.add(cle_album(t.album))
    artistes_des_pistes: list[int] = []
    if len(candidats) > 1:
        for t in [t for t in tracks if t.deezer_id][:_MAX_PISTES_VOTE]:
            fiche = await client.get_track_async(http, int(t.deezer_id))
            aid = ((fiche or {}).get("artist") or {}).get("id")
            if aid:
                artistes_des_pistes.append(int(aid))
        for c in candidats:
            for al in await client.get_artist_albums_async(http, c.id):
                if al.get("id"):
                    c.album_ids.add(int(al["id"]))
                if al.get("title"):
                    c.albums.add(cle_album(al["title"]))
            compter_recouvrement(c, albums_base, album_ids_base, artistes_des_pistes)

    elu = departager(candidats)
    if elu is None:
        logger.warning(
            f"Deezer : « {artist.name} » ambigu — "
            + " | ".join(f"{c.id} {c.name} ({c.detail})" for c in candidats)
        )
        raise ArtisteDeezerAmbigu(artist.name, candidats)
    dm.update_artist_deezer_id(artist.id, elu.id)
    artist.deezer_id = elu.id
    logger.info(f"Deezer : « {artist.name} » = {elu.id} ({elu.name}, {elu.detail or 'seul'})")
    return elu.id
