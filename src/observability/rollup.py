"""Agrégation des compteurs vers les colonnes du panneau. Fonctions PURES.

Ce module ne renvoie JAMAIS de statut `ok`/`degraded`/`broken` : l'usage réel
n'écrase pas le verdict de la sonde active, il occupe des colonnes voisines.
C'est ce qui supprime tout risque de peindre en rouge une source dont les 403
anti-bot sont normaux — et ce qui rend ce module trivialement testable.

Aucune DB, aucun réseau, aucune horloge implicite (`now` est injectable).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.observability.issues import COUNTED, FAILURES, IssueKind
from src.observability.registry import FAMILY_LABELS, FAMILY_ORDER, Family

#: Une panne locale se reconnaît à sa SIMULTANÉITÉ : plusieurs sources sans
#: rapport qui tombent ensemble, ce n'est pas plusieurs sites en panne.
NETWORK_WINDOW = timedelta(minutes=5)
NETWORK_MIN_SOURCES = 3
#: Seuls ces verdicts peuvent être requalifiés en panne locale — un `parse` ou
#: un `auth` simultanés sur trois sources ne sont pas un problème de connexion.
NETWORK_KINDS = frozenset({IssueKind.UNREACHABLE, IssueKind.TIMEOUT})


@dataclass
class NetworkEpisode:
    """Une fenêtre où la connexion locale a lâché : personne n'est accusé."""

    start: datetime
    end: datetime
    sources: tuple[str, ...]
    failures: int


@dataclass
class SourceUsageView:
    """Ce qu'une ligne du panneau affiche pour une source."""

    key: str
    window_days: int | None = None
    calls: int = 0  # dénominateur : ok + absent + échecs
    ok: int = 0
    absent: int = 0
    comm_failures: int = 0  # numérateur : JAMAIS `absent`
    dominant_issue: str = ""
    blocked_expected: int = 0  # anti-bot contourné — visible, hors numérateur
    indeterminate: int = 0  # trous de capteur
    skipped: int = 0
    network_failures: int = 0  # requalifiés en panne locale
    attempts: int = 0
    last_used: str | None = None
    last_failure: str | None = None
    last_failure_msg: str = ""
    by_flow: dict[str, int] = field(default_factory=dict)

    @property
    def failure_rate(self) -> float:
        """Part des appels tombés sur un échec de COMMUNICATION (0.0 si aucun appel)."""
        return (self.comm_failures / self.calls) if self.calls else 0.0


@dataclass
class FamilyView:
    """La ligne d'agrégat d'une section du panneau."""

    family: Family
    label: str
    calls: int = 0
    comm_failures: int = 0
    indeterminate: int = 0
    sources: list[SourceUsageView] = field(default_factory=list)

    @property
    def failure_rate(self) -> float:
        return (self.comm_failures / self.calls) if self.calls else 0.0


# ── Utilitaires ────────────────────────────────────────────────────────────────
def _as_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _in_window(day: str, since_day: str | None) -> bool:
    return since_day is None or (day or "") >= since_day


def since_day_for(window_days: int | None, *, now: datetime | None = None) -> str | None:
    """Borne basse de la fenêtre, au format des clés `day` ('YYYY-MM-DD')."""
    if not window_days:
        return None
    reference = now or datetime.now()
    return (reference - timedelta(days=window_days - 1)).strftime("%Y-%m-%d")


# ── Épisodes de panne locale ───────────────────────────────────────────────────
def network_episodes(
    failure_rows: Iterable[Mapping],
    *,
    window: timedelta = NETWORK_WINDOW,
    min_sources: int = NETWORK_MIN_SOURCES,
) -> list[NetworkEpisode]:
    """Repère les fenêtres où plusieurs sources sont tombées ENSEMBLE.

    Un `ConnectError` est indiscernable, au site du capteur, entre un Wi-Fi
    coupé, un DNS mort et un serveur en panne : le classer là-bas serait
    deviner. C'est ici, en croisant les sources, que l'information apparaît.
    """
    events = []
    for row in failure_rows:
        kind = row.get("issue")
        moment = _as_datetime(row.get("occurred_at"))
        if moment is not None and kind in NETWORK_KINDS:
            events.append((moment, row.get("source_key") or ""))
    events.sort()

    episodes: list[NetworkEpisode] = []
    start = 0
    for end in range(len(events)):
        while events[end][0] - events[start][0] > window:
            start += 1
        tranche = events[start : end + 1]
        sources = {src for _, src in tranche}
        if len(sources) < min_sources:
            continue
        episode = NetworkEpisode(
            start=tranche[0][0],
            end=tranche[-1][0],
            sources=tuple(sorted(sources)),
            failures=len(tranche),
        )
        # Fusionne avec le précédent tant que la fenêtre glisse sur le même trou.
        if episodes and episode.start <= episodes[-1].end + window:
            previous = episodes[-1]
            episodes[-1] = NetworkEpisode(
                start=previous.start,
                end=max(previous.end, episode.end),
                sources=tuple(sorted(set(previous.sources) | sources)),
                failures=max(previous.failures, episode.failures),
            )
        else:
            episodes.append(episode)
    return episodes


