"""Enrichissement des données avec les certifications.

Cœur : `apply_certifications(artist, tracks, matcher)` (E7g) — rematche chaque
morceau/album contre les CSV clean (matcher en mémoire, offline, rapide) et pose
`track.certifications`/`album_certifications`. La MATÉRIALISATION passe par des
objets typés `Certification` (`from_match`) puis se re-sérialise au format
colonne (`to_column_dict`), byte-compatible avec `cert_matcher._format` (contrat
mapper/GUI inchangé). Ne PERSISTE pas : l'appelant (worker retrieval, E7h) save.
"""

from src.models import Artist, Track
from src.models.certification import Certification
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Substitutions Unicode courantes des titres avant le matching (apostrophes
# courbes, ligatures œ) — alignées sur l'historique de enrich_tracks.
_TITLE_SUBS = {"’": "'", "‘": "'", "œ": "œ", "Œ": "Œ"}


def _normalize_title(title: str) -> str:
    for bad, good in _TITLE_SUBS.items():
        title = title.replace(bad, good)
    return title


def _extra_artists(track: Track, artist_name: str) -> list[str]:
    """Artistes candidats supplémentaires : si NOTRE artiste est secondaire/feat,
    la certif peut être déposée sous l'artiste PRINCIPAL → on le passe pour la
    rattacher quand même."""
    extra: list[str] = []
    pan = getattr(track, "primary_artist_name", None)
    if pan and pan != artist_name:
        extra.append(pan)
    fa = getattr(track, "featured_artists", None)
    if isinstance(fa, str) and fa:
        extra.append(fa)
    elif isinstance(fa, (list, tuple)):
        extra.extend(str(x) for x in fa if x)
    return extra


def apply_certifications(artist: Artist, tracks: list[Track], matcher) -> int:
    """Pose `track.certifications`/`album_certifications` depuis le matcher unifié.

    Matérialise chaque correspondance en `Certification` (frontière typée) puis la
    re-sérialise au format colonne. Renvoie le nombre de morceaux portant au moins
    une certification. Offline (matcher en mémoire), NE PERSISTE PAS.
    """
    if not tracks or not artist:
        return 0

    enriched = 0
    album_cache: dict[str, list[dict]] = {}  # évite de re-chercher le même album

    for track in tracks:
        try:
            title = _normalize_title(track.title)
            extra = _extra_artists(track, artist.name)

            matches = matcher.get_track_certifications(artist.name, title, extra_artists=extra)
            track.certifications = [Certification.from_match(m).to_column_dict() for m in matches]

            if track.certifications:
                highest = track.certifications[0]  # déjà trié par priorité
                track.has_certification = True
                track.certification_level = highest.get("certification", "")
                track.certification_date = highest.get("certification_date", "")
                # Durée d'obtention (écart sortie→certif) de la plus haute certif.
                track.calculate_certification_duration()
                enriched += 1
            else:
                track.has_certification = False
                track.certification_level = None
                track.certification_date = None
                track.certification_duration_days = None

            if track.album:
                if track.album not in album_cache:
                    album_cache[track.album] = matcher.get_album_certifications(
                        artist.name, track.album
                    )
                track.album_certifications = [
                    Certification.from_match(m).to_column_dict() for m in album_cache[track.album]
                ]
            else:
                track.album_certifications = []
        except Exception as e:
            logger.error(f"Erreur enrichissement {track.title}: {e}")
            track.certifications = []
            track.album_certifications = []
            track.has_certification = False

    if enriched:
        logger.info(f"🏆 {enriched}/{len(tracks)} morceaux enrichis avec certifications")
    albums_with_certs = sum(1 for t in tracks if t.album_certifications)
    if albums_with_certs:
        logger.info(f"💿 {albums_with_certs}/{len(tracks)} morceaux ont des certifs d'album")
    return enriched
