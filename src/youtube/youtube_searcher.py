"""Recherche YouTube avec fallbacks et cache"""

import difflib
import pickle
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import requests
from ytmusicapi.exceptions import YTMusicError

from src.config import DATA_DIR, YOUTUBE_CACHE_TTL_HOURS
from src.utils.logger import get_logger
from src.utils.title_matching import normalize_name, normalize_title

logger = get_logger(__name__)


def cache_key(artist: str, title: str) -> str:
    """Clé de cache d'une recherche. UN seul calcul, pour que la lecture,
    l'écriture et la PURGE (rejet d'un lien) visent la même entrée."""
    return f"{artist}::{title}".replace(" ", "_").lower()


def relevance_score(
    result_title: str, result_artists: list[str], target_artist: str, target_title: str
) -> float:
    """Pertinence d'un résultat de recherche, entre 0 et 1 (titre 60 %, artiste 40 %).

    Fonction PURE, extraite de la méthode pour être mesurable hors réseau.

    Les deux côtés sont NORMALISÉS avant comparaison (`title_matching`) : la
    version brute comparait « S.O.A.B » à « SOAB », « Mauvaise Humeur » à
    « Mauvaise Humeur (feat. Leto) » ou « ISHA » à « isha », et faisait donc
    plafonner à 0,7-0,8 des paires PARFAITES — sous le seuil de persistance
    (0,90), si bien que le lien n'était jamais enregistré et les vues de la
    vidéo jamais comptées. Mesuré sur les 401 recherches du cache réel
    (2026-09-07) : **77 meilleurs résultats remontent**, dont 72 de « < 0,85 »
    à « ≥ 0,90 », et les exemples sont tous des appariements justes.

    Compromis ASSUMÉ : `normalize_title` ampute les suffixes « feat. X » des
    DEUX côtés — deux morceaux au même titre mais aux invités différents ne se
    distinguent donc plus par le titre. C'est la part artiste qui les sépare, et
    le cas des vrais homonymes reste traité en amont (`ambiguous` d'
    `update_ytmusic`). Le seuil de persistance, lui, n'est PAS abaissé : la
    contrepartie de ce relâchement est la validation humaine (✔️/✖️).
    """
    cible_titre = normalize_title(target_title)
    title_similarity = difflib.SequenceMatcher(
        None, cible_titre, normalize_title(result_title or "")
    ).ratio()

    cible_artiste = normalize_name(target_artist)
    artist_similarity = max(
        (
            difflib.SequenceMatcher(None, cible_artiste, normalize_name(a)).ratio()
            for a in result_artists
        ),
        default=0.0,
    )

    return (title_similarity * 0.6) + (artist_similarity * 0.4)


