"""Le capteur : compter ce que l'usage RÉEL des sources révèle.

Deux étages, à ne jamais confondre :

  · **tentative** — un aller-retour réseau. Elle vit en mémoire, n'est jamais
    persistée telle quelle, et sert à fournir la NATURE de l'échec, que le
    niveau métier ignore ;
  · **observation** — un appel LOGIQUE à une source, donc exactement UN verdict.
    ReccoBeats pour un morceau fait ISRC Deezer + scrape Spotify + appel API
    avec retries : cela vaut 1 verdict, pas 5.

Le taux d'échec se calcule sur les verdicts, jamais sur les tentatives. C'est
ce qui rend le comptage insensible au nombre de requêtes internes d'un client.

Un capteur ne casse JAMAIS l'observé : `observe()` classe puis RÉ-ÉLÈVE toute
exception, et une erreur d'enregistrement produit un log, rien d'autre.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime

from src.observability.issues import (
    DEFAULT_ABSENT_STATUSES,
    IssueKind,
    classify,
    classify_status,
    worst,
)
from src.observability.registry import Flow, key_for_domain

#: Nombre de verdicts accumulés avant vidage automatique — un batch de 500
#: morceaux ne doit pas tout perdre si l'app est tuée en cours de route.
FLUSH_EVERY = 200


def _logger():
    """Logger projet, importé PARESSEUSEMENT.

    `src/utils/__init__` importe `DataEnricher` : un import au niveau module
    tirerait tout le pipeline dès qu'une couche basse importe ce fichier. Ici
    l'import n'a lieu que sur un chemin d'erreur, quand tout est déjà chargé.
    """
    from src.utils.logger import get_logger

    return get_logger(__name__)


# ── Modèles ────────────────────────────────────────────────────────────────────
@dataclass
class Attempt:
    """Un aller-retour réseau. Diagnostic seul : ne produit jamais de verdict."""

    kind: IssueKind
    detail: str = ""
    status_code: int | None = None
    latency_ms: int | None = None
    #: Blocage anti-bot ATTENDU (source `tolerate_403`) : exclu du calcul du pire
    #: et du taux d'échec, mais compté à part pour rester visible.
    expected: bool = False


@dataclass(frozen=True)
class Verdict:
    """Le résultat d'une observation : ce qui sera persisté."""

    source_key: str
    issue: IssueKind
    flow: str
    artist_id: int | None = None
    track_id: int | None = None
    detail: str = ""
    status_code: int | None = None
    latency_ms: int | None = None
    attempts: int = 0
    expected_blocked: int = 0
    at: datetime = field(default_factory=datetime.now)


@dataclass
class RunScope:
    """Contexte ambiant d'un batch : quel artiste, quel grand flux."""

    flow: str
    artist_id: int | None = None
    artist_name: str = ""


