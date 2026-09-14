"""Enrichissement des données avec les certifications.

Cœur : `apply_certifications(artist, tracks, matcher)` (E7g) — rematche chaque
morceau/album contre les CSV clean (matcher en mémoire, offline, rapide) et pose
`track.certs.entries`/`album_certifications`. La MATÉRIALISATION passe par des
objets typés `Certification` (`from_match`) puis se re-sérialise au format
colonne (`to_column_dict`), byte-compatible avec `cert_matcher._format` (contrat
mapper/GUI inchangé). Ne PERSISTE pas : l'appelant (worker retrieval, E7h) save.
"""

from src.models import Artist, Track
from src.models.certification import Certification
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Substitutions Unicode courantes des titres avant le matching (apostrophes
# courbes). Les entrées « œ→œ »/« Œ→Œ » d'origine étaient des no-op (même
# codepoint des deux côtés) — retirées.
_TITLE_SUBS = {"’": "'", "‘": "'"}


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
    """Pose `track.certs.entries`/`album_certifications` depuis le matcher unifié.

    Matérialise chaque correspondance en `Certification` (frontière typée) puis la
    re-sérialise au format colonne. Renvoie le nombre de morceaux portant au moins
    une certification. Offline (matcher en mémoire), NE PERSISTE PAS.
    """
    if not tracks or not artist:
        return 0

    enriched = 0
    echecs = 0
    album_cache: dict[str, list[dict]] = {}  # évite de re-chercher le même album

    for track in tracks:
        try:
            title = _normalize_title(track.title)
            extra = _extra_artists(track, artist.name)

            matches = matcher.get_track_certifications(artist.name, title, extra_artists=extra)
            track.certs.entries = [Certification.from_match(m).to_column_dict() for m in matches]
            # Recalculé : `save_track` n'écrit plus ces colonnes, c'est
            # `DataManager.record_pending` qui le fera après le save.
            track.certs.needs_write = True

            if track.certs.entries:
                highest = track.certs.entries[0]  # déjà trié par priorité
                track.certs.has = True
                track.certs.level = highest.get("certification", "")
                track.certs.date = highest.get("certification_date", "")
                # Durée d'obtention (écart sortie→certif) de la plus haute certif.
                track.calculate_certification_duration()
                enriched += 1
            else:
                track.certs.has = False
                track.certs.level = None
                track.certs.date = None
                track.certs.duration_days = None

            if track.album:
                if track.album not in album_cache:
                    album_cache[track.album] = matcher.get_album_certifications(
                        artist.name, track.album
                    )
                track.certs.album_entries = [
                    Certification.from_match(m).to_column_dict() for m in album_cache[track.album]
                ]
            else:
                track.certs.album_entries = []
        # Les objets de match viennent du matcher : une forme inattendue ne doit
        # pas faire perdre le reste de la discographie.
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            logger.error(f"Erreur enrichissement {track.title}: {e}")
            echecs += 1
            # **On n'écrit RIEN.** Le repli posait deux listes vides avec
            # `needs_write = True`, or `record_certifications` est délibérément
            # AUTORITATIF : il peut retirer. « Recalculé, et vide » était donc
            # indiscernable d'un vrai retrait, et une seule ligne du magasin à
            # la date illisible suffisait à effacer en base les certifications
            # d'un morceau — sans qu'aucun garde-fou ne bronche, puisque
            # l'écriture, elle, réussissait.
            #
            # Une exception est un REFUS DE CONCLURE, jamais un résultat vide :
            # c'est la même règle qu'`indeterminate` côté observabilité. On garde
            # ce qui est en base et on le fait savoir en fin de flux.
            track.certs.needs_write = False

    if enriched:
        logger.info(f"🏆 {enriched}/{len(tracks)} morceaux enrichis avec certifications")
    albums_with_certs = sum(1 for t in tracks if t.certs.album_entries)
    if albums_with_certs:
        logger.info(f"💿 {albums_with_certs}/{len(tracks)} morceaux ont des certifs d'album")
    if echecs:
        # En ERROR, et en fin de flux : le compte doit être visible même quand
        # le run par ailleurs réussit. Ces morceaux gardent ce qu'ils avaient en
        # base — ils n'ont pas été recalculés, c'est tout, et c'est ce qu'il faut
        # savoir avant de conclure que la discographie est à jour.
        logger.error(
            f"⚠️ {echecs}/{len(tracks)} morceau(x) NON recalculé(s) (forme inattendue "
            "côté matcher) — leurs certifications en base sont conservées telles quelles"
        )
    return enriched
