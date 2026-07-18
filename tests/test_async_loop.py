"""Tests de la boucle asyncio unique (Phase F1).

Pilotée depuis du code sync (pas de pytest-asyncio) : c'est exactement la
position des appelants réels (GUI / workers) — submit depuis un autre thread,
résultat via `concurrent.futures.Future`.
"""

import asyncio
import threading
import time
from concurrent.futures import CancelledError

import pytest

from src.concurrency.async_loop import AsyncLoopThread
from src.gui.workers import lifecycle


@pytest.fixture()
def loop_thread():
    lt = AsyncLoopThread(name="test-loop")
    lt.start()
    yield lt
    lt.shutdown(timeout=2.0)


def test_submit_returns_result(loop_thread):
    async def coro():
        return 42

    assert loop_thread.submit(coro()).result(timeout=2.0) == 42


def test_submit_runs_in_dedicated_thread(loop_thread):
    async def coro():
        return threading.current_thread().name

    assert loop_thread.submit(coro()).result(timeout=2.0) == "test-loop"


def test_exception_propagates_via_future(loop_thread):
    async def boom():
        raise ValueError("boum")

    with pytest.raises(ValueError, match="boum"):
        loop_thread.submit(boom()).result(timeout=2.0)


def test_submit_before_start_raises():
    lt = AsyncLoopThread()

    async def coro():
        return 1

    with pytest.raises(RuntimeError, match="start"):
        lt.submit(coro())


def test_start_idempotent(loop_thread):
    loop_thread.start()  # 2e appel = no-op, la même boucle continue de tourner
    assert loop_thread.is_running()

    async def coro():
        return "ok"

    assert loop_thread.submit(coro()).result(timeout=2.0) == "ok"


def test_shutdown_cancels_pending_task(loop_thread):
    started = threading.Event()

    async def sleeper():
        started.set()
        await asyncio.sleep(60)

    future = loop_thread.submit(sleeper())
    assert started.wait(2.0)
    assert loop_thread.shutdown(timeout=2.0) is True
    assert not loop_thread.is_running()
    with pytest.raises(CancelledError):
        future.result(timeout=1.0)


def test_shutdown_never_started_is_noop():
    assert AsyncLoopThread().shutdown(timeout=0.1) is True


def test_double_shutdown_is_safe(loop_thread):
    assert loop_thread.shutdown(timeout=2.0) is True
    assert loop_thread.shutdown(timeout=2.0) is True


def test_shutdown_timeout_on_blocked_task():
    lt = AsyncLoopThread(name="blocked-loop")
    lt.start()
    started = threading.Event()

    async def blocker():
        started.set()
        time.sleep(0.5)  # appel BLOQUANT : inannulable, la boucle ne rend pas la main

    lt.submit(blocker())
    assert started.wait(2.0)
    assert lt.shutdown(timeout=0.05) is False  # abandonné, comme un worker bloqué
    lt.shutdown(timeout=2.0)  # laisse le thread finir avant le test suivant


def test_restart_after_clean_shutdown():
    lt = AsyncLoopThread(name="restart-loop")
    lt.start()

    async def one():
        return 1

    assert lt.submit(one()).result(timeout=2.0) == 1
    assert lt.shutdown(timeout=2.0) is True

    lt.start()

    async def two():
        return 2

    assert lt.submit(two()).result(timeout=2.0) == 2
    assert lt.shutdown(timeout=2.0) is True


def test_shutdown_workers_shuts_the_app_loop(monkeypatch):
    """`shutdown_workers()` (lifecycle) doit arrêter la boucle applicative."""
    calls = []
    monkeypatch.setattr(
        lifecycle.async_loop, "shutdown", lambda timeout=8.0: calls.append(timeout) or True
    )
    lifecycle.reset()
    try:
        lifecycle.shutdown_workers(total_timeout=1.0)
    finally:
        lifecycle.reset()
    assert calls == [1.0]