class Observation:
    """Un appel logique à une source. Le corps métier peut déclarer son verdict."""

    def __init__(
        self,
        source_key: str,
        *,
        artist_id: int | None = None,
        track_id: int | None = None,
        label: str = "",
        absent_statuses: tuple[int, ...] = DEFAULT_ABSENT_STATUSES,
    ) -> None:
        self.source_key = source_key
        self.artist_id = artist_id
        self.track_id = track_id
        self.label = label
        self.absent_statuses = absent_statuses
        self.attempts: list[Attempt] = []
        self._declared: IssueKind | None = None
        self._detail = ""
        self._started = time.monotonic()

    # ── Déclarations explicites du corps métier ────────────────────────────────
    def ok(self, detail: str = "") -> None:
        """La donnée a été obtenue."""
        self._declare(IssueKind.OK, detail)

    def absent(self, detail: str = "") -> None:
        """La source n'a pas cette donnée — JAMAIS un échec de communication.

        Attention : cette déclaration ne fait pas foi à elle seule. Si une
        tentative a échoué pendant l'observation, c'est cet échec qui gagne —
        « rien trouvé » derrière un 403 n'est pas une absence, c'est un blocage.
        """
        self._declare(IssueKind.ABSENT, detail)

    def parse_error(self, detail: str) -> None:
        """La source a répondu mais sa structure a changé (le scrape cassé)."""
        self._declare(IssueKind.PARSE, detail)

    def blocked(self, detail: str = "", *, expected: bool = False) -> None:
        """Anti-bot. `expected=True` sur une source dont le 403 est la norme."""
        if expected:
            self.note_attempt(IssueKind.BLOCKED, detail=detail, expected=True)
            return
        self._declare(IssueKind.BLOCKED, detail)

    def skipped(self, detail: str = "") -> None:
        """Pas appelée : hors dénominateur (compter 0/0 comme un échec ment)."""
        self._declare(IssueKind.SKIPPED, detail)

    def not_configured(self, detail: str = "") -> None:
        """Clé, token ou login absents : hors dénominateur."""
        self._declare(IssueKind.CONFIG, detail)

    def fail(self, kind: IssueKind, detail: str = "") -> None:
        self._declare(kind, detail)

    def attach(self, *, artist_id: int | None = None, track_id: int | None = None) -> None:
        """Précise la cible une fois connue (l'appelant l'ignore parfois à l'entrée)."""
        if artist_id is not None:
            self.artist_id = artist_id
        if track_id is not None:
            self.track_id = track_id

    def _declare(self, kind: IssueKind, detail: str) -> None:
        self._declared = kind
        if detail:
            self._detail = detail

    # ── Tentatives ─────────────────────────────────────────────────────────────
    def note_attempt(
        self,
        kind: IssueKind,
        *,
        detail: str = "",
        status_code: int | None = None,
        latency_ms: int | None = None,
        expected: bool = False,
    ) -> None:
        self.attempts.append(Attempt(kind, detail, status_code, latency_ms, expected))

    def note_status(self, code: int, *, headers: Mapping[str, str] | None = None) -> None:
        """Enregistre un code HTTP obtenu hors des capteurs transport."""
        self.note_attempt(
            classify_status(code, headers=headers, absent_statuses=self.absent_statuses),
            status_code=code,
        )

    # ── Verdict ────────────────────────────────────────────────────────────────
    def resolve(self, exc: BaseException | None = None) -> Verdict:
        """Le verdict unique de cette observation."""
        counted = [a for a in self.attempts if not a.expected]
        expected_blocked = sum(1 for a in self.attempts if a.expected)
        pire = worst(a.kind for a in counted)
        detail = self._detail
        status = next((a.status_code for a in reversed(counted) if a.status_code), None)

        if exc is not None:
            issue = classify(exc=exc, absent_statuses=self.absent_statuses)
            detail = detail or f"{type(exc).__name__}: {exc}"
        elif self._declared in (IssueKind.OK, IssueKind.SKIPPED, IssueKind.CONFIG):
            issue = self._declared
        elif self._declared == IssueKind.ABSENT:
            # LE point de l'exigence centrale : « rien trouvé » ne vaut absence
            # que si le transport n'a rien à redire. Sinon c'est lui qui parle.
            issue = IssueKind.ABSENT if pire in (None, IssueKind.OK, IssueKind.ABSENT) else pire
        elif self._declared is not None:
            issue = self._declared
        elif pire is None:
            # Aucun signal : un client qui avale ses erreurs, ou un capteur
            # manquant. On ne conclut PAS — le trou se signale de lui-même.
            issue = IssueKind.INDETERMINATE
        else:
            issue = pire

        return Verdict(
            source_key=self.source_key,
            issue=issue,
            flow=current_flow(),
            artist_id=self.artist_id if self.artist_id is not None else current_artist_id(),
            track_id=self.track_id,
            detail=detail[:300],
            status_code=status,
            latency_ms=int((time.monotonic() - self._started) * 1000),
            attempts=len(self.attempts),
            expected_blocked=expected_blocked,
        )


# ── État de process : scopes de run, piles par unité d'exécution, tampon ───────
_lock = threading.Lock()
_scopes: list[RunScope] = []
_carriers: dict[tuple[str, int], list[Observation]] = {}
_buffer: list[Verdict] = []
_sink: Callable[[list[Verdict]], None] | None = None


def set_sink(sink: Callable[[list[Verdict]], None] | None) -> None:
    """Branche le consommateur des verdicts (le repository). None = les jeter."""
    global _sink
    with _lock:
        _sink = sink


def _carrier() -> tuple[str, int]:
    """Unité d'exécution courante : la Task asyncio, sinon le thread.

    Pourquoi pas une `ContextVar` : posée dans un thread de worker, elle est
    PERDUE à la traversée de `async_loop.submit()` — `run_coroutine_threadsafe`
    crée la Task dans le thread de la boucle, qui copie le contexte de CE
    thread. Elle marcherait en test et perdrait les données en réel. Le carrier,
    lui, n'a rien à propager : il est recalculé à chaque enregistrement.
    """
    try:
        task = asyncio.current_task()
    except RuntimeError:  # hors boucle asyncio
        task = None
    return ("task", id(task)) if task is not None else ("thread", threading.get_ident())


