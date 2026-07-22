"""File d'exécution sync sur UN thread daemon dédié (Phase F2).

Les scrapers Playwright sync sont THREAD-AFFINES : un browser doit naître,
travailler et mourir dans le même thread. `asyncio.to_thread` utilise un pool
(thread variable d'un appel à l'autre) et ses threads ne sont pas daemon (un
scrape bloqué empêcherait l'app de se fermer). `SerialWorker` garantit LE MÊME
thread daemon pour tout le travail sync d'un flux async : création, usage et
fermeture des browsers d'un batch s'y déroulent, comme dans l'ancien thread
worker.
"""

import asyncio
import queue
import threading
from concurrent.futures import Future

_STOP = object()


class SerialWorker:
    """Exécute des fonctions sync, une à la fois, toujours sur le même thread."""

    def __init__(self, name: str = "sync-worker") -> None:
        self._name = name
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
                self._thread.start()

    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            future, fn, args, kwargs = item
            if not future.set_running_or_notify_cancel():
                continue  # annulé avant exécution
            try:
                future.set_result(fn(*args, **kwargs))
            except BaseException as exc:  # noqa: BLE001 — propagé à l'appelant via la Future
                future.set_exception(exc)

    def submit(self, fn, /, *args, **kwargs) -> Future:
        """Planifie `fn(*args, **kwargs)` sur le thread dédié (démarré lazy)."""
        self._ensure_thread()
        future: Future = Future()
        self._queue.put((future, fn, args, kwargs))
        return future

    async def run(self, fn, /, *args, **kwargs):
        """Version awaitable de `submit` — à utiliser depuis les coroutines."""
        return await asyncio.wrap_future(self.submit(fn, *args, **kwargs))

    def shutdown(self, timeout: float = 2.0) -> None:
        """Arrête le thread après la file en cours (un submit ultérieur le relance).

        Join borné : au retour, le thread est mort (sauf tâche bloquée — daemon,
        abandonné au processus comme les workers).
        """
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                return
            self._queue.put(_STOP)
        thread.join(timeout)
