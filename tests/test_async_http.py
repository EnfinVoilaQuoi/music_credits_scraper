"""Tests de l'AsyncHttpSession (Phase F2) — transport mocké, zéro réseau."""

import asyncio

import httpx

from src.api.async_http import AsyncHttpSession
from src.concurrency.rate_limiter import DomainRateLimiter
from src.observability import source_usage as su


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def _session(handler, min_delay: float = 0.0):
    clock = FakeClock()
    limiter = DomainRateLimiter(min_delay, clock=clock.now, sleep=clock.sleep)
    session = AsyncHttpSession(transport=httpx.MockTransport(handler), limiter=limiter)
    return session, clock


def test_get_returns_response_and_reuses_client():
    seen_urls = []

    def handler(request):
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    session, _ = _session(handler)

    async def scenario():
        r1 = await session.get("https://api.deezer.com/search", params={"q": "x"})
        client_after_first = session._client
        r2 = await session.get("https://api.deezer.com/track/1")
        assert session._client is client_after_first  # client partagé, pas recréé
        return r1, r2

    r1, r2 = asyncio.run(scenario())
    assert r1.status_code == 200 and r1.json() == {"ok": True}
    assert r2.status_code == 200
    assert seen_urls == ["https://api.deezer.com/search?q=x", "https://api.deezer.com/track/1"]


def test_rate_limited_per_domain():
    def handler(request):
        return httpx.Response(200, json={})

    session, clock = _session(handler, min_delay=1.0)

    async def scenario():
        await session.get("https://api.deezer.com/a")
        await session.get("https://api.deezer.com/b")  # même domaine → attend
        await session.get("https://api.getsong.co/c")  # autre domaine → direct

    asyncio.run(scenario())
    assert clock.sleeps == [1.0]


def test_headers_applied_to_all_requests():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers.get("User-Agent")
        return httpx.Response(200, json={})

    session = AsyncHttpSession(
        transport=httpx.MockTransport(handler),
        limiter=DomainRateLimiter(0.0),
        headers={"User-Agent": "test-agent/1.0"},
    )
    asyncio.run(session.get("https://api.deezer.com/x"))
    assert seen["ua"] == "test-agent/1.0"


def test_aclose_idempotent_and_reopens():
    def handler(request):
        return httpx.Response(200, json={})

    session, _ = _session(handler)

    async def scenario():
        await session.get("https://api.deezer.com/x")
        await session.aclose()
        await session.aclose()  # idempotent
        response = await session.get("https://api.deezer.com/y")  # client recréé
        assert response.status_code == 200

    asyncio.run(scenario())


def test_http_errors_propagate():
    def handler(request):
        raise httpx.ConnectError("réseau coupé")

    session, _ = _session(handler)

    async def scenario():
        try:
            await session.get("https://api.deezer.com/x")
        except httpx.ConnectError:
            return "raised"
        return "not raised"

    assert asyncio.run(scenario()) == "raised"


# ── Observabilité : le transport nourrit les compteurs, sans rien changer ──────
def _observer(url: str, session):
    """Joue un GET sous observation et rend le verdict unique."""
    su.reset()
    su.set_sink(None)

    async def scenario():
        with su.observe("deezer"):
            try:
                await session.get(url)
            except httpx.HTTPError:
                pass

    asyncio.run(scenario())
    verdicts = su.flush()
    su.reset()
    return verdicts[0]


def test_transport_enregistre_les_codes_http():
    """`get()` ne lève pas sur 4xx/5xx : un capteur qui n'attraperait que les
    exceptions ne verrait jamais ces codes."""
    for code, attendu in ((200, "ok"), (429, "throttled"), (500, "unreachable")):
        session, _ = _session(lambda request, c=code: httpx.Response(c, json={}))
        assert _observer("https://api.deezer.com/x", session).issue == attendu


def test_transport_classe_le_403_anti_bot():
    def handler(request):
        return httpx.Response(403, headers={"cf-ray": "8a1b"}, text="Just a moment...")

    session, _ = _session(handler)
    assert _observer("https://genius.com/x", session).issue == "blocked"


def test_transport_classe_les_erreurs_reseau():
    def handler(request):
        raise httpx.ConnectError("réseau coupé")

    session, _ = _session(handler)
    assert _observer("https://api.deezer.com/x", session).issue == "unreachable"


def test_le_capteur_ne_change_pas_le_comportement_reseau():
    """Invariant : `get()` continue de RENDRE les 4xx/5xx au lieu de lever."""

    def handler(request):
        return httpx.Response(404, json={})

    session, _ = _session(handler)
    response = asyncio.run(session.get("https://api.deezer.com/x"))
    assert response.status_code == 404  # aucune exception levée


def test_domaine_inconnu_ne_casse_rien():
    def handler(request):
        return httpx.Response(200, json={})

    session, _ = _session(handler)
    assert asyncio.run(session.get("https://site-inconnu.test/x")).status_code == 200
