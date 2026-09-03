"""Taxonomie des verdicts d'appel à une source, et classifieur partagé.

Module PUR : aucune dépendance projet, aucun import de `requests`/`httpx`/
Playwright — il doit rester importable depuis n'importe quelle couche.

Le principe qui commande tout : **un échec de source est un échec de
COMMUNICATION, pas une absence de donnée**. Un morceau que la source ne
connaît pas (`ABSENT`) compte dans le dénominateur mais JAMAIS au numérateur —
sans quoi « 480 morceaux inconnus sur 500 » afficherait 96 % d'échec pour une
source parfaitement saine. Corollaire côté scrape : un processus qui casse
(sélecteur mort, browser mort) EST un échec de communication — la source ne
nous a pas transmis ce qu'elle contient.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

# Marqueurs de challenge Cloudflare cherchés dans le début d'un corps 403.
_CF_BODY_MARKERS = (
    "just a moment",
    "cf-browser-verification",
    "attention required",
    "checking your browser",
    "enable javascript and cookies",
)
_CF_HEADERS = ("cf-ray", "cf-mitigated")


class IssueKind(StrEnum):
    """Verdict d'UN appel logique à une source."""

    OK = "ok"  # communication OK, donnée obtenue
    ABSENT = "absent"  # communication OK, la source n'a pas la donnée
    SKIPPED = "skipped"  # pas appelée (gate / not_needed)
    CONFIG = "config"  # non configurée (clé, token ou login absents)
    INDETERMINATE = "indeterminate"  # aucun signal transport : on ne conclut PAS
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"  # DNS, connexion refusée, 5xx
    THROTTLED = "throttled"  # 429 : notre cadence, pas leur panne
    BLOCKED = "blocked"  # 403 anti-bot, Cloudflare, captcha
    AUTH = "auth"  # 401, token/login expiré
    PARSE = "parse"  # 200 mais structure inattendue — le scrape cassé
    CRASH = "crash"  # exception inattendue, browser mort — le process cassé
    NETWORK = "network"  # DÉRIVÉ à l'agrégation : panne locale, n'accuse personne


# ── Partition de sévérité (exhaustive — vérifiée par test) ─────────────────────
BENIGN = frozenset({IssueKind.OK, IssueKind.ABSENT})
IGNORED = frozenset(
    {IssueKind.SKIPPED, IssueKind.CONFIG, IssueKind.INDETERMINATE, IssueKind.NETWORK}
)
TRANSIENT = frozenset(
    {IssueKind.TIMEOUT, IssueKind.UNREACHABLE, IssueKind.THROTTLED, IssueKind.BLOCKED}
)
STRUCTURAL = frozenset({IssueKind.AUTH, IssueKind.PARSE, IssueKind.CRASH})

#: Verdicts comptés comme échec de communication (numérateur du taux d'échec).
FAILURES = TRANSIENT | STRUCTURAL
#: Verdicts comptés dans le dénominateur (un appel a bien eu lieu).
COUNTED = BENIGN | FAILURES

#: Du plus grave au plus bénin — arbitre le « pire » entre plusieurs tentatives.
SEVERITY_ORDER: tuple[IssueKind, ...] = (
    IssueKind.CRASH,
    IssueKind.PARSE,
    IssueKind.AUTH,
    IssueKind.BLOCKED,
    IssueKind.UNREACHABLE,
    IssueKind.TIMEOUT,
    IssueKind.THROTTLED,
    IssueKind.ABSENT,
    IssueKind.OK,
)
_SEVERITY_RANK = {kind: i for i, kind in enumerate(SEVERITY_ORDER)}

#: Codes qui signifient « la source n'a pas cette donnée », surchargeable par source.
DEFAULT_ABSENT_STATUSES: tuple[int, ...] = (204, 404, 410)


def worst(kinds) -> IssueKind | None:
    """Le verdict le plus grave d'un lot (None si le lot est vide)."""
    ranked = [_SEVERITY_RANK[k] for k in kinds if k in _SEVERITY_RANK]
    return SEVERITY_ORDER[min(ranked)] if ranked else None


def is_failure(kind: IssueKind) -> bool:
    """Ce verdict compte-t-il comme un échec de communication ?"""
    return kind in FAILURES


# ── Classification des exceptions, par NOM de module et de classe ──────────────
def _class_roots(exc: BaseException) -> set[str]:
    """Racines de module de toute la MRO de l'exception.

    On raisonne sur les NOMS, jamais sur `isinstance` : `patchright` expose des
    classes d'exception DISTINCTES de `playwright` (un `except playwright.Error`
    ne capture pas une erreur patchright), et surtout ce module doit rester
    importable sans que httpx ni Playwright soient installés.
    """
    return {(cls.__module__ or "").split(".")[0] for cls in type(exc).__mro__}


