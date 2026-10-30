"""Persistance de l'usage des sources (SQLAlchemy Core).

Objet AUTONOME prenant un `engine`, et non un mixin de `DataManager` : un
capteur posé dans `src/api/` doit pouvoir écrire sans passer par la façade —
et sans en tirer l'import.

Trois tables, trois régimes : des compteurs agrégés qu'on incrémente en
upsert, une fenêtre glissante des derniers échecs qu'on purge au vidage (le
diagnostic), et le suivi des échecs PAR MORCEAU, qui ne se vide que lorsque la
source répond (ce qui est à retenter).
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

from src.observability.issues import BENIGN, FAILURES, IssueKind

#: Nombre d'échecs conservés PAR SOURCE — plafond dur d'environ 800 lignes.
FAILURE_WINDOW = 50

#: Sources retirées du projet : leurs échecs suivis n'attendent plus de réponse.
SOURCES_RETIREES = ("bpmfinder",)


def cle_absence(source: str, donnee: str) -> str:
    """Clé d'un constat d'absence PAR DONNÉE dans `source_track_absences`
    (2026-10-29, sans migration) : « la source a répondu, mais sans CETTE
    donnée ». Une page Genius lue sans crédit n'est pas une page sans paroles :
    un constat de toute la source (`genius_scrape`) ferait taire aussi la
    recherche de paroles. Le constat de toute la source garde la clé nue."""
    return f"{source}:{donnee}"


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
        self.suivre_morceaux(lot)

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

    def suivre_morceaux(self, verdicts: Sequence[Any]) -> None:
        """Tient `source_track_failures` : un échec de communication pose ou met à
        jour la ligne (morceau, source), une RÉPONSE de la source (`ok`,
        `absent`) la retire. Les autres natures ne touchent à rien.

        Seuls les verdicts portant un morceau comptent — un appel `hors_morceau`
        (artiste, disque, lot) n'y entre jamais et n'en efface jamais rien. Les
        verdicts sont traités DANS L'ORDRE d'émission : un succès qui suit un
        échec du même morceau dans le même lot le retire.
        """
        lot = [
            v
            for v in verdicts
            if v.track_id is not None and (v.issue in FAILURES or v.issue in BENIGN)
        ]
        if not lot:
            return
        try:
            with self.engine.begin() as conn:
                for v in lot:
                    cle = {"tid": v.track_id, "src": v.source_key}
                    if v.issue in BENIGN:
                        conn.execute(
                            text(
                                "DELETE FROM source_track_failures "
                                "WHERE track_id = :tid AND source_key = :src"
                            ),
                            cle,
                        )
                        self._noter_absence(conn, v, cle)
                        continue
                    params = {
                        **cle,
                        "flow": v.flow,
                        "aid": v.artist_id,
                        "issue": str(v.issue),
                        "code": v.status_code,
                        "msg": (v.detail or "")[:300],
                        "at": _isoformat(v.at),
                    }
                    maj = conn.execute(
                        text(
                            "UPDATE source_track_failures SET flow = :flow, "
                            "  artist_id = :aid, issue = :issue, status_code = :code, "
                            "  message = :msg, n_echecs = n_echecs + 1, dernier_echec = :at "
                            "WHERE track_id = :tid AND source_key = :src"
                        ),
                        params,
                    ).rowcount
                    if not maj:
                        conn.execute(
                            text(
                                "INSERT INTO source_track_failures "
                                "(track_id, source_key, flow, artist_id, issue, status_code, "
                                " message, n_echecs, premier_echec, dernier_echec) "
                                "VALUES (:tid, :src, :flow, :aid, :issue, :code, "
                                " :msg, 1, :at, :at)"
                            ),
                            params,
                        )
        except SQLAlchemyError:
            _log().exception("source_usage: suivi des échecs par morceau non enregistré")

    @staticmethod
    def _noter_absence(conn, v, cle: dict) -> None:
        """Tient `source_track_absences` (e46) : `absent` pose ou date le constat
        (morceau, source), `ok` le retire — la source a trouvé, plus rien à
        mémoriser."""
        if v.issue != IssueKind.ABSENT:
            conn.execute(
                text(
                    "DELETE FROM source_track_absences "
                    "WHERE track_id = :tid AND source_key = :src"
                ),
                cle,
            )
            return
        params = {**cle, "at": _isoformat(v.at)}
        maj = conn.execute(
            text(
                "UPDATE source_track_absences SET derniere_absence = :at "
                "WHERE track_id = :tid AND source_key = :src"
            ),
            params,
        ).rowcount
        if not maj:
            conn.execute(
                text(
                    "INSERT INTO source_track_absences "
                    "(track_id, source_key, premiere_absence, derniere_absence) "
                    "VALUES (:tid, :src, :at, :at)"
                ),
                params,
            )

    def constater_absences(
        self, cle: str, poser: Iterable[tuple[int, datetime]], retirer: Iterable[int] = ()
    ) -> None:
        """Constats d'absence posés hors verdict (clé `cle_absence`) : `poser`
        (morceau, date de la lecture qui a répondu « rien ») pose le constat ou
        le date — jamais en arrière, une lecture plus récente fait foi ;
        `retirer` le lève (la donnée a été trouvée). Même table, même
        expiration (`settings.delai_absence_jours`) que les constats des
        verdicts."""
        poser = [(tid, _isoformat(at)) for tid, at in poser]
        retirer = list(retirer)
        if not (poser or retirer):
            return
        try:
            with self.engine.begin() as conn:
                for tid in retirer:
                    conn.execute(
                        text(
                            "DELETE FROM source_track_absences "
                            "WHERE track_id = :tid AND source_key = :src"
                        ),
                        {"tid": tid, "src": cle},
                    )
                for tid, at in poser:
                    params = {"tid": tid, "src": cle, "at": at}
                    maj = conn.execute(
                        text(
                            "UPDATE source_track_absences SET derniere_absence = "
                            "MAX(COALESCE(derniere_absence, :at), :at) "
                            "WHERE track_id = :tid AND source_key = :src"
                        ),
                        params,
                    ).rowcount
                    if not maj:
                        conn.execute(
                            text(
                                "INSERT INTO source_track_absences "
                                "(track_id, source_key, premiere_absence, derniere_absence) "
                                "VALUES (:tid, :src, :at, :at)"
                            ),
                            params,
                        )
        except SQLAlchemyError:
            _log().exception(f"source_usage: constats d'absence « {cle} » non enregistrés")

    def absences_recentes(self, sources: Iterable[str], depuis: datetime) -> dict[int, set[str]]:
        """`{track_id: {sources}}` des constats d'absence posés depuis `depuis`."""
        sources = list(sources)
        if not sources:
            return {}
        noms = [f"s{i}" for i in range(len(sources))]
        lignes = self._rows(
            "SELECT track_id, source_key FROM source_track_absences "
            f"WHERE source_key IN ({', '.join(':' + n for n in noms)}) "
            "AND derniere_absence >= :depuis",
            {**dict(zip(noms, sources, strict=True)), "depuis": _isoformat(depuis)},
        )
        par_morceau: dict[int, set[str]] = {}
        for r in lignes:
            par_morceau.setdefault(r["track_id"], set()).add(r["source_key"])
        return par_morceau

    def requalifier_anciens_echecs(self) -> tuple[int, int]:
        """Applique aux lignes DÉJÀ suivies les règles de classement du
        2026-10-10 (CORRECTIFS 1.13), écrites avant elles : une absence ou un
        refus d'YTM (« pas de paroles pour cette vidéo », « titre non
        confirmé », « aucun résultat ») est une RÉPONSE — la ligne part, comme
        `suivre_morceaux` la retirerait ; une annulation par notre délai de garde
        (`CancelledError`) est un `timeout`.

        Règles du 2026-10-11 : une absence LRCLIB que `/search` a rendue en 200
        après un `/get` tombé est une réponse — elle devient un CONSTAT
        d'absence (e46), daté comme l'échec ; les lignes d'une source RETIRÉE
        du projet (BPM Finder) partent. Rend (retirées, requalifiées)."""
        reponses = (
            "message LIKE 'pas de paroles%' OR message LIKE '%non confirmé%' "
            "OR message LIKE 'aucun résultat%'"
        )
        lrclib_repondu = (
            "source_key = 'lrclib' AND status_code = 200 " "AND message LIKE 'aucune parole%'"
        )
        try:
            with self.engine.begin() as conn:
                retirees = conn.execute(
                    text(
                        "DELETE FROM source_track_failures "
                        f"WHERE issue = 'crash' AND ({reponses})"
                    )
                ).rowcount
                conn.execute(
                    text(
                        "INSERT INTO source_track_absences "
                        "(track_id, source_key, premiere_absence, derniere_absence) "
                        "SELECT f.track_id, f.source_key, f.premier_echec, f.dernier_echec "
                        f"FROM source_track_failures f WHERE {lrclib_repondu} "
                        "AND NOT EXISTS (SELECT 1 FROM source_track_absences a "
                        "WHERE a.track_id = f.track_id AND a.source_key = f.source_key)"
                    )
                )
                retirees += conn.execute(
                    text(f"DELETE FROM source_track_failures WHERE {lrclib_repondu}")
                ).rowcount
                retirees += conn.execute(
                    text(
                        "DELETE FROM source_track_failures WHERE source_key IN "
                        f"({', '.join(repr(s) for s in SOURCES_RETIREES)})"
                    )
                ).rowcount
                requalifiees = conn.execute(
                    text(
                        "UPDATE source_track_failures SET issue = 'timeout' "
                        "WHERE issue = 'crash' AND message LIKE 'CancelledError%'"
                    )
                ).rowcount
        except SQLAlchemyError:
            _log().exception("source_usage: requalification des échecs suivis impossible")
            return 0, 0
        return retirees, requalifiees

    # ── Lecture ────────────────────────────────────────────────────────────────
    def echecs_suivis(
        self,
        *,
        sources: Iterable[str] | None = None,
        track_ids: Iterable[int] | None = None,
    ) -> list[dict]:
        """Les échecs en attente d'une réponse, du plus récent au plus ancien,
        restreints aux `sources` et/ou aux `track_ids` s'ils sont donnés."""
        clauses, params = [], {}
        for nom, valeurs, colonne in (
            ("src", sources, "source_key"),
            ("tid", track_ids, "track_id"),
        ):
            if valeurs is None:
                continue
            valeurs = list(valeurs)
            if not valeurs:
                return []
            noms = [f"{nom}{i}" for i in range(len(valeurs))]
            clauses.append(f"{colonne} IN ({', '.join(':' + n for n in noms)})")
            params.update(zip(noms, valeurs, strict=True))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._rows(
            "SELECT track_id, source_key, flow, artist_id, issue, status_code, message, "
            "n_echecs, premier_echec, dernier_echec FROM source_track_failures"
            f"{where} ORDER BY dernier_echec DESC, id DESC",
            params,
        )

    def morceaux_en_echec(self, sources: Iterable[str] | None = None) -> set[int]:
        """Les morceaux qu'au moins une de ces `sources` n'a pas servis."""
        return {r["track_id"] for r in self.echecs_suivis(sources=sources)}

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
