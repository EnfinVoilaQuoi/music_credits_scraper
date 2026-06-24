"""
Module YouTube Music — récupération des streams via ytmusicapi + YouTube Data API v3.

Stratégie quota-optimisée :
  1. ytmusicapi récupère tous les videoId de tous les albums (gratuit, pas de quota)
  2. Un seul passage en batch sur YouTube Data API v3 (50 IDs/requête, 1 unité/requête)
     → ex. 150 tracks = 3 requêtes = 3 unités (quota daily : 10 000)
  3. Fallback sur le champ `views` formaté de ytmusicapi si pas de clé ou erreur API
"""
import re
import logging
import unicodedata
from typing import Dict, List, Optional

from ytmusicapi import YTMusic

logger = logging.getLogger('YTMusicAPI')

try:
    from src.config import YOUTUBE_API_KEY
except ImportError:
    import os
    YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')

_YT_BATCH_SIZE = 50  # limite YouTube Data API v3


# ── Utilitaires ───────────────────────────────────────────────────────────────

def _normalize(s: str) -> str:
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', s).strip().lower()


def _parse_views(text: str) -> Optional[int]:
    """Convertit une chaîne YTMusic formatée en entier (fallback sans API key).

    Exemples : "1,2 M" → 1200000 | "500 k" → 500000 | "1 234 567" → 1234567
    Gère l'espace insécable (\\xa0) utilisé par YTMusic comme séparateur.
    """
    text = text.strip().replace('\xa0', ' ')
    for suffix, mult in [
        ('md', 1_000_000_000),
        ('b',  1_000_000_000),
        ('m',  1_000_000),
        ('k',  1_000),
    ]:
        if text.lower().endswith(suffix):
            num_part = text[:-len(suffix)].strip().replace(',', '.').replace(' ', '')
            try:
                return int(float(num_part) * mult)
            except ValueError:
                return None
    cleaned = re.sub(r'[^\d]', '', text)
    return int(cleaned) if cleaned else None


# ── Classe principale ─────────────────────────────────────────────────────────

