"""Session HTTP async PARTAGÉE des providers d'enrichissement (Phase F2).

UN `httpx.AsyncClient` pour tous les appels API du flux async + le
`DomainRateLimiter` de F1 : jamais deux requêtes simultanées vers le même
domaine, délai mini `DELAY_BETWEEN_REQUESTS` entre deux requêtes d'un même
domaine — remplace les limiteurs ad hoc des clients sync (fenêtre Deezer,
sleeps GetSongBPM), en plus prudent. Deux domaines différents ne se gênent pas.

Le client est créé LAZY au premier `get()` (donc dans la boucle asyncio) et
fermé par `aclose()` — appelé par le flux propriétaire en fin de batch
(« qui crée ferme ») ; rouvert à la demande au batch suivant.
"""

import time
from urllib.parse import urlsplit

import httpx

from src.concurrency.rate_limiter import DomainRateLimiter
from src.observability import source_usage
from src.observability.issues import classify
from src.observability.registry import key_for_domain


class AsyncHttpSession:
    """`httpx.AsyncClient` partagé + rate-limit par domaine, injectable en test."""

    def __init__(
        self,
        min_delay: float | None = None,
        *,
        headers: dict | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        limiter: DomainRateLimiter | None = None,
    ) -> None:
        self._limiter = limiter if limiter is not None else DomainRateLimiter(min_delay)
        self._headers = headers
        self._transport = transport  # tests : httpx.MockTransport
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            # follow_redirects : requests suit les redirections par défaut,
            # httpx non — aligné sur le comportement des clients sync.
            self._client = httpx.AsyncClient(
                headers=self._headers, transport=self._transport, follow_redirects=True
            )
        return self._client

    async def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        timeout: float = 15.0,
    ) -> httpx.Response:
        """GET rate-limité par domaine. Lève les erreurs httpx (frontière appelant).

        Enregistre au passage une TENTATIVE pour l'observabilité des sources.
        Le comportement réseau est inchangé : on ne lève toujours pas sur
        4xx/5xx, les appelants gardent leur `raise_for_status()` — d'où la
        lecture explicite de `status_code`, un `except httpx.HTTPError` ne
        verrait rien de ces codes.
        """
        client = self._ensure_client()
        netloc = urlsplit(url).netloc
        start = time.monotonic()
        async with self._limiter.limit(netloc):
            try:
                response = await client.get(url, params=params, headers=headers, timeout=timeout)
            except BaseException as exc:
                source_usage.record_attempt(
                    key_for_domain(netloc),
                    classify(exc=exc),
                    detail=f"{type(exc).__name__}: {exc}",
                    latency_ms=int((time.monotonic() - start) * 1000),
                )
                raise
        source_usage.record_response(
            url,
            response.status_code,
            headers=response.headers,
            latency_ms=int((time.monotonic() - start) * 1000),
        )
        return response

    async def aclose(self) -> None:
        """Ferme le client (idempotent) ; recréé au prochain `get()`."""
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()
