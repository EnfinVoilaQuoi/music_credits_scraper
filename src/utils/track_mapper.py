"""Mapper ligne DB → objet Track.

C'est le **seul** endroit du projet où les coercitions de types depuis la base
sont légales (règle refacto Phase 4, couche « Frontière DB → objet ») : durées
`"3:48"` → 228, littéraux `'None'`/`'NULL'` → None, JSON certifications, etc.
Le domaine (`models/`, logique métier) accède aux attributs directement, sans
`getattr`/`hasattr` défensif.

`track_from_row(row, artist)` est une fonction pure (aucun accès DB) : elle
prend une `sqlite3.Row` de la table `tracks` + l'`Artist` déjà construit, et
renvoie un `Track` — ou `None` si la ligne est inexploitable (id/titre absent).
Le chargement des crédits reste à l'appelant (il a besoin du curseur).
"""

import json

from src.models import Artist, Track
from src.utils.logger import get_logger

logger = get_logger(__name__)

_NULL_LITERALS = ("None", "NULL", "")


def _clean(value, default=None):
    """Valeur telle quelle, sauf littéraux DB vides ('None'/'NULL'/'') → default."""
    if value is None or str(value) in _NULL_LITERALS:
        return default
    return value


def _clean_int(value, default=None, allow_string=False):
    """Convertit en int. allow_string=True : renvoie la string d'origine si non
    convertible (key/mode peuvent être int 0-11/0-1 OU string 'G'/'major')."""
    if value is None or str(value) in _NULL_LITERALS:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        if allow_string and isinstance(value, str):
            return value
        return default


