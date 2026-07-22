"""Provider Spotify ID — récupère le Track ID Spotify (destiné à ReccoBeats).

Corps historique de `DataEnricher.get_unique_spotify_id` + le bloc « scraper
Spotify ID » d'`enrich_track`, déplacé sans changement de logique. Spotify n'est
utilisé QUE pour l'ID (jamais les audio-features). L'unicité d'ID passe par le
contexte (logique partagée avec ReccoBeats/SongBPM).
"""

from playwright.async_api import Error as PlaywrightError

from src.enrichment.base import Capability, LazyResource
from src.enrichment.context import EnrichmentContext
from src.models import Track
from src.utils.logger import get_logger

logger = get_logger(__name__)

# playwright sync et async partagent les mêmes classes d'erreur (Error, TimeoutError) ;
# TimeoutError ⊂ Error → un seul except couvre les deux voies. (patchright = classes DISTINCTES.)


class SpotifyIdProvider:
    """Récupération du Spotify Track ID via scraper (source `spotify_id`)."""

    name = "spotify_id"
    capabilities = {Capability.BPM}  # E7b structurel (non consommé)
    error_result = False

    def __init__(self, scraper=None, scraper_factory=None, async_scraper_factory=None):
        # PROPRIÉTAIRE du scraper Spotify (créé lazy, fermé par close()).
        self._resource = LazyResource(scraper, scraper_factory, label="scraper Spotify ID")
        # Variante ASYNC (F3b) : vit dans la boucle, fermée par aclose().
        self._async_resource = LazyResource(
            None, async_scraper_factory, label="scraper Spotify ID async"
        )

    @property
    def scraper(self):
        """Point d'EMPRUNT du scraper partagé (créé à la demande) : ReccoBeats
        l'utilise pour son fallback, sans jamais le posséder ni le fermer."""
        return self._resource.get()

    @property
    def async_scraper(self):
        """Point d'EMPRUNT de la variante async (F3b) — même règle : ReccoBeats
        l'utilise pour son fallback, jamais possédée ni fermée par l'emprunteur."""
        return self._async_resource.get()

    def is_available(self) -> bool:
        return self._resource.available()

    def close(self) -> None:
        """Ferme le scraper si ce provider l'a créé (recréé au run suivant)."""
        self._resource.close()

    async def aclose(self) -> None:
        """Ferme le scraper ASYNC s'il l'a créé (browsers de la boucle, F3b)."""
        await self._async_resource.aclose()

    def gate(self, track: Track, ctx: EnrichmentContext) -> str | None:
        """Skip si un ID valide existe déjà (hors force_update) ou si la voie
        ISRC a fourni les données audio (le scrape Spotify devient inutile)."""
        validate = ctx.validate_spotify_id_unique
        has_valid_id = track.spotify_id and (
            not ctx.artist_tracks
            or validate is None
            or validate(track.spotify_id, track, ctx.artist_tracks)
        )
        if (ctx.force_update or not has_valid_id) and not ctx.isrc_satisfied:
            logger.info(
                f"🎯 Appel du scraper Spotify ID pour '{track.title}' "
                f"(force_update={ctx.force_update}, has_valid_id={has_valid_id})"
            )
            return None
        # Deux motifs de skip distincts : soit l'ISRC a déjà satisfait ReccoBeats
        # en pré-étape (spotify_id souvent None sur cette voie), soit un ID valide
        # existe déjà. Ne pas afficher « ID déjà présent » quand c'est l'ISRC.
        if ctx.isrc_satisfied:
            logger.info(
                f"⏭️ Scraper Spotify ID non nécessaire (ISRC déjà satisfait) pour '{track.title}'"
            )
        else:
            logger.info(
                f"⏭️ Scraper Spotify ID non nécessaire (ID déjà présent et valide: {track.spotify_id})"
            )
        return "not_needed"

    def get_unique_spotify_id(
        self, track: Track, ctx: EnrichmentContext, force_scraper: bool = False
    ) -> str | None:
        """Récupère un Spotify ID unique (scrape + validation d'unicité)."""
        scraper = self._resource.get()
        if not scraper:
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
        spotify_id = scraper.get_spotify_id(artist_name, track.title)

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
            page_title = self._resource.get().get_spotify_page_title(spotify_id)
            if page_title:
                track.spotify_page_title = page_title
                logger.info(f"📄 Titre de page Spotify: {page_title[:50]}...")
        except PlaywrightError as e:
            logger.debug(f"Impossible de récupérer le titre de page: {e}")

        return True

    async def enrich_async(self, track: Track, ctx: EnrichmentContext) -> bool:
        """Voie async (F3b) : scraper Playwright ASYNC natif dans la boucle.

        Sans variante async configurée (tests, compat), repli sur le pont sync
        F2 (corps sync sur le thread dédié du run).
        """
        scraper = self._async_resource.get()
        if scraper is None:
            return await ctx.sync_runner.run(self.enrich, track, ctx)

        spotify_id = await self._get_unique_spotify_id_async(track, ctx, scraper)
        if not spotify_id:
            logger.warning("❌ Échec récupération Spotify ID via scraper")
            return False

        track.spotify_id = spotify_id
        logger.info(f"✅ Spotify ID attribué via scraper: {spotify_id}")

        # Récupérer le titre de la page Spotify pour vérification
        try:
            page_title = await scraper.get_spotify_page_title_async(spotify_id)
            if page_title:
                track.spotify_page_title = page_title
                logger.info(f"📄 Titre de page Spotify: {page_title[:50]}...")
        except PlaywrightError as e:
            logger.debug(f"Impossible de récupérer le titre de page: {e}")

        return True

    async def _get_unique_spotify_id_async(
        self, track: Track, ctx: EnrichmentContext, scraper
    ) -> str | None:
        """Miroir async de `get_unique_spotify_id(force_scraper=True)` : scrape
        + validation d'unicité (l'existant a déjà été jugé par le gate)."""
        artist_name = track.artist.name if hasattr(track.artist, "name") else str(track.artist)
        validate = ctx.validate_spotify_id_unique

        logger.info(f"🔍 Recherche Spotify ID via scraper pour: '{artist_name}' - '{track.title}'")
        spotify_id = await scraper.get_spotify_id_async(artist_name, track.title)

        if not spotify_id:
            logger.warning(f"❌ Aucun Spotify ID trouvé via scraper pour '{track.title}'")
            return None

        if validate and not validate(spotify_id, track, ctx.artist_tracks):
            logger.error(f"❌ ERREUR: Spotify ID trouvé par scraper est déjà utilisé: {spotify_id}")
            logger.error("   Cela ne devrait pas arriver. Vérifiez la base de données.")
            return None

        logger.info(f"✅ Spotify ID unique trouvé via scraper: {spotify_id}")
        return spotify_id
