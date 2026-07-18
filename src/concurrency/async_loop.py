"""Boucle asyncio unique de l'application, hébergée dans UN thread dédié.

Contrat (REFONTE Phase F1) :
  · `start()` au lancement de l'app — démarre le thread et attend que la boucle
    soit réellement prête (un `submit` immédiat ne peut pas la rater) ;
  · `submit(coro) -> concurrent.futures.Future` via `run_coroutine_threadsafe` —
    utilisable depuis n'importe quel thread (GUI comprise) ;
  · `shutdown(timeout=8.0)` — annule les tasks en cours puis join le thread ;
    appelé par `shutdown_workers()` de `src/gui/workers/lifecycle.py` dans le
    même budget global de fermeture.

Le thread est daemon, comme les workers : une task réellement bloquée (appel
sync qui ne rend pas la main) ne doit JAMAIS empêcher l'app de se fermer — elle
est abandonnée au processus après épuisement du budget.
"""

import asyncio
import threading
from collections.abc import Coroutine
from concurrent.futures import Future
from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)


def _cancel_pending_tasks(loop: asyncio.AbstractEventLoop) -> None:
    """Annule les tasks restantes et leur laisse traiter l'annulation.

    Même séquence que la sortie d'`asyncio.run` : cancel de tout, puis un
    dernier tour de boucle pour que les `CancelledError` se propagent (les
    Futures `run_coroutine_threadsafe` correspondantes sont alors annulées).
    """
    pending = asyncio.all_tasks(loop)
    if not pending:
        return
    for task in pending:
        task.cancel()
    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    for task in pending:
        if task.cancelled():
            continue
        exc = task.exception()
        if exc is not None:
            logger.warning(f"⚠️ Task terminée sur exception pendant l'arrêt : {exc!r}")


class AsyncLoopThread:
    """Une boucle asyncio dans un thread dédié, pilotable depuis les autres threads."""

    def __init__(self, name: str = "asyncio-loop") -> None:
        self._name = name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        loop = self._loop
        return loop is not None and loop.is_running()

    def start(self) -> None:
        """Démarre la boucle (idempotent) et ne rend la main que boucle prête."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
            self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        loop.call_soon(self._ready.set)
        try:
            loop.run_forever()
        finally:
            try:
                _cancel_pending_tasks(loop)
                loop.run_until_complete(loop.shutdown_asyncgens())
            except RuntimeError as exc:  # stop() reçu pendant le nettoyage final
                logger.warning(f"⚠️ Nettoyage de la boucle interrompu : {exc}")
            finally:
                self._loop = None
                loop.close()

    def submit(self, coro: Coroutine[Any, Any, Any]) -> Future:
        """Planifie `coro` dans la boucle ; lève RuntimeError si elle ne tourne pas."""
        loop = self._loop
        if loop is None or not loop.is_running():
            coro.close()  # sinon RuntimeWarning « coroutine never awaited »
            raise RuntimeError("Boucle asyncio non démarrée — appeler start() d'abord")
        return asyncio.run_coroutine_threadsafe(coro, loop)

    def shutdown(self, timeout: float = 8.0) -> bool:
        """Annule les tasks en cours puis attend la fin du thread.

        Returns:
            True si l'arrêt est propre (ou si rien ne tournait) ; False si le
            thread est encore vivant après `timeout` (task bloquée — abandonnée
            au processus, comme les workers daemon).
        """
        with self._lock:
            thread, loop = self._thread, self._loop
            if thread is None or not thread.is_alive():
                return True
            if loop is not None:
                # stop() fait sortir run_forever ; l'annulation des tasks se
                # joue ensuite dans le finally de _run, DANS le thread boucle.
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass  # double shutdown : boucle déjà en cours de fermeture
        thread.join(timeout)
        if thread.is_alive():
            logger.warning(f"⚠️ Boucle asyncio encore active après {timeout}s — thread abandonné")
            return False
        return True


# Singleton applicatif : UNE boucle pour toute l'app (AUDIT §8.3 « un modèle de
# concurrence unique »). Les tests instancient leur propre AsyncLoopThread.
_app_loop = AsyncLoopThread()


def start() -> None:
    _app_loop.start()


def is_running() -> bool:
    return _app_loop.is_running()


def submit(coro: Coroutine[Any, Any, Any]) -> Future:
    return _app_loop.submit(coro)


def shutdown(timeout: float = 8.0) -> bool:
    return _app_loop.shutdown(timeout)
