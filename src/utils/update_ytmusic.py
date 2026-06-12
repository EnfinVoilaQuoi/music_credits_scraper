"""Mise à jour des streams YouTube Music pour les morceaux et albums d'un artiste.

Architecture quota-optimisée :
  Étape 1 — ytmusicapi collecte tous les videoId (tous albums, zéro quota YT)
  Étape 2 — UN seul passage YouTube Data API v3 avec tous les IDs en batch
  Étape 3 — Matching normalisé titre → DB + écriture en base
"""
import re
import sys
import io
import unicodedata
import logging
from pathlib import Path
from typing import Dict, List, Optional

sys.path.append(str(Path(__file__).parent.parent.parent))

from src.api.ytmusic_api import YTMusicAPI
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _normalize_title(s: str) -> str:
    # Retirer les suffixes featuring : "Titre (feat. X)" → "Titre"
    s = re.sub(r'\s*[\(\[]\s*(?:feat|ft|avec|with)\.?[^\)\]]*[\)\]]', '', s, flags=re.IGNORECASE)
    # Unifier/supprimer les apostrophes (typographiques ou droites)
    s = re.sub(r"['’‘`´]", '', s)
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', s).strip().lower()


def _infer_channel_from_youtube_links(api, artist, data_manager,
                                      max_votes: int = 8,
                                      max_attempts: int = 15) -> 'Optional[str]':
    """
    Déduit le canal YTM de l'artiste en croisant les liens YouTube
    auto-sélectionnés (confiance ≥ 0.8) : vote majoritaire sur les chaînes
    propriétaires des vidéos. Fiable à ~99% dès 2-3 morceaux connus.
    """
    try:
        from src.utils.youtube_integration import youtube_integration
    except Exception:
        return None

    try:
        tracks = data_manager.get_artist_tracks(artist.id)
    except Exception:
        return None
    if not tracks:
        return None

    video_ids = []
    attempts = 0
    for t in tracks:
        if len(video_ids) >= max_votes or attempts >= max_attempts:
            break
        attempts += 1
        try:
            res = youtube_integration.get_youtube_link_for_track(
                artist.name, t.title, album=getattr(t, 'album', None)
            )
        except Exception:
            continue
        if res.get('type') == 'direct' and res.get('confidence', 0) >= 0.8:
            m = re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', res.get('url', ''))
            if m:
                video_ids.append(m.group(1))

    if len(video_ids) < 2:
        logger.debug(f"Inférence canal YT: seulement {len(video_ids)} vidéo(s) fiable(s)")
        return None

    logger.info(f"🗳️ Inférence du canal via {len(video_ids)} vidéo(s) à confiance ≥ 0.8")
    return api.infer_channel_from_videos(video_ids)


