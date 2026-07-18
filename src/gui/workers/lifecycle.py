"""Arrêt propre des threads workers (AUDIT §4 « threads démons sans arrêt propre »).

Problème d'origine : les workers étaient des threads daemon lancés à la volée ;
à la fermeture de l'app ils étaient tués net, potentiellement au milieu d'un
`save_track` (le commit SQLite reste atomique, mais le morceau en cours était
perdu et un lot s'arrêtait sans trace).

Le contrat, en trois pièces :
  · les workers démarrent via `run_worker()` (Phase F5 : corps sync sur un
    thread daemon ENREGISTRÉ, représenté par une coroutine de la boucle unique)
    ou via `start_worker()` (primitive daemon nue, pour le travail de fond
    purement sync sans lien avec la boucle : export SVG, santé des sources…) ;
  · les boucles par-morceau testent `stop_requested()` ENTRE deux unités de
    travail — jamais au milieu d'une écriture ;
  · `_on_closing` appelle `shutdown_workers()` : drapeau levé, annulation des
    tasks de la boucle, puis join des threads avec un budget de temps global —
    le morceau en cours finit proprement, les threads réellement bloqués sont
    abandonnés au processus (comme avant).
"""

import asyncio
import threading
import time
from concurrent.futures import Future

from src.concurrency import async_loop
from src.utils.logger import get_logger

logger = get_logger(__name__)

_stop_event = threading.Event()
_workers: list[threading.Thread] = []
_lock = threading.Lock()


def _register_daemon(target, name: str) -> threading.Thread:
    """Démarre `target` dans un thread daemon ENREGISTRÉ (joint à la fermeture)."""
    thread = threading.Thread(target=target, name=name, daemon=True)
    with _lock:
        _workers[:] = [w for w in _workers if w.is_alive()]  # purge des terminés
        _workers.append(thread)
    thread.start()
    return thread


def _worker_label(target, name: str | None) -> str:
    return name or f"worker:{getattr(target, '__name__', 'anonyme')}"


def start_worker(target, name: str | None = None) -> threading.Thread:
    """Lance `target` dans un thread daemon ENREGISTRÉ (joint à la fermeture).

    Primitive de bas niveau, réservée au travail de fond purement sync qui ne
    touche PAS la boucle asyncio (génération SVG, fenêtres d'état…). Les flux
    qui pilotent scrapers/API/DB passent par `run_worker` (Phase F5).
    """
    return _register_daemon(target, _worker_label(target, name))


def run_worker(target, name: str | None = None) -> Future:
    """Phase F5 — entrée unifiée des flux de fond sous la boucle unique.

    `target` (sync) s'exécute sur un thread daemon ENREGISTRÉ (joint à la
    fermeture avec le budget de `shutdown_workers`, comme `start_worker`) ;
    une coroutine soumise à LA boucle applicative le représente (coordination
    d'arrêt : `async_loop.shutdown` annule la coroutine, le corps s'arrête au
    prochain `stop_requested()`). Le corps tourne HORS boucle → il peut appeler
    `async_loop.run_sync` (crawls F4) et les ponts sync sans deadlock.

    Renvoie le `concurrent.futures.Future` de la coroutine (fire-and-forget :
    le corps gère ses propres erreurs et restaure l'UI via `root.after`).
    """
    async_loop.start()
    label = _worker_label(target, name)

    def _runner(fut: Future) -> None:
        if not fut.set_running_or_notify_cancel():
            return  # annulé avant démarrage
        try:
            fut.set_result(target())
        except BaseException as exc:  # remonté à la coroutine via wrap_future
            fut.set_exception(exc)

    async def _coro():
        fut: Future = Future()
        _register_daemon(lambda: _runner(fut), label)
        return await asyncio.wrap_future(fut)

    return async_loop.submit(_coro())


def stop_requested() -> bool:
    """À tester entre deux unités de travail (jamais au milieu d'un save)."""
    return _stop_event.is_set()


def shutdown_workers(total_timeout: float = 8.0) -> list[str]:
    """Lève le drapeau d'arrêt puis attend la fin des workers enregistrés.

    Returns:
        Noms des threads encore vivants après épuisement du budget (ils seront
        tués par la fin du processus, comme l'ancien comportement daemon).
    """
    _stop_event.set()
    deadline = time.monotonic() + total_timeout
    # Boucle asyncio d'abord (garde-fou Phase F : annuler les tasks AVANT de
    # fermer les ressources qu'elles utilisent) ; no-op immédiat si jamais
    # démarrée. Budget global partagé avec le join des threads.
    async_loop.shutdown(timeout=total_timeout)
    with _lock:
        workers = [w for w in _workers if w.is_alive()]
    if not workers:
        return []

    logger.info(f"⏳ Fermeture : attente de {len(workers)} worker(s) en cours…")
    survivors = []
    for thread in workers:
        remaining = deadline - time.monotonic()
        if remaining > 0:
            thread.join(timeout=remaining)
        if thread.is_alive():
            survivors.append(thread.name)
    if survivors:
        logger.warning(
            f"⚠️ Workers toujours actifs après {total_timeout}s : {survivors} — abandonnés"
        )
    else:
        logger.info("✅ Tous les workers se sont arrêtés proprement")
    return survivors


def reset() -> None:
    """Ré-arme le drapeau d'arrêt et vide le registre (utilisé par les tests)."""
    _stop_event.clear()
    with _lock:
        _workers.clear()