def _class_names(exc: BaseException) -> set[str]:
    return {cls.__name__ for cls in type(exc).__mro__}


def _from_playwright(names: set[str]) -> IssueKind:
    # Un timeout Playwright est ambigu (page qui ne vient pas VS sélecteur qui ne
    # matche plus) : on rend TIMEOUT, au scraper de préciser s'il sait lequel.
    return IssueKind.TIMEOUT if "TimeoutError" in names else IssueKind.CRASH


def _from_requests(names: set[str]) -> IssueKind:
    if "Timeout" in names:
        return IssueKind.TIMEOUT
    if "TooManyRedirects" in names:
        return IssueKind.PARSE
    # ConnectionError, SSLError, ou RequestException nue : transport, pas structure.
    return IssueKind.UNREACHABLE


def _from_httpx(exc: BaseException, names: set[str], absent: tuple[int, ...]) -> IssueKind:
    if "TimeoutException" in names:
        return IssueKind.TIMEOUT
    if "HTTPStatusError" in names:
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None)
        if code is not None:
            return classify_status(code, absent_statuses=absent)
        return IssueKind.UNREACHABLE
    if "TooManyRedirects" in names:
        return IssueKind.PARSE
    return IssueKind.UNREACHABLE  # ConnectError, TransportError…


def classify_exception(
    exc: BaseException, *, absent_statuses: tuple[int, ...] = DEFAULT_ABSENT_STATUSES
) -> IssueKind:
    """Verdict déduit d'une exception, quelle que soit sa bibliothèque d'origine."""
    roots = _class_roots(exc)
    names = _class_names(exc)

    if roots & {"playwright", "patchright"}:
        return _from_playwright(names)
    if "requests" in roots:
        return _from_requests(names)
    if "httpx" in roots:
        return _from_httpx(exc, names, absent_statuses)
    if "JSONDecodeError" in names:
        return IssueKind.PARSE
    if "TimeoutError" in names:  # asyncio/builtins — sous-classe d'OSError, testé AVANT
        return IssueKind.TIMEOUT
    if "OSError" in names:
        return IssueKind.UNREACHABLE
    return IssueKind.CRASH


# ── Classification d'une réponse HTTP ──────────────────────────────────────────
def _looks_cloudflare(headers: Mapping[str, str] | None, body_head: str) -> bool:
    if headers:
        lowered = {str(k).lower() for k in headers}
        if lowered & set(_CF_HEADERS):
            return True
    haystack = (body_head or "")[:2048].lower()
    return any(marker in haystack for marker in _CF_BODY_MARKERS)


def classify_status(
    status_code: int,
    *,
    headers: Mapping[str, str] | None = None,
    body_head: str = "",
    absent_statuses: tuple[int, ...] = DEFAULT_ABSENT_STATUSES,
) -> IssueKind:
    """Verdict déduit d'un code HTTP.

    `absent_statuses` est surchargeable PAR SOURCE : un 404 sur
    `api.deezer.com/track/<inconnu>` veut dire « pas au catalogue » (ABSENT),
    un 404 sur `api.genius.com/search` voudrait dire que l'API a bougé (PARSE).
    """
    # Testé AVANT la plage 2xx : un 204 est un succès de transport qui ne
    # rapporte aucune donnée, donc une absence, pas un OK.
    if status_code in absent_statuses:
        return IssueKind.ABSENT
    if 200 <= status_code < 300:
        return IssueKind.OK
    if status_code == 401:
        return IssueKind.AUTH
    if status_code == 403:
        # Un 403 anti-bot n'est pas un refus d'identifiants : les distinguer évite
        # d'envoyer l'utilisateur vérifier une clé API qui va très bien.
        return IssueKind.BLOCKED if _looks_cloudflare(headers, body_head) else IssueKind.AUTH
    if status_code == 429:
        return IssueKind.THROTTLED
    if status_code >= 500:
        return IssueKind.UNREACHABLE
    # Autre 4xx : la source répond correctement à une requête mal formée — c'est
    # NOTRE code qui a bougé, donc une rupture de structure.
    return IssueKind.PARSE


def classify(
    *,
    exc: BaseException | None = None,
    status_code: int | None = None,
    blocked: bool | None = None,
    headers: Mapping[str, str] | None = None,
    body_head: str = "",
    absent_statuses: tuple[int, ...] = DEFAULT_ABSENT_STATUSES,
) -> IssueKind:
    """Point d'entrée unique. Priorité : `blocked` explicite, puis exception, puis code."""
    if blocked:
        return IssueKind.BLOCKED
    if exc is not None:
        return classify_exception(exc, absent_statuses=absent_statuses)
    if status_code is not None:
        return classify_status(
            status_code, headers=headers, body_head=body_head, absent_statuses=absent_statuses
        )
    return IssueKind.INDETERMINATE
