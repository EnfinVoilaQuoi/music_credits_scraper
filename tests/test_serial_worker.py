"""Tests du SerialWorker (Phase F2) — affinité de thread garantie."""

import threading

import pytest

from src.concurrency.async_loop import AsyncLoopThread
from src.concurrency.serial_worker import SerialWorker


def test_submit_returns_result():
    worker = SerialWorker("test-serial")
    assert worker.submit(lambda: 21 * 2).result(timeout=2.0) == 42


def test_exception_propagates():
    worker = SerialWorker("test-serial")

    def boom():
        raise ValueError("boum")

    with pytest.raises(ValueError, match="boum"):
        worker.submit(boom).result(timeout=2.0)


def test_same_thread_across_submissions():
    """Le cœur du contrat : tous les appels tournent sur LE MÊME thread."""
    worker = SerialWorker("affinity")
    names = [worker.submit(lambda: threading.current_thread().name).result(2.0) for _ in range(5)]
    assert set(names) == {"affinity"}


def test_thread_is_daemon():
    """Un scrape bloqué ne doit pas empêcher l'app de se fermer (comme les workers)."""
    worker = SerialWorker("daemon-check")
    assert worker.submit(lambda: threading.current_thread().daemon).result(2.0) is True


def test_serial_execution_order():
    worker = SerialWorker("ordered")
    seen = []
    futures = [worker.submit(seen.append, i) for i in range(10)]
    for future in futures:
        future.result(timeout=2.0)
    assert seen == list(range(10))


def test_restart_after_shutdown():
    worker = SerialWorker("restartable")
    assert worker.submit(lambda: 1).result(2.0) == 1
    worker.shutdown()
    assert worker.submit(lambda: 2).result(2.0) == 2  # thread relancé lazy


def test_run_awaitable_from_loop():
    """`run()` s'await depuis une coroutine et exécute sur le thread dédié."""
    loop_thread = AsyncLoopThread(name="test-loop-serial")
    loop_thread.start()
    worker = SerialWorker("from-loop")
    try:

        async def scenario():
            return await worker.run(lambda: threading.current_thread().name)

        assert loop_thread.submit(scenario()).result(timeout=2.0) == "from-loop"
    finally:
        loop_thread.shutdown(timeout=2.0)
        worker.shutdown()