# ── Scope de run (contexte ambiant : artiste + flux) ───────────────────────────
@contextmanager
def run_scope(flow: str, *, artist_id: int | None = None, artist_name: str = ""):
    """Déclare le batch en cours. Empilable ; vide le tampon en sortie.

    L'artiste n'est pas connu des scrapers (`Track` ne porte pas d'`artist_id`)
    et ne peut donc pas voyager par les appels. Il est ambiant, ce qui suppose
    un seul batch à la fois — vrai pour cette GUI mono-utilisateur, dont les
    boutons sont désactivés pendant un run.
    """
    scope = RunScope(str(flow), artist_id, artist_name)
    with _lock:
        _scopes.append(scope)
    try:
        yield scope
    finally:
        with _lock:
            if scope in _scopes:
                _scopes.remove(scope)
        flush()


def current_scope() -> RunScope | None:
    with _lock:
        return _scopes[-1] if _scopes else None


def current_flow() -> str:
    scope = current_scope()
    return scope.flow if scope else str(Flow.NONE)


def current_artist_id() -> int | None:
    scope = current_scope()
    return scope.artist_id if scope else None


# ── Le capteur ────────────────────────────────────────────────────────────────
@contextmanager
def observe(
    source_key: str,
    *,
    artist_id: int | None = None,
    track_id: int | None = None,
    label: str = "",
    absent_statuses: tuple[int, ...] = DEFAULT_ABSENT_STATUSES,
) -> Iterator[Observation]:
    """Encadre UN appel logique à une source et en tire un verdict unique.

    N'avale rien : une exception est classée puis ré-élevée, donc les
    `try/except` existants des appelants continuent de fonctionner à
    l'identique.

    RÉENTRANT par source : si une observation de la même source est déjà
    ouverte ici, on la réutilise au lieu d'en ouvrir une seconde. Sans quoi une
    méthode publique qui en appelle une autre (LRCLIB `get_synced` → `get_exact`
    puis `search`) produirait un verdict par étage, dont un vide. L'appel
    logique, c'est le plus EXTERNE.
    """
    deja_ouverte = _current_observation(source_key)
    if deja_ouverte is not None:
        deja_ouverte.attach(artist_id=artist_id, track_id=track_id)
        yield deja_ouverte
        return

    obs = Observation(
        source_key,
        artist_id=artist_id,
        track_id=track_id,
        label=label,
        absent_statuses=absent_statuses,
    )
    carrier = _carrier()
    with _lock:
        _carriers.setdefault(carrier, []).append(obs)
    try:
        yield obs
    except BaseException as exc:
        _emit(obs.resolve(exc))
        raise
    else:
        _emit(obs.resolve())
    finally:
        with _lock:
            stack = _carriers.get(carrier)
            if stack:
                if obs in stack:
                    stack.remove(obs)
                if not stack:
                    _carriers.pop(carrier, None)


def _current_observation(source_key: str | None) -> Observation | None:
    """L'observation OUVERTE la plus interne portant cette source.

    On n'attache jamais une tentative à une observation d'une autre source : un
    403 Spotify rencontré pendant un appel ReccoBeats ne doit pas être imputé à
    ReccoBeats. Sans correspondance, la tentative est comptée seule.
    """
    with _lock:
        stack = _carriers.get(_carrier())
        if not stack:
            return None
        if source_key is None:
            return stack[-1]
        for obs in reversed(stack):
            if obs.source_key == source_key:
                return obs
    return None


def record_attempt(
    source_key: str | None,
    kind: IssueKind,
    *,
    detail: str = "",
    status_code: int | None = None,
    latency_ms: int | None = None,
    expected: bool = False,
) -> None:
    """Enregistre un aller-retour réseau (capteurs transport)."""
    try:
        obs = _current_observation(source_key)
        if obs is not None:
            obs.note_attempt(
                kind,
                detail=detail,
                status_code=status_code,
                latency_ms=latency_ms,
                expected=expected,
            )
        elif source_key:
            # Hors de toute observation : on garde la trace au niveau source,
            # sans inventer de rattachement.
            _emit(
                Verdict(
                    source_key=source_key,
                    issue=kind,
                    flow=current_flow(),
                    artist_id=current_artist_id(),
                    detail=detail[:300],
                    status_code=status_code,
                    latency_ms=latency_ms,
                    attempts=1,
                    expected_blocked=1 if expected else 0,
                )
            )
    except Exception:  # noqa: BLE001 — un capteur ne casse jamais l'observé
        _logger().exception("source_usage: enregistrement de tentative impossible")


