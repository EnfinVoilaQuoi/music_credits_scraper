"""Provider Discogs — crédits complémentaires via l'API Discogs.

Enveloppe `DiscogsClient.enrich_track_data`, appelée telle quelle par
l'orchestrateur historique. Volontairement fin : la gestion d'erreur reste au
niveau de l'orchestrateur (comportement inchangé) jusqu'à la centralisation
`_run_safely` (phase C4).
"""

from src.enrichment.context import EnrichmentContext
from src.models import Track
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DiscogsProvider:
    """Enrichissement via l'API Discogs (source `discogs`)."""

    name = "discogs"
    error_result = False

    def __init__(self, client=None):
        self._client = client

    def is_available(self) -> bool:
        return self._client is not None

    def close(self) -> None:
        """Le client Discogs (HTTP) n'a aucune ressource à libérer."""

    def gate(self, track: Track, ctx: EnrichmentContext) -> None:
        """Jamais de skip : crédits complémentaires (appelé après le vote BPM)."""
        logger.info(f"💿 Appel de Discogs API pour '{track.title}'")
        return None

    def enrich(self, track: Track, ctx: EnrichmentContext) -> bool:
        return self._client.enrich_track_data(track, force_update=ctx.force_update)
