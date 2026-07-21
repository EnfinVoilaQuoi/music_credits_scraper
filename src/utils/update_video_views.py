"""Vues des clips/shows + différenciation clip / morceau « classique ».

Chantier « Media », étape « compteurs ». Collecte les videoId des ``youtube_url``
d'un artiste, fait UN batch `fetch_video_meta_batch` (vues + titre + chaîne, coût
quota identique aux streams), classifie chaque vidéo (`classify_video_kind`) et
écrit ``youtube_video_kind`` + ``youtube_video_views`` via
``update_track_video_views``.

**Séparé de `update_ytmusic_streams`** : ``ytm_streams`` reste la somme audio+clip
(streams musicaux) ; ici on mesure LA vidéo (clip/show/live) et sa nature —
c'est la réponse au « bien différencier un clip d'un morceau classique ». Cet
updater ÉCRIT en base (comme `update_ytmusic_streams`), et mute aussi les objets
en mémoire pour un affichage immédiat.
"""

from src.utils.logger import get_logger
from src.utils.youtube_utils import classify_video_kind, extract_video_id

logger = get_logger(__name__)


def update_video_views(artist, tracks, data_manager, api=None) -> dict:
    """Met à jour vues + kind de la vidéo YouTube de chaque morceau.

    ``api`` : YTMusicAPI injecté (StreamsProvider) ; créé en interne si None.

    Returns:
        Rapport ``{"updated", "no_video_id", "no_meta", "by_kind": {...}}``.
    """
    report = {"updated": 0, "no_video_id": 0, "no_meta": 0, "by_kind": {}}

    pairs = []  # (track, video_id)
    for track in tracks:
        vid = extract_video_id(track.youtube_url)
        if vid:
            pairs.append((track, vid))
        else:
            report["no_video_id"] += 1

    if not pairs:
        logger.info(f"Vues clips : aucun lien YouTube exploitable pour {artist.name}")
        return report

    if api is None:
        from src.api.ytmusic_api import YTMusicAPI

        api = YTMusicAPI()
    meta = api.fetch_video_meta_batch([vid for _, vid in pairs])

    for track, vid in pairs:
        info = meta.get(vid)
        if not info:
            report["no_meta"] += 1
            continue
        kind = classify_video_kind(info.get("title"), info.get("channel"))
        views = info.get("views")
        if data_manager.update_track_video_views(track.id, views, kind):
            # Mutation mémoire (affichage immédiat, pas de reload nécessaire)
            track.media.youtube_video_kind = kind
            track.media.youtube_video_views = views
            report["updated"] += 1
            report["by_kind"][kind] = report["by_kind"].get(kind, 0) + 1

    logger.info(
        f"Vues clips {artist.name} : {report['updated']} mis à jour "
        f"({report['by_kind']}), {report['no_meta']} sans méta, "
        f"{report['no_video_id']} sans videoId"
    )
    return report