class YTMusicAPI:
    """Wrapper ytmusicapi + YouTube Data API v3 pour les streams d'un artiste.

    Usage recommandé (quota-optimal) :
        api = YTMusicAPI()
        channel_id = api.get_artist_channel_id(artist_name)
        albums = api.get_artist_albums(channel_id)

        # Étape 1 : collecter tous les tracks via ytmusicapi (pas de quota)
        all_tracks = {}
        all_video_ids = []
        for album in albums:
            tracks = api.get_album_tracks_raw(album['browseId'])
            all_tracks[album['title']] = tracks
            all_video_ids += [t['video_id'] for t in tracks if t['video_id']]

        # Étape 2 : UNE seule passe YouTube API pour tous les IDs
        view_counts = api.fetch_view_counts_batch(all_video_ids)

        # Étape 3 : résoudre les streams par track
        for album_title, tracks in all_tracks.items():
            for t in tracks:
                streams = api.resolve_streams(t, view_counts)
    """

    def __init__(self):
        self.yt = YTMusic()
        self._use_yt_api = bool(YOUTUBE_API_KEY)
        if self._use_yt_api:
            logger.info("YTMusicAPI : YouTube Data API v3 disponible (viewCount exact)")
        else:
            logger.warning(
                "YTMusicAPI : YOUTUBE_API_KEY absent — "
                "fallback sur ytmusicapi views (valeur arrondie)"
            )

    # ── Recherche artiste / albums ─────────────────────────────────────────────

    def resolve_channel(self, value: str) -> Optional[str]:
        """
        Résout un canal YTMusic depuis : un ID 'UC...', un handle '@ISHAOfficiel',
        ou une URL music.youtube.com/@handle ou /channel/UC...
        """
        value = (value or '').strip()
        if not value:
            return None

        # Déjà un channel ID
        m = re.search(r'(UC[A-Za-z0-9_-]{22})', value)
        if m:
            return m.group(1)

        # Extraire le handle (@xxx)
        h = re.search(r'@([A-Za-z0-9._-]+)', value)
        if not h:
            logger.warning(f"resolve_channel: format non reconnu: {value!r}")
            return None
        handle = h.group(1)

        # 1. YouTube Data API v3 (forHandle) — fiable
        if self._use_yt_api:
            try:
                from googleapiclient.discovery import build
                yt = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
                resp = yt.channels().list(part='id', forHandle='@' + handle).execute()
                items = resp.get('items') or []
                if items:
                    cid = items[0]['id']
                    logger.info(f"✅ Handle @{handle} résolu via API: {cid}")
                    return cid
            except Exception as e:
                logger.warning(f"resolve_channel API échec pour @{handle}: {e}")

        # 2. Fallback : page music.youtube.com/@handle
        try:
            import requests as _rq
            r = _rq.get(
                f"https://music.youtube.com/@{handle}",
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'},
                timeout=15,
            )
            m = re.search(r'"(?:channelId|browseId)"\s*:\s*"(UC[A-Za-z0-9_-]{22})"', r.text)
            if m:
                logger.info(f"✅ Handle @{handle} résolu via page: {m.group(1)}")
                return m.group(1)
        except Exception as e:
            logger.warning(f"resolve_channel page échec pour @{handle}: {e}")

        return None

    def infer_channel_from_videos(self, video_ids: List[str]) -> Optional[str]:
        """
        Déduit le canal artiste par VOTE MAJORITAIRE sur les chaînes des vidéos
        (les liens YT auto-sélectionnés à confiance ≥ 0.8 pointent presque
        toujours vers le canal officiel). 1 unité de quota par lot de 50.
        """
        if not video_ids or not self._use_yt_api:
            return None
        try:
            from googleapiclient.discovery import build
            from collections import Counter

            yt = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
            resp = yt.videos().list(
                part='snippet', id=','.join(video_ids[:50])
            ).execute()

            votes = Counter()
            names = {}
            for item in resp.get('items', []):
                snip = item.get('snippet', {})
                cid = snip.get('channelId')
                if cid:
                    votes[cid] += 1
                    names[cid] = snip.get('channelTitle', '')

            if not votes:
                return None
            best, count = votes.most_common(1)[0]
            # Majorité stricte exigée (≥ 2 voix et > moitié des votes)
            if count >= 2 and count > sum(votes.values()) / 2:
                logger.info(
                    f"🗳️ Canal déduit des vidéos YT: '{names.get(best)}' ({best}) "
                    f"— {count}/{sum(votes.values())} voix"
                )
                return best
            logger.info(f"🗳️ Vote canal non concluant: {dict(votes)}")
            return None
        except Exception as e:
            logger.warning(f"infer_channel_from_videos échec: {e}")
            return None

    def get_artist_channel_id(self, artist_name: str) -> Optional[str]:
        """Recherche un artiste et retourne son browseId YTMusic."""
        candidates = self.get_artist_channel_candidates(artist_name)
        return candidates[0][0] if candidates else None

    def get_artist_channel_candidates(self, artist_name: str, limit: int = 5) -> list:
        """
        Retourne les canaux candidats [(browseId, nom), ...], matchs exacts
        d'abord. Gère les homonymes (plusieurs artistes 'Isha') : l'appelant
        essaie les candidats jusqu'à en trouver un avec des albums.
        """
        try:
            results = self.yt.search(artist_name, filter='artists', limit=limit)
            if not results:
                logger.warning(f"Aucun résultat artiste YTMusic pour '{artist_name}'")
                return []
            norm_target = _normalize(artist_name)
            exact = [r for r in results
                     if r.get('browseId') and _normalize(r.get('artist', '')) == norm_target]
            others = [r for r in results
                      if r.get('browseId') and r not in exact]
            candidates = [(r['browseId'], r.get('artist', '')) for r in exact + others]
            logger.info(
                f"YTMusic candidats pour '{artist_name}': "
                + ", ".join(f"{n} ({b})" for b, n in candidates[:3])
            )
            return candidates
        except Exception as e:
            logger.error(f"Erreur get_artist_channel_candidates pour '{artist_name}': {e}")
            return []

    def get_artist_info(self, channel_id: str) -> Dict:
        """Retourne les infos artiste : albums, singles et auditeurs mensuels.

        Un seul appel API ytmusicapi — évite de doubler les requêtes.

        Returns:
            {
              'albums': [{'title': str, 'browseId': str}],
              'monthly_listeners': int|None,
            }
        """
        try:
            artist = self.yt.get_artist(channel_id)
            items = (
                artist.get('albums', {}).get('results', []) +
                artist.get('singles', {}).get('results', [])
            )
            albums = [
                {'title': a['title'], 'browseId': a['browseId']}
                for a in items if 'browseId' in a
            ]
            # monthlyListeners est un int ou None selon l'artiste
            raw_ml = artist.get('monthlyListeners')
            monthly_listeners = int(raw_ml) if raw_ml else None
            logger.info(
                f"YTMusic get_artist_info: {len(albums)} albums, "
                f"auditeurs/mois={monthly_listeners}"
            )
            return {'albums': albums, 'monthly_listeners': monthly_listeners}
        except Exception as e:
            logger.error(f"Erreur get_artist_info ({channel_id}): {e}")
            return {'albums': [], 'monthly_listeners': None}

    def get_artist_albums(self, channel_id: str) -> List[Dict]:
        """Retourne albums + singles d'un artiste.

        Returns:
            [{'title': str, 'browseId': str}]
        """
        try:
            artist = self.yt.get_artist(channel_id)
            items = (
                artist.get('albums', {}).get('results', []) +
                artist.get('singles', {}).get('results', [])
            )
            result = [
                {'title': a['title'], 'browseId': a['browseId']}
                for a in items if 'browseId' in a
            ]
            logger.info(f"YTMusic: {len(result)} album(s)/single(s) trouvés pour {channel_id}")
            return result
        except Exception as e:
            logger.error(f"Erreur get_artist_albums ({channel_id}): {e}")
            return []

    # ── Étape 1 : tracks raw (ytmusicapi, zéro quota YT) ──────────────────────

    def get_album_tracks_raw(self, browse_id: str) -> List[Dict]:
        """Retourne les tracks d'un album avec leur videoId et views_str.

        N'appelle PAS YouTube Data API — zéro consommation de quota.

        Returns:
            [{'title': str, 'video_id': str|None, 'views_str': str|None}]
        """
        try:
            album = self.yt.get_album(browse_id)
            return [
                {
                    'title': t.get('title', ''),
                    'video_id': t.get('videoId'),
                    'views_str': t.get('views'),
                }
                for t in album.get('tracks', [])
            ]
        except Exception as e:
            logger.error(f"Erreur get_album_tracks_raw ({browse_id}): {e}")
            return []

    # ── Paroles (YTMusic, sans quota, source primaire) ───────────────────────

    @staticmethod
    def _format_lrc(lines: list) -> Optional[str]:
        """Convertit des lignes synchronisées (LyricLine) en texte LRC [mm:ss.cc]."""
        out = []
        any_ts = False
        for l in lines:
            if isinstance(l, dict):
                text, start = l.get('text', ''), l.get('start_time')
            else:
                text, start = getattr(l, 'text', ''), getattr(l, 'start_time', None)
            if start is None:
                out.append(text)
                continue
            any_ts = True
            ms = int(start)
            m, s, cs = ms // 60000, (ms % 60000) // 1000, (ms % 1000) // 10
            out.append(f"[{m:02d}:{s:02d}.{cs:02d}]{text}")
        return "\n".join(out) if any_ts else None

    def get_lyrics(self, artist: str, title: str) -> Optional[Dict]:
        """
        Récupère les paroles via YTMusic : search(songs) → videoId →
        get_watch_playlist → browseId paroles → get_lyrics.
        Vérifie que l'artiste correspond (évite des paroles erronées).
        Récupère aussi la version SYNCHRONISÉE (LRC) quand la source la fournit.

        Returns:
            {'lyrics': str, 'lyrics_synced': str|None, 'source': str} ou None.
        """
        try:
            results = self.yt.search(f"{artist} {title}", filter='songs', limit=3)
            if not results:
                return None

            na = _normalize(artist)
            chosen = None
            for r in results:
                if not r.get('videoId'):
                    continue
                arts = " ".join(a.get('name', '') for a in (r.get('artists') or []))
                if na in _normalize(arts) or _normalize(arts) in na:
                    chosen = r
                    break
            if not chosen:
                logger.debug(f"YTM lyrics: artiste non confirmé pour '{artist} - {title}'")
                return None

            watch = self.yt.get_watch_playlist(videoId=chosen['videoId'])
            lyrics_id = watch.get('lyrics') if isinstance(watch, dict) else None
            if not lyrics_id:
                return None

            # Demander la version synchronisée ; fallback texte brut si indispo
            try:
                data = self.yt.get_lyrics(lyrics_id, timestamps=True)
            except Exception:
                data = self.yt.get_lyrics(lyrics_id)

            raw = data.get('lyrics') if isinstance(data, dict) else None
            synced = None
            if isinstance(raw, list):
                synced = self._format_lrc(raw)
                text = "\n".join(
                    (l.get('text', '') if isinstance(l, dict) else getattr(l, 'text', ''))
                    for l in raw
                )
            else:
                text = raw

            if not text or not str(text).strip():
                return None

            source = (data.get('source') if isinstance(data, dict) else None) or 'YouTube Music'
            logger.info(
                f"📝 YTM paroles: '{artist} - {title}' (source: {source}"
                f"{', synchro' if synced else ''})"
            )
            return {'lyrics': str(text).strip(), 'lyrics_synced': synced, 'source': source}
        except Exception as e:
            logger.debug(f"YTM get_lyrics échec '{artist} - {title}': {e}")
            return None

    # ── Étape 2 : batch YouTube Data API v3 (quota-optimal) ───────────────────

    def fetch_view_counts_batch(self, video_ids: List[str]) -> Dict[str, int]:
        """Récupère les viewCounts exacts pour une liste de videoId.

        Regroupe automatiquement en batches de 50 pour minimiser les requêtes API.
        Retourne {} si YOUTUBE_API_KEY absent ou erreur.

        Returns:
            {videoId: viewCount}
        """
        if not video_ids:
            return {}

        if not self._use_yt_api:
            logger.debug("fetch_view_counts_batch ignoré : pas de YOUTUBE_API_KEY")
            return {}

        try:
            from googleapiclient.discovery import build
        except ImportError:
            logger.warning("google-api-python-client non installé — pip install google-api-python-client")
            return {}

        counts: Dict[str, int] = {}
        unique_ids = list(dict.fromkeys(video_ids))  # déduplique en préservant l'ordre
        n_batches = (len(unique_ids) + _YT_BATCH_SIZE - 1) // _YT_BATCH_SIZE

        logger.info(
            f"YouTube Data API v3 : {len(unique_ids)} videoId → "
            f"{n_batches} requête(s) (~{n_batches} unité(s) de quota)"
        )

        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)

        for i in range(0, len(unique_ids), _YT_BATCH_SIZE):
            batch = unique_ids[i:i + _YT_BATCH_SIZE]
            try:
                response = youtube.videos().list(
                    part='statistics',
                    id=','.join(batch)
                ).execute()
                for item in response.get('items', []):
                    view_str = item.get('statistics', {}).get('viewCount')
                    if view_str is not None:
                        counts[item['id']] = int(view_str)
            except Exception as e:
                logger.error(f"Erreur YouTube Data API v3 (batch {i // _YT_BATCH_SIZE + 1}): {e}")

        logger.info(f"YouTube Data API v3 : {len(counts)}/{len(unique_ids)} viewCounts récupérés")
        return counts

    # ── Étape 3 : résolution streams par track ─────────────────────────────────

    @staticmethod
    def resolve_streams(track_raw: Dict, view_counts: Dict[str, int]) -> Optional[int]:
        """Résout le nombre de streams pour un track raw.

        Priorité :
          1. viewCount exact (YouTube Data API v3)
          2. views_str formaté (ytmusicapi, fallback arrondi)
        """
        vid = track_raw.get('video_id')
        if vid and vid in view_counts:
            return view_counts[vid]
        views_str = track_raw.get('views_str')
        if views_str:
            return _parse_views(views_str)
        return None
