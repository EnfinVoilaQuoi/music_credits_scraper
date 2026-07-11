"""Arrêt propre des threads workers (AUDIT §4 « threads démons sans arrêt propre »).

Problème d'origine : les workers étaient des threads daemon lancés à la volée ;
à la fermeture de l'app ils étaient tués net, potentiellement au milieu d'un
`save_track` (le commit SQLite reste atomique, mais le morceau en cours était
perdu et un lot s'arrêtait sans trace).

Le contrat, en trois pièces :
  · les workers démarrent via `start_worker()` (toujours daemon : un scraper
    Playwright bloqué ne doit JAMAIS empêcher l'app de se fermer) ;
  · les boucles par-morceau testent `stop_requested()` ENTRE deux unités de
    travail — jamais au milieu d'une écriture ;
  · `_on_closing` appelle `shutdown_workers()` : drapeau levé, puis join avec
    un budget de temps global — le morceau en cours finit proprement, les
    threads réellement bloqués sont abandonnés au processus (comme avant).
"""

import threading
import time

from src.utils.logger import get_logger

logger = get_logger(__name__)

_stop_event = threading.Event()
_workers: list[threading.Thread] = []
_lock = threading.Lock()


def start_worker(target, name: str | None = None) -> threading.Thread:
    """Lance `target` dans un thread daemon ENREGISTRÉ (joint à la fermeture)."""
    thread = threading.Thread(
        target=target,
        name=name or f"worker:{getattr(target, '__name__', 'anonyme')}",
        daemon=True,
    )
    with _lock:
        _workers[:] = [w for w in _workers if w.is_alive()]  # purge des terminés
        _workers.append(thread)
    thread.start()
    return thread


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
    with _lock:
        workers = [w for w in _workers if w.is_alive()]
    if not workers:
        return []

    logger.info(f"⏳ Fermeture : attente de {len(workers)} worker(s) en cours…")
    deadline = time.monotonic() + total_timeout
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
