"""Persistance de l'usage des sources (SQLAlchemy Core).

Objet AUTONOME prenant un `engine`, et non un mixin de `DataManager` : un
capteur posé dans `src/api/` doit pouvoir écrire sans passer par la façade —
et sans en tirer l'import.

Deux tables, deux régimes : des compteurs agrégés qu'on incrémente en upsert,
et une fenêtre glissante des derniers échecs qu'on purge au vidage.
"""

from __future__ import annotations

import atexit
import queue
import threading
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.observability.issues import FAILURES

#: Nombre d'échecs conservés PAR SOURCE — plafond dur d'environ 800 lignes.
FAILURE_WINDOW = 50


class SourceUsageRepository:
    """Lecture/écriture des compteurs d'usage. Ne lève jamais vers l'appelant."""

    def __init__(self, engine) -> None:
        self.engine = engine

    # ── Écriture ───────────────────────────────────────────────────────────────
    def record(self, verdicts: Iterable[Any], *, keep: int = FAILURE_WINDOW) -> None:
        """Consomme un lot de `Verdict` : agrège, puis archive les échecs.

        C'est le `sink` branché sur `source_usage.set_sink`.
        """
        lot = list(verdicts)
        if not lot:
            return
        self.upsert_daily(_aggregate(lot))
        echecs = [v for v in lot if v.issue in FAILURES]
        if echecs:
            self.push_failures(echecs, keep=keep)

    def upsert_daily(self, counters: Sequence[DailyCounter]) -> None:
        """Incrémente les compteurs agrégés (création si la ligne n'existe pas).

        PIÈGE SQLITE : un `INSERT ... ON CONFLICT` ne verrait jamais le conflit
        pour les lignes `artist_id IS NULL` — dans un index UNIQUE, SQLite tient
        deux NULL pour distincts, si bien que l'usage hors contexte artiste
        accumulerait des doublons silencieusement. D'où l'UPDATE explicite avec
        l'opérateur `IS`, qui lui est null-safe, puis l'INSERT si rien n'a bougé.
        """
        if not counters:
            return
        try:
            with self.engine.begin() as conn:
                for counter in counters:
                    params = {
                        "day": counter.day,
                        "src": counter.source_key,
                        "aid": counter.artist_id,
                        "flow": counter.flow,
                        "issue": str(counter.issue),
                        "calls": counter.n_calls,
                        "attempts": counter.n_attempts,
                        "expected": counter.expected_blocked,
                        "latency": counter.latency_ms_total,
                        "seen": counter.last_seen,
                    }
                    updated = conn.execute(
                        text(
                            "UPDATE source_usage_daily SET "
                            "  n_calls = COALESCE(n_calls, 0) + :calls, "
                            "  n_attempts = COALESCE(n_attempts, 0) + :attempts, "
                            "  expected_blocked = COALESCE(expected_blocked, 0) + :expected, "
                            "  latency_ms_total = COALESCE(latency_ms_total, 0) + :latency, "
                            "  last_seen = :seen "
                            "WHERE day = :day AND source_key = :src AND artist_id IS :aid "
                            "  AND flow = :flow AND issue = :issue"
                        ),
                        params,
                    ).rowcount
                    if not updated:
                        conn.execute(
                            text(
                                "INSERT INTO source_usage_daily "
                                "(day, source_key, artist_id, flow, issue, n_calls, "
                                " n_attempts, expected_blocked, latency_ms_total, last_seen) "
                                "VALUES (:day, :src, :aid, :flow, :issue, :calls, "
                                " :attempts, :expected, :latency, :seen)"
                            ),
                            params,
                        )
        except SQLAlchemyError:
            _log().exception("source_usage: compteurs non enregistrés")

    def push_failures(self, verdicts: Sequence[Any], *, keep: int = FAILURE_WINDOW) -> None:
        """Archive les derniers échecs, puis rogne la fenêtre par source."""
        if not verdicts:
            return
        try:
            with self.engine.begin() as conn:
                for verdict in verdicts:
                    conn.execute(
                        text(
                            "INSERT INTO source_usage_failures "
                            "(source_key, issue, artist_id, track_id, status_code, "
                            " message, occurred_at) "
                            "VALUES (:src, :issue, :aid, :tid, :code, :msg, :at)"
                        ),
                        {
                            "src": verdict.source_key,
                            "issue": str(verdict.issue),
                            "aid": verdict.artist_id,
                            "tid": verdict.track_id,
                            "code": verdict.status_code,
                            "msg": (verdict.detail or "")[:300],
                            "at": _isoformat(verdict.at),
                        },
                    )
                for source_key in {v.source_key for v in verdicts}:
                    conn.execute(
                        text(
                            "DELETE FROM source_usage_failures WHERE source_key = :src "
                            "AND id NOT IN (SELECT id FROM source_usage_failures "
                            "               WHERE source_key = :src ORDER BY id DESC LIMIT :keep)"
                        ),
                        {"src": source_key, "keep": keep},
                    )
        except SQLAlchemyError:
            _log().exception("source_usage: échecs non archivés")

    # ── Lecture ────────────────────────────────────────────────────────────────
    def daily(
        self,
        *,
        since_day: str | None = None,
        source_key: str | None = None,
        artist_id: int | None = None,
    ) -> list[dict]:
        """Compteurs bruts, prêts pour `rollup.summarize`.

        `text()` non typé volontairement : `last_seen` doit revenir en STRING
        verbatim, comme au temps du sqlite3 legacy — un `select()` typé la
        parserait en datetime (piège TIMESTAMP double-face).
        """
        clauses, params = [], {}
        if since_day:
            clauses.append("day >= :since")
            params["since"] = since_day
        if source_key:
            clauses.append("source_key = :src")
            params["src"] = source_key
        if artist_id is not None:
            clauses.append("artist_id = :aid")
            params["aid"] = artist_id
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._rows(
            "SELECT day, source_key, artist_id, flow, issue, n_calls, n_attempts, "
            "expected_blocked, latency_ms_total, last_seen FROM source_usage_daily"
            f"{where}",
            params,
        )

    def recent_failures(self, source_key: str | None = None, limit: int = 50) -> list[dict]:
        where = " WHERE source_key = :src" if source_key else ""
        params: dict = {"limit": limit}
        if source_key:
            params["src"] = source_key
        return self._rows(
            "SELECT source_key, issue, artist_id, track_id, status_code, message, "
            f"occurred_at FROM source_usage_failures{where} ORDER BY id DESC LIMIT :limit",
            params,
        )

    def artists_with_usage(self) -> list[tuple[int, str]]:
        """(id, nom) des artistes pour lesquels des sources ont été sollicitées."""
        rows = self._rows(
            "SELECT DISTINCT d.artist_id AS id, a.name AS name FROM source_usage_daily d "
            "JOIN artists a ON a.id = d.artist_id WHERE d.artist_id IS NOT NULL "
            "ORDER BY a.name",
            {},
        )
        return [(r["id"], r["name"]) for r in rows]

    def _rows(self, sql: str, params: dict) -> list[dict]:
        try:
            with self.engine.connect() as conn:
                return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]
        except SQLAlchemyError:
            _log().exception("source_usage: lecture impossible")
            return []


