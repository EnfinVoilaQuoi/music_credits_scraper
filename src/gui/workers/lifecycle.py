"""Ré-export de compatibilité : le module vit dans `src/concurrency/lifecycle.py`
depuis le 2026-09-14 (partagé avec les services hors GUI). Les tests qui
patchent `lifecycle._stop_event`/`_workers` passent par l'objet module réel."""

from src.concurrency import lifecycle as _lifecycle
from src.concurrency.lifecycle import (  # noqa: F401
    request_stop,
    reset,
    run_worker,
    shutdown_workers,
    start_worker,
    stop_requested,
)

__all__ = [
    "request_stop",
    "reset",
    "run_worker",
    "shutdown_workers",
    "start_worker",
    "stop_requested",
]


def __getattr__(name: str):
    # Attributs privés (`_stop_event`, `_workers`, `_lock`) : lus sur le VRAI
    # module pour que les tests existants observent le même état.
    return getattr(_lifecycle, name)