def _clean_duration(value, default=None):
    """Durée en secondes (int). Supporte int, '180', '3:00' (MM:SS)."""
    if value is None or str(value) in _NULL_LITERALS:
        return default

    if isinstance(value, int):
        return value

    if isinstance(value, str):
        value = value.strip()
        if not value:
            return default

        if ":" in value:
            try:
                parts = value.split(":")
                if len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
            except (ValueError, IndexError):
                return default

        try:
            return int(value)
        except ValueError:
            return default

    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def track_from_row(row, artist: Artist, observations=None) -> Track | None:
    """Construit un Track depuis une ligne `SELECT * FROM tracks` (sqlite3.Row).

    Renvoie None si la ligne n'a pas d'id ou de titre exploitable — l'appelant
    passe alors au morceau suivant. Les crédits ne sont PAS chargés ici.

    `observations` (phase E6, bascule lecture) : liste d'`Observation` du morceau.
    Si fournie, le moteur les réconcilie et PILOTE bpm/bpm_alt/source/confidence
    + key/mode/key_mode_source/musical_key (via `apply_resolutions`), écrasant les
    valeurs des colonnes legacy déjà posées ci-dessous. Un champ sans observation
    n'est pas touché → FALLBACK sur la colonne legacy (la triple écriture les
    garde en phase ; un morceau jamais réenrichi lit ses colonnes). Réalise le
    point-2 : l'appariement key/mode inter-runs se fait ici, sur l'union
    persistée des observations.
    """
    track_id = row["id"]
    title = row["title"]

    if not track_id or not title:
        return None
    if str(title).strip() in _NULL_LITERALS:
        return None

    track = Track(id=track_id, title=str(title).strip())
    track.artist = artist

    track.album = _clean(row["album"])
    track.track_number = _clean_int(row["track_number"])
    track.release_date = _clean(row["release_date"])
    track.genius_id = _clean(row["genius_id"])
    track.spotify_id = _clean(row["spotify_id"])
    track.discogs_id = _clean(row["discogs_id"])
    track.isrc = _clean(row["isrc"])
    # E7-D2 : colonnes AUDIO droppées (bpm, bpm_alt, bpm_source, bpm_confidence,
    # key, mode, key_mode_source, musical_key, time_signature, reccobeats_resolution).
    # Attributs posés à None ici (garantit leur existence) PUIS pilotés par la
    # réconciliation des observations (bas de fonction). reccobeats_resolution est
    # reposé par apply_resolutions quand son observation de provenance existe.
    track.audio.bpm = None
    track.audio.bpm_source = None
    track.audio.bpm_confidence = None
    track.audio.key_mode_source = None
    track.audio.reccobeats_resolution = None
    track.audio.bpm_alt = None
    track.lyrics_source = _clean(row["lyrics_source"])
    track.lyrics_synced = _clean(row["lyrics_synced"])
    track.lyrics_synced_source = _clean(row["lyrics_synced_source"])
    track.lyrics_synced_confidence = _clean_int(row["lyrics_synced_confidence"])
    track.youtube_url = _clean(row["youtube_url"])
    track.youtube_url_source = _clean(row["youtube_url_source"])
    track.streams.spotify_streams = _clean_int(row["spotify_streams"])
    track.streams.spotify_daily_streams = _clean_int(row["spotify_daily_streams"])
    track.streams.spotify_streams_updated = _clean(row["spotify_streams_updated"])
    track.streams.ytm_streams = _clean_int(row["ytm_streams"])
    track.streams.ytm_streams_updated = _clean(row["ytm_streams_updated"])
    track.album_override = _clean_int(row["album_override"])

    # Chantier « Media » : chemins d'images (Text) + vidéo YouTube. La date
    # `youtube_video_views_updated` reste brute (piège TIMESTAMP, comme *_updated).
    track.cover_path = _clean(row["cover_path"])
    track.yt_thumbnail_path = _clean(row["yt_thumbnail_path"])
    track.youtube_video_kind = _clean(row["youtube_video_kind"])
    track.youtube_video_views = _clean_int(row["youtube_video_views"])
    track.youtube_video_views_updated = _clean(row["youtube_video_views_updated"])

    relationships_raw = row["relationships"]
    try:
        track.relationships = json.loads(relationships_raw) if relationships_raw else []
    except (ValueError, TypeError):
        track.relationships = []

    track.duration = _clean_duration(row["duration"])  # Supporte "3:48" et int
    track.genre = _clean(row["genre"])
    # E7-D2 : key/mode/musical_key/time_signature DROPPÉS → None ici, pilotés par
    # la réconciliation des observations (apply_resolutions, bas de fonction) qui
    # normalise key/mode et RECALCULE musical_key (key_mode_to_french, déjà
    # canonique) — l'ancien self-healing sur la colonne devient inutile.
    track.audio.key = None
    track.audio.mode = None
    track.audio.musical_key = None
    track.audio.time_signature = None
    track.genius_url = _clean(row["genius_url"])
    track.spotify_url = _clean(row["spotify_url"])
    track.spotify_page_title = _clean(row["spotify_page_title"])
    track.created_at = _clean(row["created_at"])
    track.updated_at = _clean(row["updated_at"])
    track.last_scraped = _clean(row["last_scraped"])

    # Propriétés featuring
    track.is_featuring = bool(_clean(row["is_featuring"], False))
    track.primary_artist_name = _clean(row["primary_artist_name"])
    track.featured_artists = _clean(row["featured_artists"])
    track.secondary_role = _clean(row["secondary_role"])

    # Propriétés paroles
    track.lyrics = _clean(row["lyrics"])
    track.anecdotes = _clean(row["anecdotes"])
    track.has_lyrics = bool(_clean(row["has_lyrics"], False))
    track.lyrics_scraped_at = _clean(row["lyrics_scraped_at"])

    # Désérialiser les certifications JSON
    certifications_json = row["certifications"]
    try:
        if certifications_json:
            track.certifications = json.loads(certifications_json)
            # Champs de rétrocompatibilité (plus haute certification)
            if track.certifications:
                highest = track.certifications[0]
                track.has_certification = True
                track.certification_level = highest.get("certification")
                track.certification_date = highest.get("certification_date")
        else:
            track.certifications = []
    except (ValueError, TypeError, json.JSONDecodeError):
        logger.debug(
            f"JSON certifications invalide pour track {track_id}: {certifications_json!r:.100}"
        )
        track.certifications = []

    album_certifications_json = row["album_certifications"]
    try:
        if album_certifications_json:
            track.album_certifications = json.loads(album_certifications_json)
        else:
            track.album_certifications = []
    except (ValueError, TypeError, json.JSONDecodeError):
        logger.debug(
            f"JSON album_certifications invalide pour track {track_id}: "
            f"{album_certifications_json!r:.100}"
        )
        track.album_certifications = []

    # E6 : les observations pilotent l'audio réconciliable (bpm/key/mode), en
    # écrasant les colonnes legacy déjà posées ci-dessus. Champ sans observation
    # = colonne legacy conservée (fallback). Import local (anti-cycle utils↔enrich).
    if observations:
        from src.enrichment.reconcile import apply_resolutions, reconcile

        # track.duration posé plus haut (l.135) : alimente la stratégie
        # lyrics_synced (départage par durée réelle dans compare_synced).
        apply_resolutions(track, reconcile(observations, track_duration=track.duration))

    return track
