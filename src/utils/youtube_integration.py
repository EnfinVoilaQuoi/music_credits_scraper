"""Intégration YouTube simplifiée pour main_window"""

import webbrowser
from urllib.parse import quote

from src.config import YOUTUBE_AUTO_SELECT_ALBUM_TRACKS
from src.models import TrackVideo
from src.utils.logger import get_logger
from src.utils.youtube_utils import artiste_de_recherche, extract_video_id
from src.youtube.track_classifier import TrackClassifier, TrackType
from src.youtube.youtube_searcher import YouTubeSearcher

logger = get_logger(__name__)


# ── Fixer / retirer le lien d'un morceau ─────────────────────────────────────
# Extraites de `manual_entry.manual_youtube_link`, qui en était le seul appelant :
# la fiche morceau doit pouvoir VALIDER ou REJETER le lien proposé
# automatiquement, et ce sont exactement les mêmes gestes.


def lien_youtube_valide(url: str | None) -> str | None:
    """Video id d'une URL de VIDÉO YouTube, ou None si ce n'en est pas une.

    Un lien de RECHERCHE (`/results?search_query=…`) est un faux ami : il
    s'ouvre dans le navigateur mais ne désigne aucune vidéo — l'enregistrer
    poserait en base un lien dont aucune vue ne pourra jamais être comptée.
    """
    if not url:
        return None
    if "youtube.com/watch" not in url and "youtu.be/" not in url:
        return None
    return extract_video_id(url)


def set_youtube_link(data_manager, track, url: str, *, source: str = "manual") -> bool:
    """Fixe le lien YouTube d'un morceau et enregistre la vidéo correspondante.

    `source='manual'` est prioritaire : ni la recherche ni Genius ne l'écrasent
    (cf. `source_lien_retenue`). Écrit dans les DEUX magasins — la colonne, qui
    porte la vidéo principale, et `track_videos`, dont dépend la somme des vues.
    N'écrire que la première laisserait le total ignorer la vidéo validée.

    Returns:
        False si l'URL ne désigne pas une vidéo (rien n'est écrit).
    """
    video_id = lien_youtube_valide(url)
    if not video_id:
        return False

    data_manager.update_track_youtube_url(track.id, url, source)
    data_manager.record_track_videos(
        track.id, [TrackVideo(video_id=video_id, url=url, source=source)]
    )
    track.youtube_url = url
    track.youtube_url_source = source
    if not any(v.video_id == video_id for v in track.videos):
        track.videos.append(TrackVideo(video_id=video_id, url=url, source=source))
    logger.info(f"🔗 Lien YouTube {source} : '{track.title}' → {url}")
    return True


def clear_youtube_link(data_manager, track) -> None:
    """Retire le lien YouTube d'un morceau (retour à la recherche live).

    La vidéo est AUSSI oubliée de `track_videos` : depuis que `ytm_streams`
    somme toutes les vidéos connues, la laisser en base ferait continuer de
    compter les vues d'un lien que l'utilisateur vient de juger faux.
    """
    video_id = extract_video_id(track.youtube_url)
    data_manager.clear_track_youtube_link(track.id)
    if video_id:
        data_manager.forget_track_video(track.id, video_id)
        track.videos = [v for v in track.videos if v.video_id != video_id]
    track.youtube_url = None
    track.youtube_url_source = None
    logger.info(f"🔗 Lien YouTube retiré : '{track.title}'")


def reject_youtube_link(data_manager, track, url: str, artiste_courant: str, searcher=None) -> None:
    """Rejette un lien proposé automatiquement.

    Trois gestes, et il les faut tous les trois : oublier la vidéo (sinon ses
    vues restent sommées), effacer la colonne si c'est bien ce lien qu'elle
    porte, et PURGER la recherche mise en cache — sans quoi la fiche
    reproposerait le même mauvais résultat jusqu'à expiration du cache, et le
    rejet donnerait l'impression de n'avoir servi à rien.
    """
    video_id = extract_video_id(url)
    if video_id:
        data_manager.forget_track_video(track.id, video_id)
        track.videos = [v for v in track.videos if v.video_id != video_id]
    if video_id and extract_video_id(track.youtube_url) == video_id:
        data_manager.clear_track_youtube_link(track.id)
        track.youtube_url = None
        track.youtube_url_source = None

    searcher = searcher if searcher is not None else youtube_integration.searcher
    searcher.forget(artiste_de_recherche(track, artiste_courant), track.title)
    logger.info(f"🚫 Lien YouTube rejeté : '{track.title}' → {url}")


