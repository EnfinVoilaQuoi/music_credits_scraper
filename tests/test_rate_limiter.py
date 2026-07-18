"""Tests du DomainRateLimiter (Phase F1) — horloge et sleep fake, zéro attente réelle."""

import asyncio

import pytest

from src.concurrency.rate_limiter import DomainRateLimiter


class FakeClock:
    """Horloge monotone contrôlée : `sleep` avance le temps au lieu d'attendre."""

    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def make_limiter(min_delay: float = 1.0) -> tuple[DomainRateLimiter, FakeClock]:
    clock = FakeClock()
    return DomainRateLimiter(min_delay, clock=clock.now, sleep=clock.sleep), clock


async def _request(limiter, domain, clock=None, duration=0.0):
    async with limiter.limit(domain):
        if duration and clock is not None:
            clock.t += duration  # durée simulée de la requête


def test_first_request_passes_immediately():
    limiter, clock = make_limiter()
    asyncio.run(_request(limiter, "api.deezer.com"))
    assert clock.sleeps == []


def test_back_to_back_requests_wait_min_delay():
    limiter, clock = make_limiter(min_delay=1.0)

    async def scenario():
        await _request(limiter, "api.deezer.com")
        await _request(limiter, "api.deezer.com")

    asyncio.run(scenario())
    assert clock.sleeps == [1.0]


def test_partial_elapse_waits_remainder():
    limiter, clock = make_limiter(min_delay=1.0)

    async def scenario():
        await _request(limiter, "api.deezer.com")
        clock.t += 0.3  # 0.3 s se sont écoulées entre les deux requêtes
        await _request(limiter, "api.deezer.com")

    asyncio.run(scenario())
    assert clock.sleeps == [pytest.approx(0.7)]


def test_enough_elapsed_no_wait():
    limiter, clock = make_limiter(min_delay=1.0)

    async def scenario():
        await _request(limiter, "api.deezer.com")
        clock.t += 5.0
        await _request(limiter, "api.deezer.com")

    asyncio.run(scenario())
    assert clock.sleeps == []


def test_delay_counted_from_request_end():
    """Le délai court depuis la FIN de la requête : sa durée ne le raccourcit pas."""
    limiter, clock = make_limiter(min_delay=1.0)

    async def scenario():
        await _request(limiter, "api.deezer.com", clock, duration=0.4)
        await _request(limiter, "api.deezer.com")

    asyncio.run(scenario())
    assert clock.sleeps == [pytest.approx(1.0)]


def test_failed_request_still_spaces():
    limiter, clock = make_limiter(min_delay=1.0)

    async def scenario():
        try:
            async with limiter.limit("api.deezer.com"):
                raise ValueError("requête échouée")
        except ValueError:
            pass
        await _request(limiter, "api.deezer.com")

    asyncio.run(scenario())
    assert clock.sleeps == [1.0]


def test_domains_are_independent():
    limiter, clock = make_limiter(min_delay=1.0)

    async def scenario():
        await _request(limiter, "api.deezer.com")
        await _request(limiter, "api.genius.com")  # autre domaine : aucun délai

    asyncio.run(scenario())
    assert clock.sleeps == []


def test_domain_normalized_case_and_spaces():
    limiter, clock = make_limiter(min_delay=1.0)

    async def scenario():
        await _request(limiter, "API.Deezer.com ")
        await _request(limiter, "api.deezer.com")  # même domaine une fois normalisé

    asyncio.run(scenario())
    assert clock.sleeps == [1.0]


def test_zero_delay_never_sleeps():
    limiter, clock = make_limiter(min_delay=0.0)

    async def scenario():
        for _ in range(3):
            await _request(limiter, "api.deezer.com")

    asyncio.run(scenario())
    assert clock.sleeps == []


def test_same_domain_serialized():
    """Semaphore(1) : jamais deux requêtes simultanées vers le même domaine."""
    limiter, _ = make_limiter(min_delay=0.0)
    inside = 0
    max_inside = 0

    async def one():
        nonlocal inside, max_inside
        async with limiter.limit("api.deezer.com"):
            inside += 1
            max_inside = max(max_inside, inside)
            await asyncio.sleep(0)  # rend la main au milieu de la « requête »
            inside -= 1

    async def scenario():
        await asyncio.gather(one(), one(), one())

    asyncio.run(scenario())
    assert max_inside == 1


def test_different_domains_run_concurrently():
    """Deux domaines se tiennent simultanément (deadlock croisé sinon)."""
    limiter, _ = make_limiter(min_delay=0.0)

    async def scenario():
        a_in, b_in = asyncio.Event(), asyncio.Event()

        async def a():
            async with limiter.limit("a.com"):
                a_in.set()
                await asyncio.wait_for(b_in.wait(), timeout=2.0)

        async def b():
            async with limiter.limit("b.com"):
                b_in.set()
                await asyncio.wait_for(a_in.wait(), timeout=2.0)

        await asyncio.gather(a(), b())

    asyncio.run(scenario())


def test_default_delay_comes_from_config():
    from src.config import DELAY_BETWEEN_REQUESTS

    assert DomainRateLimiter().min_delay == DELAY_BETWEEN_REQUESTS
