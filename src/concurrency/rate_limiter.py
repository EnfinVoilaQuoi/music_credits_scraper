"""Rate-limiter PAR DOMAINE pour les providers async (REFONTE Phase F1).

Remplace, au fil de la Phase F, les `time.sleep(DELAY_BETWEEN_REQUESTS)`
décentralisés des clients API : chaque domaine a un `asyncio.Semaphore(1)`
(jamais deux requêtes simultanées vers le même site) et un délai minimum entre
la fin d'une requête et le début de la suivante. Deux domaines différents ne se
gênent pas.

Usage (F2+) ::

    limiter = DomainRateLimiter()  # délai = DELAY_BETWEEN_REQUESTS
    async with limiter.limit("api.deezer.com"):
        response = await client.get(url)

Conçu pour UNE boucle asyncio (celle d'`async_loop`) — les primitives asyncio
ne sont pas partageables entre boucles. L'horloge et le sleep sont injectables
pour tester sans attendre (horloge fake).
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass
class _DomainState:
    semaphore: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(1))
    next_allowed: float = 0.0  # horloge monotone : avant, on attend


class DomainRateLimiter:
    """`domaine → Semaphore(1) + délai mini` entre deux requêtes du même domaine."""

    def __init__(
        self,
        min_delay: float | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """`min_delay=None` → `DELAY_BETWEEN_REQUESTS` de la config (défaut app)."""
        if min_delay is None:
            from src.config import DELAY_BETWEEN_REQUESTS

            min_delay = DELAY_BETWEEN_REQUESTS
        self.min_delay = float(min_delay)
        self._clock = clock
        self._sleep = sleep
        self._domains: dict[str, _DomainState] = {}

    def _state(self, domain: str) -> _DomainState:
        return self._domains.setdefault(domain.strip().lower(), _DomainState())

    @asynccontextmanager
    async def limit(self, domain: str):
        """Section critique d'une requête vers `domain` : sérialise + espace.

        Le délai court à partir de la FIN de la requête précédente (sortie du
        `with`), même si elle a échoué — un site fâché se ménage aussi.
        """
        state = self._state(domain)
        async with state.semaphore:
            wait = state.next_allowed - self._clock()
            if wait > 0:
                await self._sleep(wait)
            try:
                yield
            finally:
                state.next_allowed = self._clock() + self.min_delay