# ── Agrégation d'un lot de verdicts vers des compteurs ────────────────────────
class DailyCounter:
    """Une ligne de `source_usage_daily` à incrémenter."""

    __slots__ = (
        "day",
        "source_key",
        "artist_id",
        "flow",
        "issue",
        "n_calls",
        "n_attempts",
        "expected_blocked",
        "latency_ms_total",
        "last_seen",
    )

    def __init__(self, day, source_key, artist_id, flow, issue):
        self.day = day
        self.source_key = source_key
        self.artist_id = artist_id
        self.flow = flow
        self.issue = issue
        self.n_calls = 0
        self.n_attempts = 0
        self.expected_blocked = 0
        self.latency_ms_total = 0
        self.last_seen: str | None = None


def _aggregate(verdicts: Sequence[Any]) -> list[DailyCounter]:
    """Replie un lot de verdicts en compteurs (jour, source, artiste, flux, nature).

    `n_calls` compte les OCCURRENCES, y compris celles hors dénominateur : un
    `indeterminate` doit rester visible pour signaler un trou de capteur. C'est
    `rollup` qui décide ensuite de ce qui entre dans le taux d'échec — la
    persistance ne doit pas trancher à sa place.
    """
    groupes: dict[tuple, DailyCounter] = {}

    for verdict in verdicts:
        moment = verdict.at if isinstance(verdict.at, datetime) else datetime.now()
        cle = (
            moment.strftime("%Y-%m-%d"),
            verdict.source_key,
            verdict.artist_id,
            verdict.flow,
            str(verdict.issue),
        )
        counter = groupes.get(cle)
        if counter is None:
            counter = groupes[cle] = DailyCounter(*cle)
        counter.n_calls += 1
        counter.n_attempts += verdict.attempts
        counter.expected_blocked += verdict.expected_blocked
        counter.latency_ms_total += verdict.latency_ms or 0
        counter.last_seen = _isoformat(moment)

    return list(groupes.values())


