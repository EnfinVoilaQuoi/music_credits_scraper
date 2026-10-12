"""Lignes des AUTRES artistes de la base sur une fiche sans page Genius
(CORRECTIFS 1.17, décision utilisateur 2026-10-09).

Une ligne sœur naît d'une page Genius commune (`track_soeurs`, clé
`genius_id`). Une fiche créée par les écarts Deezer ou par une décision Kworb
n'a pas de page : elle n'existait que chez l'artiste en cours de traitement.
Mesuré : « Dolce Camara (Snight B Remix) » et « (Dee Mad x Akalex Remix) »
étaient chez Booba seulement, SDM crédité en voix sur les deux — chez SDM, la
certif du remix tombait alors sur son « Dolce Camara » comme une certif propre.

« Si on détecte dans la base d'un artiste des morceaux qui concernent aussi un
autre artiste en base, c'est pratique de les mettre à jour aussi chez eux. »
Un artiste de la base crédité en VOIX ou en FEATURING sur une telle fiche reçoit
sa ligne : même enregistrement (identifiants, durée, date, crédits,
observations, vidéos), contexte d'invité (`is_featuring`, l'artiste principal
en `primary_artist_name`). Rien n'est créé quand il a déjà une ligne pour cet
enregistrement (même ID Deezer, Spotify ou ISRC, ou même titre à la graphie
près). Ce n'est PAS une synchronisation continue : chaque artiste enrichit
ensuite sa ligne par ses propres runs.
"""

from __future__ import annotations

import copy

from src.models import Artist, Track
from src.models.track import CreditRole
from src.utils.logger import get_logger
from src.utils.title_matching import normalize_title
from src.utils.version_descriptors import titres_confondus

logger = get_logger(__name__)

#: Ce qui fait d'un artiste un INTERPRÈTE de la fiche (pas un auteur ni un
#: producteur : ceux-là ne reçoivent pas de ligne, comme sur Genius).
ROLES_DE_VOIX = frozenset({CreditRole.FEATURED, CreditRole.VOCALS, CreditRole.ADDITIONAL_VOCALS})


def _meme_enregistrement(a: Track, b: Track) -> bool:
    return bool(
        (a.deezer_id and str(a.deezer_id) == str(b.deezer_id))
        or (a.spotify_id and a.spotify_id == b.spotify_id)
        or (a.isrc and b.isrc and str(a.isrc).upper() == str(b.isrc).upper())
        or titres_confondus(a.title, b.title)
    )


def interpretes_en_base(track: Track, artistes: list[Artist], proprietaire_id) -> list[Artist]:
    """PUR. Les artistes de la base (hors propriétaire) crédités en voix ou
    featuring sur `track` — nom EXACT à la normalisation près."""
    voix = {normalize_title(c.name) for c in track.credits if c.role in ROLES_DE_VOIX and c.name}
    return [a for a in artistes if a.id != proprietaire_id and normalize_title(a.name) in voix]


def donner_aux_interpretes(dm, track: Track, proprietaire: Artist) -> list[str]:
    """Crée la ligne de chaque interprète en base qui n'en a pas. Rend un
    compte rendu par ligne créée. Une fiche AVEC page Genius n'est pas concernée
    (ses lignes sœurs naissent de la page)."""
    if track.genius_id or track.id is None:
        return []
    faits = []
    for artiste in interpretes_en_base(track, dm.artistes_de_la_base(), proprietaire.id):
        if any(_meme_enregistrement(track, t) for t in dm.get_artist_tracks(artiste.id)):
            continue
        ligne = Track(title=track.title, artist=artiste)
        ligne.album = track.album
        ligne.release_date = track.release_date
        ligne.duration = track.duration
        ligne.deezer_id, ligne.deezer_url = track.deezer_id, track.deezer_url
        ligne.isrc = track.isrc
        if track.spotify_id:
            ligne.spotify_id = track.spotify_id
            ligne.add_spotify_id(track.spotify_id, source="ligne_tiers")
        ligne.is_featuring = True
        ligne.primary_artist_name = proprietaire.name
        ligne.credits = copy.deepcopy(track.credits)
        ligne.streams = copy.deepcopy(track.streams)
        ligne.relationships = copy.deepcopy(track.relationships or [])
        ligne._relationships_pending = bool(ligne.relationships)
        dm.save_track(ligne)
        dm.record_pending(ligne)
        dm.upsert_observations(ligne.id, dm.get_observations(track.id))
        videos = dm.get_track_videos(track.id)
        if videos:
            # Le total YouTube Music suit les vidéos reçues : l'écrivain le
            # recalcule lui-même (chantier A ; 2026-10-11, les lignes SDM de
            # « Dolce Camara » restaient sans total).
            dm.record_track_videos(ligne.id, videos)
        faits.append(f"« {track.title} » ajouté chez {artiste.name} (crédité sur la fiche)")
        logger.info(f"👥 {faits[-1]}")
    return faits


def rattraper(dm, artiste: Artist, tracks: list[Track] | None = None) -> list[str]:
    """Passe sur les fiches sans page Genius de l'artiste (fin de run
    discographie) : rattrape celles créées avant la règle."""
    faits = []
    for t in tracks if tracks is not None else dm.get_artist_tracks(artiste.id):
        if not t.genius_id:
            faits += donner_aux_interpretes(dm, t, artiste)
    return faits
