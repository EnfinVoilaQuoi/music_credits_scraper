"""Compléter les albums de l'artiste par leur tracklist Genius (2026-09-24).

L'import part de `/artists/{id}/songs` : les morceaux où l'artiste est CRÉDITÉ.
Un titre de SON album où Genius crédite quelqu'un d'autre en est absent — MC
Solaar, *Qui sème le vent récolte le tempo* : l'« Intro » et « Funky Dreamer »
sont signés Jimmy Jay, l'« Interlude » Boom Bass. Décision utilisateur : c'est
son album, sa DA, ses choix — ces titres sont des morceaux PRINCIPAUX de
l'artiste, l'artiste de la page Genius restant visible (`primary_artist_name`
posé SANS `is_featuring` : la combinaison n'existait nulle part en base).

La même lecture rattrape les morceaux de l'artiste que la liste omet (paroles
incomplètes — « Vas-y chante », 2 sur 14).

Un album n'est lu que s'il est À l'artiste (`primary_artists` de l'album) :
jamais la compilation ou le projet d'un autre où il n'est qu'invité. Coût : une
requête `/songs/{id}` par album (l'album et son artiste) + la tracklist ; un
album déjà complet n'est plus relu pendant `FRAICHEUR`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from src.api.genius_api import QuotaGeniusAtteint
from src.config import DATA_DIR
from src.enrichment.observation import Observation
from src.models import Artist, ReleaseObservation, Track
from src.utils.inedits import porte_le_marqueur, titre_sans_marqueur
from src.utils.logger import get_logger
from src.utils.pages_genius import page_non_morceau
from src.utils.title_matching import cle_album

logger = get_logger(__name__)

FICHIER = Path(DATA_DIR) / "tracklists_genius.json"
#: Un album complet n'est relu qu'après ce délai (deluxe, titres ajoutés).
FRAICHEUR = timedelta(days=30)
#: L'album d'un AUTRE ne change pas de propriétaire : relu bien plus rarement.
FRAICHEUR_ETRANGER = timedelta(days=180)


@dataclass
class Completion:
    pistes: list[Track] = field(default_factory=list)
    albums_lus: int = 0
    albums_etrangers: int = 0
    echecs: int = 0
    #: Graines que Genius ne range dans aucun album (singles) : pas un échec.
    sans_album: int = 0
    #: Quota journalier Genius épuisé : la complétion s'est ARRÊTÉE en route.
    quota: bool = False


def _charger() -> dict:
    try:
        return json.loads(FICHIER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _sauver(donnees: dict) -> None:
    try:
        FICHIER.parent.mkdir(parents=True, exist_ok=True)
        FICHIER.write_text(json.dumps(donnees, ensure_ascii=False, indent=1), "utf-8")
    except OSError as e:
        logger.warning(f"Cache des tracklists Genius non écrit : {e}")


def _frais(entree: dict | None, connus: set[int], maintenant: datetime) -> bool:
    """L'album peut-il être sauté sans appel ?"""
    if not entree:
        return False
    try:
        vu = datetime.fromisoformat(entree["vu"])
    except (KeyError, TypeError, ValueError):
        return False
    if entree.get("etranger") or entree.get("sans_album"):
        return maintenant - vu < FRAICHEUR_ETRANGER
    return maintenant - vu < FRAICHEUR and set(entree.get("ids") or []) <= connus


def graines(tracks: list[Track]) -> dict[str, list[int]]:
    """PUR. Par album (clé normalisée), les genius_id des morceaux PRINCIPAUX
    qui y sont rangés : un seul suffit à retrouver l'album chez Genius."""
    par_album: dict[str, list[int]] = {}
    for t in tracks:
        if not t.genius_id or not t.album or t.is_featuring or t.secondary_role:
            continue
        par_album.setdefault(cle_album(t.album), []).append(int(t.genius_id))
    return {k: sorted(set(v)) for k, v in par_album.items()}


