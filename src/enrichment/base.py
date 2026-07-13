"""Contrat commun des providers d'enrichissement.

Un provider encapsule UNE source : il sait dire s'il est disponible, enrichir un
morceau dans un contexte donné (renvoie True si des données ont changé) et se
fermer. L'orchestrateur ne connaît que ce protocole + l'ordre d'appel.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from src.enrichment.context import EnrichmentContext
    from src.models import Track


@runtime_checkable
class EnrichmentProvider(Protocol):
    """Interface minimale d'une source d'enrichissement."""

    name: str

    def is_available(self) -> bool:
        """True si la source est utilisable (client/API/scraper initialisé)."""
        ...

    def enrich(self, track: "Track", ctx: "EnrichmentContext") -> bool:
        """Enrichit `track`. Renvoie True si au moins une donnée a été posée."""
        ...

    def close(self) -> None:
        """Libère les ressources (navigateur, session…). No-op si rien à fermer."""
        ...