class YouTubeIntegration:
    """Interface simplifiée pour l'intégration YouTube dans l'interface"""

    def __init__(self):
        self._searcher = None
        self.classifier = TrackClassifier()

    @property
    def searcher(self) -> YouTubeSearcher:
        """Chercheur créé au PREMIER usage (idiome des clients paresseux).

        Ce module expose un singleton construit à l'import ; construire le
        chercheur dans `__init__` ouvrait donc `data/youtube_cache.db` et un
        client `YTMusic` — un aller-retour réseau — du seul fait d'importer le
        module. Un test qui importe ces fonctions n'a rien à demander à YouTube.
        """
        if self._searcher is None:
            self._searcher = YouTubeSearcher()
        return self._searcher

    def get_youtube_link_for_track(
        self,
        artist: str,
        title: str,
        album: str = None,
        release_year: int = None,
        known_url: str = None,
        known_source: str = None,
    ) -> dict[str, str]:
        """
        Retourne le lien YouTube approprié pour un morceau.

        known_url : lien déjà en base (Genius media ou recherche persistée) —
        s'il est fourni, AUCUNE recherche n'est lancée : la recherche live ne
        sert plus que de fallback pour les rares cas sans lien au catalogue Genius.

        Returns:
            Dict avec 'url', 'type' ('direct' ou 'search'), 'confidence', 'method', 'source'
        """
        if known_url:
            source = known_source or "genius_media"
            return {
                "url": known_url,
                "type": "direct",
                "confidence": 1.0,
                "method": "stored",
                "source": source,
                "title": title,
                "channel": (
                    "Genius (media)" if source == "genius_media" else "Recherche (persistée)"
                ),
            }

        try:
            # Étape 1: Classification du morceau
            track_type = self.classifier.classify_track(
                title, album=album, release_year=release_year
            )

            # Étape 2: Décider de la stratégie
            should_auto = YOUTUBE_AUTO_SELECT_ALBUM_TRACKS and self.classifier.should_auto_select(
                track_type
            )

            if should_auto:
                # Tentative de sélection automatique
                auto_result = self._try_auto_selection(artist, title, track_type)
                if auto_result:
                    return auto_result

            # Fallback: URL de recherche
            return self._generate_search_url(artist, title, track_type)

        except (AttributeError, TypeError, KeyError) as e:
            logger.error(f"Erreur YouTube pour {artist} - {title}: {e}")
            return self._generate_fallback_search_url(artist, title)

    def _try_auto_selection(self, artist: str, title: str, track_type: TrackType) -> dict | None:
        """Tentative de sélection automatique"""

        try:
            # Rechercher les candidats
            results = self.searcher.search_track(artist, title, max_results=10)

            if not results:
                logger.debug(f"Aucun résultat pour {artist} - {title}")
                return None

            best_result = results[0]
            confidence = best_result.get("relevance_score", 0)
            threshold = self.classifier.get_confidence_threshold(track_type)

            # Vérifier le seuil de confiance
            if confidence >= threshold and not best_result.get("is_search_url", False):
                logger.info(f"Auto-sélection: {best_result['url']} (confiance: {confidence:.2f})")

                return {
                    "url": best_result["url"],
                    "type": "direct",
                    "confidence": confidence,
                    "method": "auto_selected",
                    "source": "search_auto",
                    "track_type": track_type.value,
                    "title": best_result.get("title", title),
                    "channel": best_result.get("channel_title", "Inconnu"),
                }
            else:
                logger.debug(f"Confiance insuffisante: {confidence:.2f} < {threshold}")
                return None

        except (AttributeError, TypeError, KeyError, IndexError) as e:
            logger.debug(f"Erreur auto-sélection: {e}")
            return None

    def _generate_search_url(self, artist: str, title: str, track_type: TrackType) -> dict:
        """Génère une URL de recherche optimisée selon le type"""

        strategy = self.classifier.get_search_strategy(track_type)

        # Construire la requête selon la stratégie
        primary_query = strategy["primary_query"].format(artist=artist, title=title)
        search_url = f"https://www.youtube.com/results?search_query={quote(primary_query)}"

        return {
            "url": search_url,
            "type": "search",
            "confidence": 0.0,
            "method": "optimized_search",
            "track_type": track_type.value,
            "query": primary_query,
        }

    def _generate_fallback_search_url(self, artist: str, title: str) -> dict:
        """Génère une URL de recherche basique en cas d'erreur"""

        search_term = f"{artist} {title} audio"
        search_url = f"https://www.youtube.com/results?search_query={quote(search_term)}"

        return {
            "url": search_url,
            "type": "search",
            "confidence": 0.0,
            "method": "fallback_search",
            "track_type": "unknown",
        }

    def open_youtube_link(self, youtube_result: dict) -> bool:
        """Ouvre le lien YouTube dans le navigateur"""

        try:
            url = youtube_result.get("url")
            if url:
                webbrowser.open(url)

                # Log selon le type
                if youtube_result.get("type") == "direct":
                    logger.info(f"Ouverture directe: {url}")
                else:
                    logger.info(f"Ouverture recherche: {youtube_result.get('query', 'N/A')}")

                return True
        except (AttributeError, TypeError, KeyError, OSError) as e:
            logger.error(f"Erreur ouverture YouTube: {e}")

        return False


# Instance globale pour faciliter l'import
youtube_integration = YouTubeIntegration()