def update_ytmusic_streams(artist, data_manager) -> Dict:
    """Met à jour les streams YouTube Music des morceaux et albums de l'artiste.

    Args:
        artist: objet Artist avec `id` et `name`
        data_manager: instance de DataManager

    Returns:
        dict résumé {matched, unmatched, albums_processed, yt_api_calls, unmatched_titles}
    """
    result = {
        'matched': 0,
        'unmatched': 0,
        'albums_processed': 0,
        'yt_api_calls': 0,
        'unmatched_titles': [],
    }

    api = YTMusicAPI()

    # ── Étape 0 : ID artiste YTMusic (gestion des homonymes) ──────────────────
    # 1. Canal épinglé manuellement ? (résout définitivement les homonymes)
    pinned = None
    try:
        pinned = data_manager.get_artist_ytm_channel(artist.id)
    except Exception:
        pass

    # 2. Sinon, déduire le canal par vote sur les vidéos YT à haute confiance
    if not pinned:
        inferred = _infer_channel_from_youtube_links(api, artist, data_manager)
        if inferred:
            pinned = inferred
            try:
                data_manager.set_artist_ytm_channel(artist.id, inferred)
            except Exception:
                pass

    if pinned:
        logger.info(f"📌 Canal YTM utilisé: {pinned}")
        candidates = [(pinned, artist.name)]
    else:
        # 3. Dernier recours : recherche par nom (candidats avec albums d'abord)
        candidates = api.get_artist_channel_candidates(artist.name)
    if not candidates:
        logger.error(f"Artiste '{artist.name}' introuvable sur YouTube Music.")
        return result

    # Essayer les candidats jusqu'à en trouver un avec des albums
    # (plusieurs artistes peuvent porter le même nom, ex: 'Isha')
    channel_id, artist_info = None, None
    for cid, cname in candidates:
        info = api.get_artist_info(cid)
        if info.get('albums'):
            channel_id, artist_info = cid, info
            logger.info(
                f"Canal YTMusic retenu: '{cname}' ({cid}) — "
                f"{len(info['albums'])} album(s)"
            )
            break
        logger.info(f"Canal YTMusic '{cname}' ({cid}) sans albums — candidat suivant")

    if artist_info is None:
        channel_id = candidates[0][0]
        artist_info = api.get_artist_info(channel_id)

    albums = artist_info['albums']
    ytm_monthly_listeners = artist_info['monthly_listeners']

    if ytm_monthly_listeners:
        data_manager.update_artist_monthly_listeners(
            artist.id, ytm_listeners=ytm_monthly_listeners
        )
        logger.info(f"Auditeurs mensuels YTMusic : {ytm_monthly_listeners:,}")

    if not albums:
        logger.warning(f"Aucun album YTMusic pour '{artist.name}'")
        return result

    # ── Étape 1 : collecter tous les tracks via ytmusicapi (zéro quota YT) ───
    tracks_by_album: Dict[str, List[Dict]] = {}
    all_video_ids: List[str] = []

    for album_info in albums:
        raw_tracks = api.get_album_tracks_raw(album_info['browseId'])
        tracks_by_album[album_info['title']] = raw_tracks
        all_video_ids.extend(t['video_id'] for t in raw_tracks if t.get('video_id'))

    logger.info(
        f"ytmusicapi : {len(albums)} album(s), "
        f"{sum(len(v) for v in tracks_by_album.values())} tracks, "
        f"{len(all_video_ids)} videoId collectés"
    )

    # ── Étape 2 : UNE seule passe YouTube Data API v3 pour tous les IDs ──────
    view_counts = api.fetch_view_counts_batch(all_video_ids)
    # Estimer le nb de requêtes effectuées
    result['yt_api_calls'] = (len(all_video_ids) + 49) // 50 if all_video_ids else 0

    # ── Étape 3 : matching DB + mise à jour ───────────────────────────────────
    db_tracks = data_manager.get_artist_tracks(artist.id)
    track_index: Dict[str, object] = {
        _normalize_title(t.title): t for t in db_tracks
    }

    for album_title, raw_tracks in tracks_by_album.items():
        album_total_streams = 0

        for entry in raw_tracks:
            streams = api.resolve_streams(entry, view_counts)
            norm = _normalize_title(entry['title'])
            matched_track = track_index.get(norm)

            if matched_track:
                if streams is not None:
                    data_manager.update_track_ytm_streams(matched_track.id, streams)
                    album_total_streams += streams
                result['matched'] += 1
                logger.debug(
                    f"✅ Match YTM: '{entry['title']}' → "
                    f"{streams:,}" if streams is not None else f"✅ Match YTM: '{entry['title']}' (streams N/A)"
                )
            else:
                result['unmatched'] += 1
                result['unmatched_titles'].append(entry['title'])
                logger.debug(f"⚠️ Pas de match DB: '{entry['title']}'")

        if album_total_streams > 0:
            data_manager.update_album_ytm_streams(artist.id, album_title, album_total_streams)

        result['albums_processed'] += 1

    logger.info(
        f"YTMusic terminé : {result['matched']} matchés, "
        f"{result['unmatched']} non matchés, "
        f"{result['albums_processed']} albums, "
        f"{result['yt_api_calls']} requête(s) YouTube API"
    )
    if result['unmatched_titles']:
        logger.warning(f"Titres YTMusic non matchés : {result['unmatched_titles']}")

    return result


# ── CLI standalone ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if sys.platform == 'win32':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    import argparse

    parser = argparse.ArgumentParser(
        description='Met à jour les streams YouTube Music pour un artiste')
    parser.add_argument('artist_name', help="Nom exact de l'artiste dans la DB")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s — %(message)s',
    )

    from src.utils.data_manager import DataManager

    dm = DataManager()
    artist = dm.get_artist_by_name(args.artist_name)
    if not artist:
        print(f"❌ Artiste '{args.artist_name}' non trouvé en base de données.")
        sys.exit(1)

    summary = update_ytmusic_streams(artist, dm)
    print('\n── Résumé YTMusic ──────────────────────────────────')
    print(f"Morceaux matchés      : {summary['matched']}")
    print(f"Morceaux non matchés  : {summary['unmatched']}")
    print(f"Albums traités        : {summary['albums_processed']}")
    print(f"Requêtes YouTube API  : {summary['yt_api_calls']}")
    if summary['unmatched_titles']:
        print(f"Titres non matchés    : {summary['unmatched_titles']}")
