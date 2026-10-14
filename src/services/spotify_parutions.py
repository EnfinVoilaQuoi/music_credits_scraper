"""Spotify par la PARUTION : MusicBrainz, puis la place sur l'album (2026-10-15).

Couche de l'étape Identité entre Kworb (2c) et le scraper par titre (3b).
La recherche par titre est ce qui produisait les faux ID (« Only » ← « Only
One ») ; ici l'ID vient de la PLACE de la piste sur la bonne édition.

  ① L'album Spotify de l'édition, par MusicBrainz (code-barres de la parution
    Deezer) — GARDE : retenu seulement si la parution MusicBrainz lie
    EXACTEMENT notre album Deezer. Mesuré le 2026-10-15 sur 127 UPC connus :
    100 liens Deezer justes (retenus), 5 vers une autre édition du même album
    (*BULLY*, *Man on the Moon* deluxe… — écartés), 22 sans lien Deezer (non
    utilisés). Datée (e57 `releases.mb_checked_at`), refaite à expiration.
  ② Les ID des pistes par la place. La page album Spotify donne l'ORDRE, pas
    le disque, et peut être incomplète (*Civilisation Édition Ultime* : 23
    rendues sur 25) : utilisée seulement si elle est COMPLÈTE et a le même
    nombre de pistes que l'album Deezer du même code-barres, qui donne le
    découpage en disques. Chaque piste alignée doit concorder avec la piste
    Deezer à sa place (titre, sinon durée ±2 s) ; sinon l'album est laissé.
    L'ID ne s'écrit qu'après le gate (`spotify_identity.valider_identite`), et
    jamais sur une fiche qui a déjà un ID principal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.utils.logger import get_logger
from src.utils.version_descriptors import titres_equivalents

logger = get_logger(__name__)

#: Même fichier chez deux plateformes : 99,4 % à ±2 s (mesuré le 2026-10-14).
TOLERANCE_DUREE_S = 2
#: Source des ID posés par la place (index `track_spotify_ids`).
SOURCE_PLACE = "parution"


@dataclass
class BilanSpotifyParutions:
    albums_mb: int = 0
    albums_rattaches: int = 0
    albums_ecartes_garde: int = 0
    pages_lues: int = 0
    pages_inutilisables: int = 0
    ids_poses: int = 0
    ids_refuses_gate: int = 0
    divergents: int = 0
    erreurs: list[str] = field(default_factory=list)


def album_spotify_garde(parutions_mb: list[dict], deezer_album_id) -> str | None:
    """PUR. L'album Spotify des parutions MusicBrainz qui lient EXACTEMENT
    notre album Deezer — un seul, sinon None (rien ne départage)."""
    cible = str(deezer_album_id)
    spotify = {
        sid
        for p in parutions_mb or ()
        if cible in (p.get("albums") or {}).get("deezer", [])
        for sid in (p.get("albums") or {}).get("spotify", [])
    }
    return spotify.pop() if len(spotify) == 1 else None


def ids_par_place(page: dict | None, pistes_deezer) -> dict[tuple[int, int], str] | None:
    """PUR. `{(disque, numéro): ID Spotify}` alignés sur l'album Deezer, ou None
    si la page ne permet pas de conclure (incomplète, autre nombre de pistes,
    place Deezer inconnue, alignement démenti)."""
    if not page:
        return None
    pistes_sp = page.get("tracks") or []
    annonce = page.get("announced")
    if annonce is None or len(pistes_sp) != annonce or len(pistes_sp) != len(pistes_deezer):
        return None
    if any(p.position is None for p in pistes_deezer):
        return None
    ordre = sorted(pistes_deezer, key=lambda p: (p.disk or 1, p.position))
    durees = page.get("durations") or {}
    out: dict[tuple[int, int], str] = {}
    for piste, (sid, titre) in zip(ordre, pistes_sp, strict=True):
        duree_sp = durees.get(sid)
        concorde = titres_equivalents(piste.title, titre) or bool(
            piste.duration and duree_sp and abs(piste.duration - duree_sp) <= TOLERANCE_DUREE_S
        )
        if not concorde:
            # Un seul démenti et l'ordre n'est plus sûr : l'album est laissé.
            return None
        out[(piste.disk or 1, piste.position)] = sid
    return out


def relier_par_parution(
    dm,
    artist,
    albums,
    *,
    mb=None,
    lire_albums=None,
    valider=None,
    force: bool = False,
    should_stop=lambda: False,
) -> BilanSpotifyParutions:
    """① puis ② pour les albums Deezer de ce run (`BilanEcarts.albums`)."""
    from src.services.runtime import constat_perime

    bilan = BilanSpotifyParutions()
    if mb is None:
        from src.api.musicbrainz_api import MusicBrainzAPI

        mb = MusicBrainzAPI()
    if lire_albums is None:
        from src.scrapers.spotify_web_scraper import lire_albums_page as lire_albums
    if valider is None:
        from src.utils.spotify_identity import valider_identite as valider

    # ① l'album Spotify de chaque parution
    a_lire: dict[str, tuple[int, object]] = {}
    for album in albums or ():
        if should_stop():
            break
        if not album.upc:
            continue
        release_id = dm.get_release_id_for_deezer_album(artist.id, album.id)
        if release_id is None:
            continue
        spotify_album, vu = dm.etat_parution_spotify(release_id)
        if spotify_album is None and (force or constat_perime(vu, album.release_date)):
            try:
                parutions = mb.parutions_par_code_barres(album.upc)
            except Exception as e:  # noqa: BLE001 — une panne ne date rien
                bilan.erreurs.append(f"MusicBrainz {album.title} : {e}")
                continue
            bilan.albums_mb += 1
            spotify_album = album_spotify_garde(parutions, album.id)
            if spotify_album is None and any(
                (p.get("albums") or {}).get("spotify") for p in parutions
            ):
                bilan.albums_ecartes_garde += 1
            dm.noter_parution_musicbrainz(release_id, artist.id, spotify_album)
            if spotify_album:
                bilan.albums_rattaches += 1
        if spotify_album:
            a_lire[spotify_album] = (release_id, album)

    # ② les ID par la place — seulement les albums où une fiche liée en manque
    positions = dm.get_artist_positions(artist.id)
    fiches = {t.id: t for t in artist.tracks}
    places_par_release: dict[int, dict[tuple[int, int], int]] = {}
    for tid, liste in positions.items():
        for p in liste:
            if p.get("position_source") == "deezer":
                places_par_release.setdefault(p["release_id"], {})[
                    (p.get("disc_number") or 1, p["track_number"])
                ] = tid
    utiles = {
        sid: (rid, album)
        for sid, (rid, album) in a_lire.items()
        if any(
            fiches.get(tid) is not None and not fiches[tid].spotify_id
            for tid in places_par_release.get(rid, {}).values()
        )
    }
    if not utiles or should_stop():
        return _journal(bilan)
    try:
        pages = lire_albums(list(utiles))
    except Exception as e:  # noqa: BLE001 — pages non lues : rien n'est écrit
        bilan.erreurs.append(f"pages album Spotify : {e}")
        return _journal(bilan)
    for sid_album, (release_id, album) in utiles.items():
        page = pages.get(sid_album)
        bilan.pages_lues += page is not None
        alignes = ids_par_place(page, album.pistes)
        if alignes is None:
            bilan.pages_inutilisables += 1
            continue
        for place, tid in places_par_release.get(release_id, {}).items():
            fiche, sid = fiches.get(tid), alignes.get(place)
            if fiche is None or sid is None:
                continue
            if fiche.spotify_id:
                bilan.divergents += fiche.spotify_id != sid
                continue
            if not valider(fiche, sid):
                bilan.ids_refuses_gate += 1
                continue
            if dm.update_track_spotify_id(fiche.id, sid, source=SOURCE_PLACE):
                fiche.spotify_id = sid
                bilan.ids_poses += 1
    return _journal(bilan)


def _journal(bilan: BilanSpotifyParutions) -> BilanSpotifyParutions:
    if bilan.albums_mb or bilan.pages_lues:
        logger.info(
            f"🎧 Spotify par la parution : {bilan.albums_rattaches} album(s) par MusicBrainz "
            f"({bilan.albums_ecartes_garde} écarté(s) par la garde), {bilan.pages_lues} page(s), "
            f"{bilan.pages_inutilisables} inutilisable(s), {bilan.ids_poses} ID posé(s), "
            f"{bilan.ids_refuses_gate} refusé(s) par le gate"
        )
    return bilan
