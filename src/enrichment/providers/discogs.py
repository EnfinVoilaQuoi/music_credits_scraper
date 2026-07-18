"""Provider Discogs — crédits complémentaires via l'API Discogs.

Enveloppe `DiscogsClient.enrich_track_data`, appelée telle quelle par
l'orchestrateur historique. Volontairement fin : la gestion d'erreur reste au
niveau de l'orchestrateur (comportement inchangé) jusqu'à la centralisation
`_run_safely` (phase C4).
"""

from src.enrichment.base import Capability, LazyResource
from src.enrichment.context import EnrichmentContext
from src.models import Track
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DiscogsProvider:
    """Enrichissement via l'API Discogs (source `discogs`)."""

    name = "discogs"
    capabilities = {Capability.BPM}  # E7b structurel (non consommé)
    error_result = False

    def __init__(self, client=None, client_factory=None):
        # Client créé lazy (le lookup du token DISCOGS_* vit dans la factory).
        self._resource = LazyResource(client, client_factory, label="client Discogs")

    def is_available(self) -> bool:
        return self._resource.available()

    def close(self) -> None:
        """Le client Discogs (HTTP) n'a aucune ressource à libérer."""

    def gate(self, track: Track, ctx: EnrichmentContext) -> None:
        """Jamais de skip : crédits complémentaires (appelé après le vote BPM)."""
        logger.info(f"💿 Appel de Discogs API pour '{track.title}'")
        return None

    def enrich(self, track: Track, ctx: EnrichmentContext) -> bool | str:
        # enrich_track_data peut renvoyer "not_needed" (release matchée mais
        # rien de nouveau) : transmis tel quel à l'orchestrateur, qui le traite
        # comme un skip (pas d'échec, exclu du calcul `all_failed`).
        client = self._resource.get()
        if client is None:
            return False
        return client.enrich_track_data(track, force_update=ctx.force_update)

    async def enrich_async(self, track: Track, ctx: EnrichmentContext) -> bool | str:
        """Voie async (F2) : client discogs-client sync inchangé, exécuté sur le
        thread sync dédié du run."""
        return await ctx.sync_runner.run(self.enrich, track, ctx)
