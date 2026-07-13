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
    # Valeur posée dans le dict de résultats si enrich() lève (l'orchestrateur
    # capture à la frontière batch). False = « pas de données » ; None =
    # crash/timeout, EXCLU du « tout a échoué » qui déclenche le nettoyage.
    error_result: bool | None

    def is_available(self) -> bool:
        """True si la source est utilisable (client/API/scraper initialisé)."""
        ...

    def gate(self, track: "Track", ctx: "EnrichmentContext") -> object | None:
        """Décide si enrich() doit tourner pour ce morceau (+ log de la raison).

        None = exécuter enrich(). Toute autre valeur = skip, posée telle quelle
        dans le dict de résultats ("not_needed", True pour ISRC déjà satisfait…).
        """
        ...

    def enrich(self, track: "Track", ctx: "EnrichmentContext") -> bool:
        """Enrichit `track`. Renvoie True si au moins une donnée a été posée."""
        ...

    def close(self) -> None:
        """Libère les ressources (navigateur, session…). No-op si rien à fermer."""
        ...