def _network_failures_by_source(
    failure_rows: Iterable[Mapping], episodes: Sequence[NetworkEpisode]
) -> Counter:
    """Combien d'échecs de chaque source tombent dans une panne locale."""
    counts: Counter = Counter()
    if not episodes:
        return counts
    for row in failure_rows:
        moment = _as_datetime(row.get("occurred_at"))
        if moment is None or row.get("issue") not in NETWORK_KINDS:
            continue
        if any(ep.start <= moment <= ep.end for ep in episodes):
            counts[row.get("source_key") or ""] += 1
    return counts


# ── Synthèse par source ────────────────────────────────────────────────────────
def summarize(
    daily_rows: Iterable[Mapping],
    failure_rows: Iterable[Mapping] = (),
    *,
    source_key: str | None = None,
    window_days: int | None = 7,
    now: datetime | None = None,
) -> dict[str, SourceUsageView]:
    """Agrège les compteurs quotidiens en une vue par source.

    `daily_rows` : lignes de `source_usage_daily`. `failure_rows` : lignes de
    `source_usage_failures`, utilisées pour la dernière erreur et pour repérer
    les pannes locales.
    """
    rows = list(daily_rows)
    failures = list(failure_rows)
    since = since_day_for(window_days, now=now)
    episodes = network_episodes(failures)
    network_counts = _network_failures_by_source(failures, episodes)

    views: dict[str, SourceUsageView] = {}
    dominant: dict[str, Counter] = {}

    for row in rows:
        key = row.get("source_key") or ""
        if source_key and key != source_key:
            continue
        if not _in_window(row.get("day") or "", since):
            continue

        view = views.setdefault(key, SourceUsageView(key=key, window_days=window_days))
        dominant.setdefault(key, Counter())
        issue = row.get("issue")
        n = int(row.get("n_calls") or 0)
        view.attempts += int(row.get("n_attempts") or 0)
        view.blocked_expected += int(row.get("expected_blocked") or 0)

        if issue in COUNTED:
            view.calls += n
            flow = row.get("flow") or ""
            view.by_flow[flow] = view.by_flow.get(flow, 0) + n
        if issue == IssueKind.OK:
            view.ok += n
        elif issue == IssueKind.ABSENT:
            view.absent += n
        elif issue == IssueKind.INDETERMINATE:
            view.indeterminate += n
        elif issue == IssueKind.SKIPPED:
            view.skipped += n
        if issue in FAILURES:
            view.comm_failures += n
            dominant[key][str(issue)] += n

        seen = row.get("last_seen")
        moment = _as_datetime(seen)
        current = _as_datetime(view.last_used)
        if moment is not None and (current is None or moment > current):
            view.last_used = moment.isoformat(timespec="seconds")

    for key, view in views.items():
        counts = dominant.get(key)
        view.dominant_issue = counts.most_common(1)[0][0] if counts else ""
        # Une panne locale n'accuse personne : ses échecs sortent du numérateur.
        view.network_failures = min(network_counts.get(key, 0), view.comm_failures)
        view.comm_failures -= view.network_failures

    _fill_last_failure(views, failures)
    return views


def _fill_last_failure(views: Mapping[str, SourceUsageView], failure_rows: Sequence[Mapping]):
    for row in failure_rows:
        view = views.get(row.get("source_key") or "")
        if view is None:
            continue
        moment = _as_datetime(row.get("occurred_at"))
        current = _as_datetime(view.last_failure)
        if moment is not None and (current is None or moment > current):
            view.last_failure = moment.isoformat(timespec="seconds")
            view.last_failure_msg = f"{row.get('issue') or ''} — {row.get('message') or ''}".strip(
                " —"
            )


# ── Synthèse par famille (les sections du panneau) ─────────────────────────────
def summarize_by_family(
    views: Mapping[str, SourceUsageView],
    families_by_key: Mapping[str, Sequence[Family]],
) -> list[FamilyView]:
    """Répartit les vues par section, dans l'ordre d'affichage.

    Une source servant deux familles apparaît dans les deux : c'est voulu, et
    c'est la ventilation par flux (`by_flow`) qui empêche d'y lire deux fois le
    même chiffre.
    """
    sections = [FamilyView(family=f, label=FAMILY_LABELS[f]) for f in FAMILY_ORDER]
    by_family = {section.family: section for section in sections}

    for key, view in sorted(views.items()):
        for family in families_by_key.get(key, ()):
            section = by_family.get(family)
            if section is None:
                continue
            section.sources.append(view)
            section.calls += view.calls
            section.comm_failures += view.comm_failures
            section.indeterminate += view.indeterminate
    return sections