class YouTubeSearcher:
    """Recherche YouTube avec ytmusicapi et fallbacks"""

    def __init__(self):
        self.cache_db = DATA_DIR / "youtube_cache.db"
        self._init_cache()

        # Tenter d'initialiser ytmusicapi
        try:
            from ytmusicapi import YTMusic

            self.ytmusic = YTMusic()
            self.ytmusic_available = True
            logger.info("YTMusic initialisé avec succès")
        except ImportError:
            logger.warning("ytmusicapi non disponible, utilisation fallback requests")
            self.ytmusic = None
            self.ytmusic_available = False
        except (YTMusicError, requests.RequestException, OSError) as e:
            logger.warning(f"Erreur YTMusic: {e}, utilisation fallback")
            self.ytmusic = None
            self.ytmusic_available = False

    def _init_cache(self):
        """Initialise la base de données de cache"""
        self.cache_db.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.cache_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS youtube_search_cache (
                query_hash TEXT PRIMARY KEY,
                results BLOB,
                cached_at TIMESTAMP,
                expires_at TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    def search_track(self, artist: str, title: str, max_results: int = 25) -> list[dict]:
        """Recherche principale avec cache et fallbacks"""

        # Vérifier le cache d'abord
        cle = cache_key(artist, title)
        cached_result = self._get_cached_result(cle)
        if cached_result:
            logger.debug(f"Cache hit pour {artist} - {title}")
            return cached_result

        results = []

        # Méthode 1: ytmusicapi (recommandée)
        if self.ytmusic_available:
            try:
                results = self._search_with_ytmusic(artist, title, max_results)
                logger.info(f"ytmusicapi: {len(results)} résultats pour {artist} - {title}")
            except (YTMusicError, requests.RequestException, KeyError, TypeError, IndexError) as e:
                logger.warning(f"Erreur ytmusicapi: {e}")

        # Méthode 2: Fallback requests simple (si ytmusicapi échoue)
        if not results:
            try:
                results = self._search_with_requests_fallback(artist, title, max_results)
                logger.info(f"Fallback: {len(results)} résultats pour {artist} - {title}")
            except (requests.RequestException, ValueError, KeyError, TypeError) as e:
                logger.error(f"Erreur fallback: {e}")

        # Trier par pertinence
        if results:
            results = sorted(results, key=lambda x: x.get("relevance_score", 0), reverse=True)
            # Mettre en cache
            self._cache_result(cle, results)

        return results

    def forget(self, artist: str, title: str) -> bool:
        """Oublie la recherche mise en cache pour ce couple artiste/titre.

        Appelée quand l'utilisateur REJETTE un lien proposé automatiquement :
        sans elle, la prochaine ouverture de la fiche reproposerait le même
        mauvais résultat jusqu'à expiration du cache
        (`YOUTUBE_CACHE_TTL_HOURS`), et le rejet donnerait l'impression de
        n'avoir servi à rien.
        """
        try:
            # `closing` ferme (le contexte d'une connexion ne fait que
            # commit/rollback) ; le second `conn` garde le COMMIT à la sortie.
            with closing(sqlite3.connect(self.cache_db)) as conn, conn:
                supprimees = conn.execute(
                    "DELETE FROM youtube_search_cache WHERE query_hash = ?",
                    (cache_key(artist, title),),
                ).rowcount
            return supprimees > 0
        except (sqlite3.Error, OSError) as e:
            logger.warning(f"Purge du cache YouTube échouée ({artist} - {title}): {e}")
            return False

    def _search_with_ytmusic(self, artist: str, title: str, max_results: int) -> list[dict]:
        """Recherche avec ytmusicapi"""

        search_queries = [
            f"{artist} {title}",
            f'"{artist}" "{title}"',
            f"{artist} {title} official",
        ]

        all_results = []
        seen_video_ids = set()

        for query in search_queries:
            try:
                results = self.ytmusic.search(query, filter="songs", limit=15)

                for result in results:
                    video_id = result.get("videoId")
                    if video_id and video_id not in seen_video_ids:
                        seen_video_ids.add(video_id)

                        # Calculer score de pertinence
                        score = self._calculate_relevance_score(result, artist, title)

                        # Extraire les artistes
                        artists = []
                        for art in result.get("artists", []):
                            if isinstance(art, dict):
                                artists.append(art.get("name", ""))
                            else:
                                artists.append(str(art))

                        formatted_result = {
                            "video_id": video_id,
                            "title": result.get("title", ""),
                            "channel_title": artists[0] if artists else "Inconnu",
                            "channel_id": None,  # ytmusicapi ne fournit pas toujours l'ID
                            "duration": result.get("duration", ""),
                            "thumbnail_url": self._get_best_thumbnail(result.get("thumbnails", [])),
                            "relevance_score": score,
                            "source": "ytmusicapi",
                            "url": f"https://youtube.com/watch?v={video_id}",
                        }

                        all_results.append(formatted_result)

                        if len(all_results) >= max_results:
                            break

            except (YTMusicError, requests.RequestException, KeyError, TypeError, IndexError) as e:
                logger.debug(f"Erreur recherche ytmusicapi pour '{query}': {e}")
                continue

        return all_results[:max_results]

    def _search_with_requests_fallback(
        self, artist: str, title: str, max_results: int
    ) -> list[dict]:
        """Fallback avec requests basique (génère juste des liens de recherche)"""

        # Cette méthode génère des résultats "fictifs" pour tests
        # En production, vous pourriez utiliser yt-dlp ou autre

        search_queries = [
            f"{artist} {title}",
            f"{artist} {title} official",
            f"{artist} {title} lyrics",
        ]

        results = []

        for i, query in enumerate(search_queries):
            # Simuler un résultat de recherche
            from urllib.parse import quote

            search_url = f"https://www.youtube.com/results?search_query={quote(query)}"

            result = {
                "video_id": f"fallback_{i}",
                "title": f"{title} - {artist}",
                "channel_title": artist,
                "channel_id": None,
                "duration": "3:30",
                "thumbnail_url": None,
                "relevance_score": 0.8 - (i * 0.2),  # Score décroissant
                "source": "fallback_search",
                "url": search_url,  # URL de recherche au lieu de vidéo directe
                "is_search_url": True,  # Marquer comme URL de recherche
            }

            results.append(result)

        return results

    def _calculate_relevance_score(
        self, result: dict, target_artist: str, target_title: str
    ) -> float:
        """Score de pertinence d'un résultat ytmusicapi (délègue au calcul pur)."""
        result_artists = [
            (a.get("name", "") if isinstance(a, dict) else str(a))
            for a in result.get("artists", [])
        ]
        return relevance_score(result.get("title", ""), result_artists, target_artist, target_title)

    def _get_best_thumbnail(self, thumbnails: list[dict]) -> str | None:
        """Récupère la meilleure thumbnail disponible"""
        if not thumbnails:
            return None

        # Prendre la thumbnail de meilleure qualité
        best_thumb = max(thumbnails, key=lambda x: x.get("width", 0) * x.get("height", 0))
        return best_thumb.get("url")

    def _get_cached_result(self, cache_key: str) -> list[dict] | None:
        """Récupération depuis le cache"""
        try:
            conn = sqlite3.connect(self.cache_db)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT results FROM youtube_search_cache WHERE query_hash = ? AND expires_at > ?",
                (cache_key, datetime.now()),
            )
            result = cursor.fetchone()
            conn.close()

            if result:
                return pickle.loads(result[0])
        except (sqlite3.Error, pickle.PickleError, EOFError, OSError) as e:
            logger.debug(f"Erreur cache: {e}")

        return None

    def _cache_result(self, cache_key: str, results: list[dict]):
        """Mise en cache des résultats"""
        try:
            conn = sqlite3.connect(self.cache_db)
            cursor = conn.cursor()
            expires_at = datetime.now() + timedelta(hours=YOUTUBE_CACHE_TTL_HOURS)
            cursor.execute(
                "INSERT OR REPLACE INTO youtube_search_cache (query_hash, results, cached_at, expires_at) VALUES (?, ?, ?, ?)",
                (cache_key, pickle.dumps(results), datetime.now(), expires_at),
            )
            conn.commit()
            conn.close()
        except (sqlite3.Error, pickle.PickleError, OSError) as e:
            logger.debug(f"Erreur mise en cache: {e}")
