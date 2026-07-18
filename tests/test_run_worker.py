"""Tests de `lifecycle.run_worker` (Phase F5) — flux de fond sous la boucle unique."""

import threading

import pytest

from src.concurrency import async_loop
from src.gui.workers import lifecycle


@pytest.fixture(autouse=True)
def _clean():
    lifecycle.reset()
    yield
    async_loop.shutdown(timeout=2.0)
    lifecycle.reset()


def test_execute_le_corps_et_renvoie_le_resultat():
    fut = lifecycle.run_worker(lambda: 6 * 7, name="calc")
    assert fut.result(timeout=3.0) == 42
    assert async_loop.is_running()  # la boucle a été démarrée


def test_corps_hors_boucle_peut_appeler_run_sync():
    """Le corps tourne sur un thread ≠ boucle → run_sync (crawls F4) fonctionne."""

    async def coro():
        return "depuis la boucle"

    def body():
        # Nom du thread porteur ≠ boucle, ET run_sync ne deadlocke pas
        return threading.current_thread().name, async_loop.run_sync(coro())

    thread_name, inner = lifecycle.run_worker(body, name="bridge-worker").result(timeout=5.0)
    assert thread_name == "bridge-worker"
    assert thread_name != "asyncio-loop"
    assert inner == "depuis la boucle"


def test_thread_enregistre_et_joignable_au_shutdown():
    started = threading.Event()
    released = threading.Event()

    def body():
        started.set()
        released.wait(timeout=5.0)  # bloque jusqu'à libération

    lifecycle.run_worker(body, name="registered-worker")
    assert started.wait(2.0)
    with lifecycle._lock:
        names = [w.name for w in lifecycle._workers if w.is_alive()]
    assert "registered-worker" in names  # enregistré → shutdown_workers le joindra
    released.set()


def test_stop_requested_visible_depuis_le_corps():
    seen = {}

    def body():
        seen["avant"] = lifecycle.stop_requested()
        lifecycle._stop_event.set()  # simule shutdown
        seen["apres"] = lifecycle.stop_requested()

    lifecycle.run_worker(body, name="stop-aware").result(timeout=3.0)
    assert seen == {"avant": False, "apres": True}


def test_exception_du_corps_remontee_a_la_future():
    def boom():
        raise ValueError("échec worker")

    with pytest.raises(ValueError, match="échec worker"):
        lifecycle.run_worker(boom, name="boom").result(timeout=3.0)


def test_shutdown_workers_joint_le_thread_de_run_worker():
    done = threading.Event()

    def body():
        while not lifecycle.stop_requested():
            done.wait(0.02)
        done.set()

    lifecycle.run_worker(body, name="joinable")
    # Laisse le thread démarrer sa boucle
    threading.Event().wait(0.1)
    survivors = lifecycle.shutdown_workers(total_timeout=3.0)
    assert "joinable" not in survivors  # s'est arrêté proprement au stop
    assert done.is_set()