def _isoformat(moment) -> str:
    if isinstance(moment, datetime):
        return moment.isoformat(timespec="seconds")
    return str(moment)


class BackgroundWriter:
    """Écrit les verdicts sur un fil dédié, jamais sur celui de l'appelant.

    Le vidage part parfois de la boucle asyncio (le batch d'enrichissement est
    une coroutine) et parfois du thread du `sync_runner`. Or ces deux-là
    écrivent déjà dans la même base SQLite : une écriture de compteurs qui
    tombe pendant un `save_track` attendrait le verrou — et bloquerait la
    boucle. Le fil dédié rend le vidage non bloquant partout, ce qui dispense
    d'un `asyncio.to_thread` au site d'appel.
    """

    def __init__(self, repo: SourceUsageRepository) -> None:
        self._repo = repo
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._boucle, name="source-usage-writer", daemon=True
        )
        self._thread.start()

    def __call__(self, verdicts) -> None:
        """Le `sink` : dépose et rend la main immédiatement."""
        lot = list(verdicts)
        if lot:
            self._queue.put(lot)

    def _boucle(self) -> None:
        while True:
            lot = self._queue.get()
            if lot is None:  # sentinelle d'arrêt
                return
            self._repo.record(lot)

    def close(self, timeout: float = 3.0) -> None:
        """Écrit ce qui reste puis arrête le fil (idempotent)."""
        if not self._thread.is_alive():
            return
        self._queue.put(None)
        self._thread.join(timeout)


def attach(engine) -> SourceUsageRepository:
    """Branche la persistance sur le capteur (appelé UNE fois, au démarrage app).

    Tant que personne n'appelle ceci, les verdicts sont simplement jetés — les
    tests et les CLI n'ont donc rien à désactiver.
    """
    from src.observability import source_usage

    repo = SourceUsageRepository(engine)
    writer = BackgroundWriter(repo)
    source_usage.set_sink(writer)
    # Dernier filet : l'app peut mourir sans passer par la fermeture GUI.
    atexit.register(lambda: (source_usage.flush(), writer.close()))
    return repo


@contextmanager
def script_scope(flow: str):
    """Branche les compteurs pour un script lancé en SOUS-PROCESSUS.

    Les mises à jour de certifications tournent hors de la GUI (`subprocess.run`
    depuis `certification_update_gui`) : sans ce branchement, leur usage des
    sources ne serait compté nulle part. La base étant la même, les compteurs
    rejoignent ceux de l'application. Un échec de branchement laisse le script
    tourner normalement — un compteur ne bloque jamais une mise à jour.
    """
    from src.observability import source_usage

    branche = False
    try:
        from src.utils.data_manager import DataManager

        attach(DataManager().engine)
        branche = True
    except Exception as e:  # noqa: BLE001 — compteur best-effort
        _log().warning(f"compteurs d'usage indisponibles : {e}")

    if not branche:
        yield
        return
    with source_usage.run_scope(flow):
        yield


def _log():
    """Logger projet, importé paresseusement (cf. `source_usage._logger`)."""
    from src.utils.logger import get_logger

    return get_logger(__name__)