@contextmanager
def attempt(source_key: str, *, detail: str = ""):
    """Encadre UN aller-retour vers une bibliothèque tierce OPAQUE.

    `lyricsgenius`, `ytmusicapi` et `discogs_client` font leurs requêtes
    eux-mêmes : ni le shim `requests_get` ni `AsyncHttpSession` ne les voient.
    Sans ce capteur, l'observation qui les entoure n'aurait aucune tentative et
    conclurait `INDETERMINATE`. On enregistre donc le seul signal disponible :
    la sortie de l'appel. Le code HTTP reste hors de portée.
    """
    start = time.monotonic()
    try:
        yield
    except BaseException as exc:
        record_attempt(
            source_key,
            classify(exc=exc),
            detail=detail or f"{type(exc).__name__}: {exc}",
            latency_ms=int((time.monotonic() - start) * 1000),
        )
        raise
    record_attempt(
        source_key, IssueKind.OK, detail=detail, latency_ms=int((time.monotonic() - start) * 1000)
    )


def note_failure(source_key: str | None, exc: BaseException, *, detail: str = "") -> None:
    """Raccourci pour les `except` existants : classe l'exception en tentative.

    Se pose DANS le `except` d'un client sans en changer le flux — c'est ce qui
    rend observables les clients qui avalent leurs erreurs et renvoient None.
    """
    record_attempt(
        source_key,
        classify(exc=exc),
        detail=detail or f"{type(exc).__name__}: {exc}",
    )


def record_response(
    url: str,
    status_code: int,
    *,
    headers: Mapping[str, str] | None = None,
    latency_ms: int | None = None,
    source_key: str | None = None,
) -> None:
    """Tentative déduite d'une réponse HTTP (source devinée par le domaine)."""
    from urllib.parse import urlsplit

    key = source_key or key_for_domain(urlsplit(url).netloc)
    obs = _current_observation(key)
    absent = obs.absent_statuses if obs else DEFAULT_ABSENT_STATUSES
    record_attempt(
        key,
        classify_status(status_code, headers=headers, absent_statuses=absent),
        status_code=status_code,
        latency_ms=latency_ms,
    )


# ── Tampon et vidage ──────────────────────────────────────────────────────────
def _emit(verdict: Verdict) -> None:
    try:
        with _lock:
            _buffer.append(verdict)
            trop_plein = len(_buffer) >= FLUSH_EVERY
        if trop_plein:
            flush()
    except Exception:  # noqa: BLE001 — un capteur ne casse jamais l'observé
        _logger().exception("source_usage: verdict non enregistré")


def flush() -> list[Verdict]:
    """Remet les verdicts accumulés au consommateur. Renvoie ce qui a été vidé."""
    with _lock:
        if not _buffer:
            return []
        pending, _buffer[:] = list(_buffer), []
        sink = _sink
    if sink is None:
        return pending
    try:
        sink(pending)
    except Exception:  # noqa: BLE001 — la santé ne doit jamais faire tomber un batch
        _logger().exception("source_usage: vidage impossible (%d verdicts perdus)", len(pending))
    return pending


def reset() -> None:
    """Remise à zéro complète — réservée aux tests."""
    with _lock:
        _scopes.clear()
        _carriers.clear()
        _buffer.clear()


# ── Shim `requests` : rendre observables les clients qui avalent leurs erreurs ─
def requests_get(source_key: str, url: str, **kwargs):
    """`requests.get` instrumenté : même signature, même exception, même retour.

    Indispensable et non optionnel : plusieurs clients (`KworbScraper` en tête)
    font `except requests.RequestException: return None`, si bien que l'échec
    n'atteint jamais `observe()`. Sans ce shim, leur verdict serait
    `INDETERMINATE`.
    """
    import requests

    start = time.monotonic()
    try:
        resp = requests.get(url, **kwargs)
    except BaseException as exc:
        record_attempt(
            source_key,
            classify(exc=exc),
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=int((time.monotonic() - start) * 1000),
        )
        raise
    record_response(
        url,
        resp.status_code,
        headers=getattr(resp, "headers", None),
        latency_ms=int((time.monotonic() - start) * 1000),
        source_key=source_key,
    )
    return resp
