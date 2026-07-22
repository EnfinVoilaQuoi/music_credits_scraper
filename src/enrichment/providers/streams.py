"""Provider streams (Spotify via Kworb / YouTube Music) — capability STREAMS.

Packagé comme provider (E7b `Capability.STREAMS`) mais **PAS consommé par
l'orchestrateur audio** (`enrich_track`) : utilisé par le worker GUI « Nb
Streams » (`src/gui/workers/streams.py`) — MÊME déclencheur, comportement
inchangé. Il POSSÈDE ses clients (Kworb + YTM, créés lazy) et délègue aux
updaters autonomes (`update_kworb_streams` / `update_ytmusic_streams` /
`update_video_views`, gate d'identité YTM E8 inclus). Le worker garde la boucle,
les résumés GUI (🚨 aborted / ⚠️ warning), la confirmation Kworb floue et les
saves.

Interface volontairement propre au domaine streams (pas l'`EnrichmentContext`
audio), sur le modèle du `LyricsProvider` : le jour où l'orchestrateur consommera
la capability STREAMS, un adaptateur fera le pont — sans réécrire ce cœur.
"""

from src.enrichment.base import Capability
from src.utils.logger import get_logger

logger = get_logger(__name__)


class StreamsProvider:
    """Récupère les streams Spotify (Kworb) et YouTube Music d'un artiste."""

    name = "streams"
    capabilities = {Capability.STREAMS}

    def __init__(self, *, kworb=None, ytm=None):
        # Clients : injectés (tests) ou créés LAZY au 1er usage. Le client YTM est
        # PARTAGÉ entre les streams YTM et les vues vidéo (un seul `YTMusic()`
        # sur le batch, au lieu de deux instanciations comme avant le packaging).
        self._kworb = kworb
        self._ytm = ytm

    # ── Clients (lazy) ───────────────────────────────────────────────────────

    def _kworb_client(self):
        if self._kworb is None:
            from src.scrapers.kworb_scraper import KworbScraper

            self._kworb = KworbScraper()
        return self._kworb

    def _ytm_client(self):
        if self._ytm is None:
            from src.api.ytmusic_api import YTMusicAPI

            self._ytm = YTMusicAPI()
        return self._ytm

    # ── Récupérations (comportement identique aux updaters historiques) ──────

    def fetch_spotify(self, artist, data_manager) -> dict:
        """Streams Spotify via Kworb (matching + backfill ID + éditions agrégées)."""
        from src.utils.update_kworb import update_kworb_streams

        return update_kworb_streams(artist, data_manager, scraper=self._kworb_client())

    def fetch_ytm(self, artist, data_manager) -> dict:
        """Streams YouTube Music (gate d'identité de canal E8 inclus)."""
        from src.utils.update_ytmusic import update_ytmusic_streams

        return update_ytmusic_streams(artist, data_manager, api=self._ytm_client())

    def fetch_video_views(self, artist, tracks, data_manager) -> dict:
        """Vues + nature (clip/show/audio) de LA vidéo — batch YT (client partagé)."""
        from src.utils.update_video_views import update_video_views

        return update_video_views(artist, tracks, data_manager, api=self._ytm_client())

    def close(self) -> None:
        """Ferme les clients qui l'exposent (défensif — Kworb/YTM n'ont pas de
        ressource persistante ; no-op sinon)."""
        for client in (self._kworb, self._ytm):
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as e:
                    logger.debug(f"Fermeture client streams: {e}")
