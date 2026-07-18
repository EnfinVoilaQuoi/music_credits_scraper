"""Contrat commun des providers d'enrichissement.

Un provider encapsule UNE source : il sait dire s'il est disponible, décider
s'il doit tourner (gate), enrichir un morceau dans un contexte donné (renvoie
True si des données ont changé) et se fermer. L'orchestrateur ne connaît que ce
protocole + l'ordre d'appel.
"""

from collections.abc import Callable
from enum import Enum, auto
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.enrichment.context import EnrichmentContext
    from src.models import Track

logger = get_logger(__name__)


class Capability(Enum):
    """Nature des données qu'une source sait produire (jeu de l'AUDIT §8).

    Additif/structurel (phase E7b) : déclaré par chaque provider mais PAS encore
    consommé par l'orchestrateur — l'aiguillage par capacité (étage 2 : lyrics,
    streams, certifs) est différé à F5, quand le passage async restructurera les
    workers concernés (règle « jamais structure + comportement ensemble »).
    """

    BPM = auto()
    LYRICS = auto()
    STREAMS = auto()
    CERTS = auto()
    CREDITS = auto()


class LazyResource:
    """Ressource d'un provider : injectée (tests) ou créée par factory au 1er usage.

    Règle d'ownership « qui crée ferme » (Refacto Phase 3.5) : close() ne ferme
    la ressource QUE si elle a été créée par la factory, et la remet à None —
    elle sera recréée à la demande au run suivant, dans le thread qui l'utilise
    (les browsers Playwright naissent et meurent dans le thread du batch). Une
    ressource injectée ou empruntée n'est jamais fermée ici. Un échec de
    création marque la ressource cassée : plus de tentative pour la session.
    """

    def __init__(self, resource=None, factory: Callable | None = None, label: str = "ressource"):
        self._resource = resource
        self._factory = factory
        self._label = label
        self._owned = False
        self._broken = False

    def available(self) -> bool:
        """True si la ressource existe ou peut être créée."""
        return not self._broken and (self._resource is not None or self._factory is not None)

    def get(self):
        """Ressource injectée, ou créée par la factory au 1er appel (lazy)."""
        if self._resource is None and self._factory is not None and not self._broken:
            try:
                self._resource = self._factory()
                self._owned = True
            except Exception as e:
                logger.warning(f"⚠️ {self._label} indisponible (création échouée): {e}")
                self._broken = True
        return self._resource

    def close(self) -> None:
        """Ferme la ressource si créée par la factory (idempotent, ré-ouvrable)."""
        if not (self._owned and self._resource is not None):
            return
        try:
            self._resource.close()
            logger.info(f"{self._label} fermé")
        except Exception as e:
            logger.warning(f"⚠️ Fermeture {self._label}: {e}")
        self._resource = None
        self._owned = False

    async def aclose(self) -> None:
        """Variante async de close() (Phase F3) : awaite `resource.aclose()`.

        Même ownership « qui crée ferme » — pour les ressources vivant dans la
        boucle asyncio (scrapers Playwright async). À appeler DANS la boucle.
        """
        if not (self._owned and self._resource is not None):
            return
        try:
            await self._resource.aclose()
            logger.info(f"{self._label} fermé")
        except Exception as e:
            logger.warning(f"⚠️ Fermeture {self._label}: {e}")
        self._resource = None
        self._owned = False


@runtime_checkable
class EnrichmentProvider(Protocol):
    """Interface minimale d'une source d'enrichissement."""

    name: str
    # Données que la source sait produire (E7b, structurel — non consommé par
    # l'orchestrateur pour l'instant, cf. Capability).
    capabilities: set[Capability]
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