def piste_depuis_tracklist(entree: dict, album: dict, artist: Artist) -> Track | None:
    """PUR. La fiche d'un titre de la tracklist, ou None (traduction, livret).

    Crédité à l'artiste ⇒ morceau principal ; l'artiste en featuring ⇒ feat ;
    sinon titre d'album signé par un autre ⇒ morceau PRINCIPAL de l'artiste avec
    l'artiste Genius en `primary_artist_name` (décision utilisateur)."""
    from src.api.genius_api import GeniusAPI

    song = entree.get("song") or {}
    titre_brut = song.get("title") or ""
    if not song.get("id") or not titre_brut or page_non_morceau(titre_brut):
        return None
    aid = int(artist.genius_id)
    principaux = GeniusAPI._collect_artist_ids(song.get("primary_artists"))
    principaux |= GeniusAPI._collect_artist_ids([song.get("primary_artist")])
    invites = GeniusAPI._collect_artist_ids(song.get("featured_artists"))
    nom_album = titre_sans_marqueur(album.get("name") or "") or None
    track = Track(
        title=titre_sans_marqueur(titre_brut),
        artist=artist,
        genius_id=song["id"],
        genius_url=song.get("url"),
        album=nom_album,
        track_number=entree.get("number"),
        is_featuring=False,
    )
    if porte_le_marqueur(titre_brut):
        track.unreleased = True
    signataire = (song.get("primary_artist") or {}).get("name")
    if aid not in principaux:
        if aid in invites:
            track.is_featuring = True
        track.primary_artist_name = signataire
    composants = song.get("release_date_components") or {}
    if composants.get("year"):
        a, m, j = composants.get("year"), composants.get("month"), composants.get("day")
        track.release_date = datetime(a, m or 1, j or 1) if (m and j) else datetime(a, 1, 1)
        precision = (
            f"{a:04d}-{m:02d}-{j:02d}" if (m and j) else (f"{a:04d}-{m:02d}" if m else f"{a:04d}")
        )
        track.observations.append(Observation("release_date", precision, "genius"))
    if nom_album:
        track.release_observations.append(
            ReleaseObservation(
                title=nom_album,
                source="genius",
                external_release_id=album.get("id"),
                external_track_id=song["id"],
                release_date=track.release_date,
                scope="own",
                confidence="identified",
            )
        )
    return track


def completer(
    genius,
    artist: Artist,
    tracks: list[Track],
    *,
    exclus: set[int] = frozenset(),
    should_stop=lambda: False,
    maintenant: datetime | None = None,
) -> Completion:
    """Les titres des albums de l'artiste que `tracks` (base + run) n'a pas.

    `exclus` : genius_id supprimés par l'utilisateur, absorbés à la main ou notés
    comme éditions — jamais recréés."""
    maintenant = maintenant or datetime.now()
    bilan = Completion()
    if not artist.genius_id or not artist.id:
        return bilan
    connus = {int(t.genius_id) for t in tracks if t.genius_id}
    cache = _charger()
    du_artiste = cache.setdefault(str(artist.id), {})
    albums_vus: set[int] = set()
    for cle, seeds in graines(tracks).items():
        if should_stop():
            break
        if _frais(du_artiste.get(cle), connus | set(exclus), maintenant):
            continue
        album = None
        try:
            for seed in seeds[:2]:  # un morceau peut pointer vers une autre parution
                album = genius.album_du_morceau(seed)
                if album is None or album.get("id"):
                    break
            pistes = (
                genius.tracklist_album(album["id"])
                if album
                and int(artist.genius_id) in album["primary_artist_ids"]
                and album["id"] not in albums_vus
                else None
            )
        except QuotaGeniusAtteint:
            # Rien n'est mis en cache : le prochain run reprend là.
            logger.warning("⛔ Quota journalier Genius épuisé — complétion des tracklists arrêtée")
            bilan.quota = True
            break
        if album is None:
            bilan.echecs += 1
            continue
        if not album:
            bilan.sans_album += 1
            du_artiste[cle] = {"sans_album": True, "vu": maintenant.isoformat()}
            continue
        if int(artist.genius_id) not in album["primary_artist_ids"]:
            bilan.albums_etrangers += 1
            du_artiste[cle] = {
                "album_id": album["id"],
                "etranger": True,
                "vu": maintenant.isoformat(),
            }
            continue
        if album["id"] in albums_vus:
            continue
        albums_vus.add(album["id"])
        if pistes is None:
            bilan.echecs += 1
            continue
        bilan.albums_lus += 1
        ids = []
        for entree in pistes:
            track = piste_depuis_tracklist(entree, album, artist)
            if track is None:
                continue
            ids.append(int(track.genius_id))
            if track.genius_id in connus or track.genius_id in exclus:
                continue
            connus.add(track.genius_id)
            bilan.pistes.append(track)
            logger.info(
                f"➕ Tracklist « {album.get('name')} » : {track.title}"
                + (
                    f" (page Genius de {track.primary_artist_name})"
                    if track.primary_artist_name
                    else ""
                )
            )
        du_artiste[cle] = {"album_id": album["id"], "ids": ids, "vu": maintenant.isoformat()}
    _sauver(cache)
    return bilan
