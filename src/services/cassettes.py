"""Cassettes : ce qu'un VRAI run a lu, rejoué en CI (chantier B, 2026-10-12).

Le brut des sources (chantier E) garde ce que Deezer a servi ; une cassette en
tire, pour UN artiste, les entrées réelles du classement des écarts Deezer
(`ecarts_deezer.classer`) — les albums et pistes lus, la discographie en base
réduite à ses champs d'IDENTITÉ (jamais de paroles : le dépôt est public) — et
le résultat attendu. Rejouée à chaque suite de tests
(`tests/test_cassettes.py`), elle voit toute règle qui change le classement de
données réelles ; un changement VOULU se réenregistre
(`scripts/cassette_deezer.py`) et le diff du fixture nomme les écarts touchés.
"""

from __future__ import annotations

import dataclasses
import json

from src.models import Artist, Track
from src.services.ecarts_deezer import AlbumDeezer, PisteDeezer, classer, cocher_par_defaut

#: Ce que le classement lit d'une fiche (et rien d'autre : ni paroles, ni crédits).
CHAMPS_FICHE = (
    "id",
    "title",
    "album",
    "genius_id",
    "deezer_id",
    "isrc",
    "duration",
    "duration_source",
    "durations_observees",
    "primary_artist_name",
    "is_featuring",
    "secondary_role",
    "release_date",
    "unreleased",
    "durees_editions",
)


def fiche_vers_dict(track: Track) -> dict:
    d = {}
    for champ in CHAMPS_FICHE:
        valeur = getattr(track, champ, None)
        if champ == "durees_editions":
            valeur = list(valeur or ())
        d[champ] = str(valeur) if champ == "release_date" and valeur is not None else valeur
    return d


def fiche_depuis(d: dict, artiste: str) -> Track:
    t = Track(title=d["title"], artist=Artist(name=artiste))
    for champ in CHAMPS_FICHE:
        if champ in d and champ != "title":
            setattr(t, champ, d[champ])
    t.durations_observees = d.get("durations_observees") or {}
    return t


def album_vers_dict(album: AlbumDeezer) -> dict:
    return dataclasses.asdict(album)


def album_depuis(d: dict) -> AlbumDeezer:
    pistes = [
        PisteDeezer(**{**p, "contributors": [tuple(c) for c in p.get("contributors") or []]})
        for p in d.get("pistes") or []
    ]
    return AlbumDeezer(
        **{
            **{k: v for k, v in d.items() if k != "pistes"},
            "contributors": [tuple(c) for c in d.get("contributors") or []],
            "pistes": pistes,
        }
    )


def resultat(ecarts, jumelles) -> dict:
    """Le résultat COMPARABLE du classement : un écart par ligne, trié."""
    lignes = sorted(
        json.dumps(
            {
                "nature": e.nature,
                "album": e.album.id,
                "piste": e.piste.id,
                "titre": e.piste.title,
                "fiche": e.existing_track.id if e.existing_track is not None else None,
                "socle": e.socle.id if e.socle is not None else None,
                "coche": e.coche,
                "motifs": list(e.motifs),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for e in ecarts
    )
    return {"ecarts": lignes, "editions_jumelles": sorted(jumelles)}


def rejouer(cassette: dict) -> dict:
    """Le classement des écarts Deezer, rejoué sur les entrées de la cassette."""
    artiste = cassette["artiste"]
    base = [fiche_depuis(d, artiste) for d in cassette["base"]]
    albums = [album_depuis(d) for d in cassette["albums"]]
    ecarts, jumelles = classer(
        albums,
        base,
        cassette["albums_base"],
        cassette["deezer_id"],
        liens_connus={tuple(x) for x in cassette["liens_connus"]},
    )
    cocher_par_defaut(ecarts, base)
    return resultat(ecarts, jumelles)


# ── Kworb (2026-10-13) : le rapprochement ligne → fiche ─────────────────────

#: Ce que `update_kworb.rapprocher` lit d'une fiche.
CHAMPS_KWORB = (
    "id",
    "title",
    "album",
    "spotify_id",
    "secondary_role",
    "primary_artist_name",
    "is_featuring",
)


def fiche_kworb_vers_dict(track: Track) -> dict:
    d = {champ: getattr(track, champ, None) for champ in CHAMPS_KWORB}
    d["spotify_id_entries"] = [
        {"spotify_id": e.spotify_id, "kind": e.kind}
        for e in getattr(track, "spotify_id_entries", None) or []
    ]
    return d


def fiche_kworb_depuis(d: dict, artiste: str) -> Track:
    from src.models.track import TrackSpotifyId

    t = Track(title=d["title"], artist=Artist(name=artiste))
    for champ in CHAMPS_KWORB:
        if champ in d and champ != "title":
            setattr(t, champ, d[champ])
    t.spotify_id_entries = [TrackSpotifyId(**e) for e in d.get("spotify_id_entries") or []]
    return t


def rejouer_kworb(cassette: dict) -> list[dict]:
    """Le verdict de `rapprocher` pour chaque ligne de la cassette, trié : les
    lectures d'identité Spotify sont celles du run enregistré."""
    from src.utils import update_kworb

    artiste = cassette["artiste"]
    index = update_kworb.construire_index(
        [fiche_kworb_depuis(d, artiste) for d in cassette["base"]]
    )
    identites = cassette["identites"]
    sortie = []
    for entry in cassette["lignes"]:
        r = update_kworb.rapprocher(
            entry, index, Artist(name=artiste), cassette["decisions"], identites.get
        )
        sortie.append(update_kworb.verdict_vers_dict(entry, r))
    return sorted(sortie, key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))
