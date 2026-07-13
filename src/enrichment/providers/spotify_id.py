"""Provider Spotify ID — récupère le Track ID Spotify (destiné à ReccoBeats).

Corps historique de `DataEnricher.get_unique_spotify_id` + le bloc « scraper
Spotify ID » d'`enrich_track`, déplacé sans changement de logique. Spotify n'est
utilisé QUE pour l'ID (jamais les audio-features). L'unicité d'ID passe par le
contexte (logique partagée avec ReccoBeats/SongBPM).
"""

from src.enrichment.context import EnrichmentContext
from src.models import Track
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SpotifyIdProvider:
    """Récupération du Spotify Track ID via scraper (source `spotify_id`)."""

    name = "spotify_id"

    def __init__(self, scraper=None):
        self._scraper = scraper

    def is_available(self) -> bool:
        return self._scraper is not None

    def close(self) -> None:
        """No-op en C3 : le scraper reste fermé par DataEnricher.close (→ C5)."""

    def get_unique_spotify_id(
        self, track: Track, ctx: EnrichmentContext, force_scraper: bool = False
    ) -> str | None:
        """Récupère un Spotify ID unique (scrape + validation d'unicité)."""
        if not self._scraper:
            logger.warning("❌ Spotify ID scraper non disponible")
            return None

        artist_name = track.artist.name if hasattr(track.artist, "name") else str(track.artist)
        validate = ctx.validate_spotify_id_unique

        # Si le track a déjà un Spotify ID et qu'on ne force pas, le vérifier
        if not force_scraper and track.spotify_id:
            if validate and validate(track.spotify_id, track, ctx.artist_tracks):
                logger.info(f"✅ Spotify ID existant validé: {track.spotify_id}")
                return track.spotify_id
            else:
                logger.warning(
                    "⚠️ Spotify ID existant invalide (dupliqué), recherche d'un nouveau..."
                )

        # Utiliser le scraper Spotify_ID pour obtenir le bon ID
        logger.info(f"🔍 Recherche Spotify ID via scraper pour: '{artist_name}' - '{track.title}'")
        spotify_id = self._scraper.get_spotify_id(artist_name, track.title)

        if not spotify_id:
            logger.warning(f"❌ Aucun Spotify ID trouvé via scraper pour '{track.title}'")
            return None

        # Valider l'unicité de l'ID trouvé
        if validate and not validate(spotify_id, track, ctx.artist_tracks):
            logger.error(f"❌ ERREUR: Spotify ID trouvé par scraper est déjà utilisé: {spotify_id}")
            logger.error("   Cela ne devrait pas arriver. Vérifiez la base de données.")
            return None

        logger.info(f"✅ Spotify ID unique trouvé via scraper: {spotify_id}")
        return spotify_id

    def enrich(self, track: Track, ctx: EnrichmentContext) -> bool:
        """Scrape un Spotify ID unique et le pose (+ titre de page pour vérif)."""
        spotify_id = self.get_unique_spotify_id(track, ctx, force_scraper=True)
        if not spotify_id:
            logger.warning("❌ Échec récupération Spotify ID via scraper")
            return False

        track.spotify_id = spotify_id
        logger.info(f"✅ Spotify ID attribué via scraper: {spotify_id}")

        # Récupérer le titre de la page Spotify pour vérification
        try:
            page_title = self._scraper.get_spotify_page_title(spotify_id)
            if page_title:
                track.spotify_page_title = page_title
                logger.info(f"📄 Titre de page Spotify: {page_title[:50]}...")
        except Exception as e:
            logger.debug(f"Impossible de récupérer le titre de page: {e}")

        return True
