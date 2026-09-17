"""Générateur « Timeline » : la carrière d'un artiste en carrousel de frises.

Quatre pages (trois pour un petit artiste) de quatre **points** chacune — un
projet par point : les albums de l'artiste (y compris les albums communs et une
**réédition** notable, posée comme un point à part), les feats les plus
streamés, les freestyles marquants (Grünt, Colors, Planète Rap…). Chaque page
porte un cartouche « N M de streams cumulés estimés\\* » = le cumul de TOUT ce
que l'artiste a sorti jusqu'au 4ᵉ point de la page, et une courbe de ce cumul
qui traverse les pages.

Ce qui vient des données : les candidats (proposés par heuristique), les dates,
les streams estimés (`streams_calculator.calculate_total_streams`, la même
estimation que la fiche morceau), les pochettes, les certifications. Ce qui ne
peut PAS en venir — lesquels des ~30 candidats font les 16 points, et les
libellés éditoriaux (« Passage chez **Colors Studios** ») — vit dans
`data/timeline_overrides.json` (`timeline_overrides_io`), prérempli.

Deux pièges mesurés sur Isha et qui façonnent l'heuristique (2026-09-15) :
- un album a PLUSIEURS dates de sortie (singles extraits AVANT l'album ; « La
  vie augmente Vol.1 » s'étale sur 8 mois) : sa date est la date MODALE (celle
  qui porte le plus de morceaux), et une réédition est un lot POSTÉRIEUR à la
  modale de plus de `REEDITION_MIN_DAYS` ;
- un album COMMUN (« Bitume Caviar (vol.2) ») a tous ses titres en
  `is_featuring` : un album candidat = au moins `ALBUM_MIN_TRACKS` morceaux de
  l'artiste, feat ou non.

Module pur, GUI-indépendant et **déterministe** (tri total, aucun aléa) : deux
générations produisent un SVG byte-identique. Pilotable via `scripts/timeline.py`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from src.dataviz.bubble_prod import default_output_path, list_albums, select_album_tracks
from src.dataviz.timeline_json import build_payload, write_timeline_json
from src.dataviz.timeline_svg import (
    SIZES,
    CertSpec,
    PageSpec,
    PointSpec,
    TimelineSpec,
    TimelineStyle,
    background_point,
    format_streams_short,
    parse_marked_lines,
    slot_x,
    write_timeline_svg,
)
from src.enrichment.album_types import libelle_record_type
from src.utils.cert_normalize import ORGANISMES, RANG_PALIERS, decouper_multiplicateur
from src.utils.dates import parse_flexible
from src.utils.logger import get_logger
from src.utils.streams_calculator import calculate_total_streams
from src.utils.title_matching import clean_display_title, normalize_title, split_title_paren

logger = get_logger(__name__)

__all__ = [
    "ALBUM_MIN_TRACKS",
    "PAGE_SIZE",
    "Candidate",
    "EntryChoice",
    "TimelineResult",
    "build_candidates",
    "build_timeline_spec",
    "cumulative_streams",
    "default_page_count",
    "default_selection",
    "generate_timeline",
    "generate_timeline_preview",
    "record_types_par_titre",
    "sort_candidates",
]

PAGE_SIZE = 4
PAGES_FULL = 4
PAGES_SMALL = 3
ALBUM_MIN_TRACKS = 4
REEDITION_MIN_DAYS = 180

DISC_SIDES: tuple[str, ...] = ("auto", "gauche", "droite")
_DISC_SIDE_SPEC = {"gauche": "left", "droite": "right"}

KIND_ALBUM = "album"
KIND_REEDITION = "reedition"
KIND_TRACK = "track"
_KIND_RANK = {KIND_ALBUM: 0, KIND_REEDITION: 1, KIND_TRACK: 2}

# Émissions / formats de freestyle, cherchés dans le titre, l'album ET l'artiste
# principal (« Grünt » est un album ET un primary, « Booska Pogo » un titre sans
# album). Comparés sous `normalize_title` : accents et casse neutralisés.
FREESTYLE_KEYWORDS: tuple[str, ...] = (
    "grünt",
    "colors show",
    "colors studios",
    "planète rap",
    "booska",
    "rentre dans le cercle",
    "oklm",
    "skyrock",
    "iron mic",
    "jukebox",
    "freestyle",  # générique, en DERNIER : « Freestyle Skyrock » doit nommer Skyrock
)
_FREESTYLE_NORM = tuple((kw, normalize_title(kw)) for kw in FREESTYLE_KEYWORDS)

# Préfixes en toutes lettres des paliers multiples (« Double Platine »), que
# `decouper_multiplicateur` ne découpe pas (il ne connaît que « Nx »).
_PREFIXES_MULTI = (("quadruple ", 4), ("triple ", 3), ("double ", 2))
_ORGANISME_ORDER = {name: i for i, name in enumerate(ORGANISMES)}


@dataclass(frozen=True)
class Candidate:
    """Un projet proposable : sa clé stable, sa date, ses libellés par défaut."""

    key: str  # « album:<norm> » | « reedition:<norm> » | « track:<id> »
    kind: str
    date: date
    title: str
    line1_default: str
    line2_default: str
    size_default: str
    streams: int
    is_freestyle: bool
    is_feat: bool
    cover: str | None  # chemin relatif à IMAGES_DIR, ou None
    track_ids: tuple[int, ...]
    has_cert: bool = False  # au moins une certif (album → d'album, morceau → la sienne)
    disabled: bool = False  # morceau DÉSACTIVÉ : proposable, mais hors cumul


@dataclass(frozen=True)
class EntryChoice:
    """Une ligne retenue par l'utilisateur (ou par défaut) : clé + libellés.

    `disc_side` = côté où dépassent les disques de certification : « auto »
    (vers l'intérieur de la page), « gauche » ou « droite » — un choix à l'œil,
    qui dépend de l'air autour (décision utilisateur 2026-09-15).
    """

    key: str
    line1: str
    line2: str
    size: str
    disc_side: str = "auto"
    # Page (1-4) dont CE projet fournit la pochette de fond, 0 = automatique
    # (le projet de l'artiste le plus streamé de la page). Un projet peut servir
    # de fond à une page qui n'est pas la sienne.
    background: int = 0


@dataclass(frozen=True)
class TimelineResult:
    """Retour de `generate_timeline` : le spec rendu, les chemins et le bilan.

    `undated_count` = morceaux SANS date, exclus des candidats ET du cumul (le
    cumul est donc sous-estimé d'autant — à dire). `unstreamed_count` = morceaux
    datés sans aucun stream (comptent 0). `missing_covers` = points sans pochette.
    """

    spec: TimelineSpec
    path: Path
    json_path: Path
    page_count: int
    entry_count: int
    total_cumul: int
    undated_count: int
    unstreamed_count: int
    missing_covers: tuple[str, ...]


# ── Briques pures ────────────────────────────────────────────────────────────


def track_date(track) -> date | None:
    """Date de sortie en `date`, ou None (la colonne est une chaîne en pratique)."""
    parsed = parse_flexible(track.release_date)
    return parsed.date() if parsed is not None else None


def track_streams(track) -> int:
    """Streams estimés du morceau (règle unique de `streams_calculator`), 0 si aucun."""
    return calculate_total_streams(track.streams.spotify_streams, track.streams.ytm_streams) or 0


def _dated(tracks) -> list[tuple[date, object]]:
    out = [(d, t) for t in tracks if (d := track_date(t)) is not None]
    return sorted(out, key=lambda pair: (pair[0], _track_sort_key(pair[1])))


def _track_sort_key(track) -> tuple:
    return (track.track_number is None, track.track_number or 0, normalize_title(track.title or ""))


def album_batches(album_tracks) -> list[tuple[date, list]]:
    """Les morceaux de l'album groupés par date de sortie, dates croissantes."""
    groups: dict[date, list] = {}
    for d, t in _dated(album_tracks):
        groups.setdefault(d, []).append(t)
    return sorted(groups.items())


def album_date(album_tracks) -> date | None:
    """Date MODALE de l'album : celle qui porte le plus de morceaux (départage : la
    plus ancienne). Les singles extraits avant l'album n'avancent donc pas sa date."""
    batches = album_batches(album_tracks)
    if not batches:
        return None
    best = max(len(tracks) for _, tracks in batches)
    return next(d for d, tracks in batches if len(tracks) == best)


def detect_reedition(album_tracks) -> tuple[date, list] | None:
    """Le lot de morceaux sortis > `REEDITION_MIN_DAYS` APRÈS la date modale.

    Plusieurs lots tardifs → le plus tardif est retenu, les autres signalés.
    """
    modal = album_date(album_tracks)
    if modal is None:
        return None
    late = [
        (d, tracks)
        for d, tracks in album_batches(album_tracks)
        if (d - modal).days > REEDITION_MIN_DAYS
    ]
    if not late:
        return None
    if len(late) > 1:
        logger.info(
            f"Timeline : {len(late)} lots tardifs pour « {album_tracks[0].album} », "
            f"seul le dernier ({late[-1][0]}) est proposé en réédition"
        )
    return late[-1]


def is_freestyle(track) -> tuple[bool, str | None]:
    """(freestyle ?, nom de l'émission détectée) d'après titre, album et primary."""
    for is_title, field_value in (
        (False, track.album),
        (False, track.primary_artist_name),
        (True, track.title),
    ):
        norm = normalize_title(field_value or "")
        if not norm:
            continue
        for keyword, kw_norm in _FREESTYLE_NORM:
            if kw_norm and kw_norm in norm:
                # L'émission = l'album ou le primary qui l'a révélée ; trouvée
                # dans le seul titre, on ne connaît que le mot-clé.
                if is_title:
                    emission = None if keyword == "freestyle" else keyword.title()
                else:
                    emission = clean_display_title(field_value)
                return True, emission
    return False, None


def resolve_cover(rel: str | None) -> Path | None:
    """Chemin absolu d'une pochette (`cover_path` est relatif à IMAGES_DIR), si le
    fichier existe. Monkeypatché par les tests (jamais `data/` réel)."""
    if not rel:
        return None
    from src.config import IMAGES_DIR

    candidate = Path(rel)
    if not candidate.is_absolute():
        candidate = Path(IMAGES_DIR) / rel
    return candidate if candidate.is_file() else None


def _cover_of(tracks, at: date | None = None) -> str | None:
    """1re pochette EXISTANTE par ordre de tracklist, en préférant les morceaux
    sortis LE jour `at` : les pochettes sont par morceau, et un single extrait
    avant l'album porte la sienne (mesuré : « Durag » en piste 1 de « La vie
    augmente Vol.3 »), pas celle de l'album."""
    ordered = sorted(tracks, key=_track_sort_key)
    preferred = [t for t in ordered if at is not None and track_date(t) == at]
    for t in preferred + [t for t in ordered if t not in preferred]:
        rel = t.media.cover_path
        if rel and resolve_cover(rel) is not None:
            return rel
    return None


def disc_count(level: str) -> tuple[int, str]:
    """« Double Platine » → (2, « platine ») ; « 3x Platine » → (3, « platine »)."""
    mult, palier = decouper_multiplicateur(level)
    for prefix, n in _PREFIXES_MULTI:
        if palier.startswith(prefix):
            return mult * n, palier[len(prefix) :]
    return mult, palier


def best_cert(entries) -> CertSpec | None:
    """La certification la plus haute d'une liste de dicts (`cert_matcher._format`).

    Classement : palier (`RANG_PALIERS`, 1 = le plus haut) puis multiplicateur,
    puis ordre des organismes (SNEP en tête). Palier inconnu → ignoré.
    """
    best: tuple | None = None
    for entry in entries or ():
        level = str(entry.get("certification") or "")
        mult, palier = disc_count(level)
        rank = RANG_PALIERS.get(palier)
        if rank is None:
            logger.warning(f"Timeline : palier inconnu ignoré — « {level} »")
            continue
        body = str(entry.get("body") or "")
        org = _ORGANISME_ORDER.get(body.split()[0] if body else "", len(_ORGANISME_ORDER))
        key = (rank, -mult, org)
        if best is None or key < best[0]:
            best = (
                key,
                CertSpec(
                    body=body,
                    palier=palier,
                    multiplier=mult,
                    level=level,
                    category=str(entry.get("category") or ""),
                ),
            )
    return best[1] if best else None


def _common_primary(tracks) -> str | None:
    """Le `primary_artist_name` partagé par TOUS les morceaux (album commun)."""
    names = {(t.primary_artist_name or "").strip() for t in tracks}
    if len(names) == 1 and all(t.is_featuring for t in tracks):
        name = next(iter(names))
        return name or None
    return None


def _album_key(album: str) -> str:
    return f"{KIND_ALBUM}:{normalize_title(album)}"


def _reedition_key(album: str) -> str:
    return f"{KIND_REEDITION}:{normalize_title(album)}"


def _track_key(track) -> str:
    return f"{KIND_TRACK}:{track.id}"


def compte_pour_la_timeline(track) -> bool:
    """Seuls les morceaux PRINCIPAUX et les FEATS comptent : une apparition
    secondaire (chœurs, voix additionnelle — `secondary_role`) n'est pas un
    point de carrière et ses streams ne sont pas ceux de l'artiste (décision
    utilisateur 2026-09-17 : un Green Montana proposé à Isha pour une voix
    additionnelle). Exclue des candidats, des albums ET du cumul."""
    return not (track.secondary_role or "").strip()


def _track_line1(track) -> str:
    freestyle, emission = is_freestyle(track)
    if freestyle:
        return f"Freestyle **{emission}**" if emission else "Freestyle"
    primary = (track.primary_artist_name or "").strip()
    if track.is_featuring and primary:
        return f"Feat avec **{primary}**"
    return "Single"


def _display_title(title: str) -> str:
    base, _paren = split_title_paren(clean_display_title(title or ""))
    return base or clean_display_title(title or "")


def record_types_par_titre(albums) -> dict[str, str]:
    """`{titre normalisé: record_type}` depuis les lignes de la table `albums`
    (`get_albums_for_artist`) — la clé est celle de `_album_key`, donc les
    graphies Genius/Kworb/Deezer d'un même album se rejoignent."""
    return {
        normalize_title(a["title"]): a["record_type"]
        for a in albums or ()
        if a.get("title") and a.get("record_type")
    }


def build_candidates(
    tracks,
    artist_name: str = "",
    disabled: frozenset[int] | set[int] = frozenset(),
    record_types: Mapping[str, str] | None = None,
) -> list[Candidate]:
    """TOUS les candidats, dans l'ordre « proposés » : projets (albums puis
    rééditions, par date), puis les morceaux par streams décroissants, puis les
    FREESTYLES à part (par streams aussi) — trois blocs, que la GUI sépare d'un
    trait. Aucun plafond (un « top 30 » a été essayé et retiré : dès qu'il y a
    plus de matière, la limite n'a pas de sens et fait disparaître des projets).

    `tracks` = TOUS les morceaux, désactivés compris : un freestyle Grünt est
    désactivé parce qu'il ne compte pas comme MORCEAU (ses streams sortent du
    cumul), mais c'est un point de carrière proposable (décision utilisateur
    2026-09-15). `disabled` = leurs ids, pour le marquer.
    """
    projects: list[Candidate] = []
    consumed: set[int] = set()
    tracks = [t for t in tracks if compte_pour_la_timeline(t)]

    for album in list_albums(tracks):
        album_tracks = [t for t in select_album_tracks(tracks, album) if track_date(t)]
        if len(album_tracks) < ALBUM_MIN_TRACKS:
            continue
        modal = album_date(album_tracks)
        if modal is None:
            continue
        title = clean_display_title(album)  # la parenthèse d'un album (« (vol.1) ») RESTE
        common = _common_primary(album_tracks)
        # « EP » / « Album » / … = ce que le distributeur déclare (`albums.record_type`,
        # Deezer ou saisie) ; sans donnée, « Album ». Un `line1` mémorisé prime.
        nature = libelle_record_type((record_types or {}).get(normalize_title(album)))
        line1 = f"{nature} avec **{common}**" if common else nature
        ids = tuple(sorted(t.id for t in album_tracks if t.id is not None))
        projects.append(
            Candidate(
                key=_album_key(album),
                kind=KIND_ALBUM,
                date=modal,
                title=title,
                line1_default=line1,
                line2_default=f"**{title}**",
                size_default="grand",
                streams=sum(track_streams(t) for t in album_tracks),
                is_freestyle=False,
                is_feat=common is not None,
                cover=_cover_of(album_tracks, modal),
                track_ids=ids,
                has_cert=any(t.certs.album_entries for t in album_tracks),
            )
        )
        # Les morceaux À la date modale ou après (hors réédition) appartiennent
        # à l'album ; ceux d'AVANT sont des singles extraits, proposés à part.
        reedition = detect_reedition(album_tracks)
        bonus = reedition[1] if reedition else []
        for t in album_tracks:
            d = track_date(t)
            if d is not None and d >= modal and t not in bonus:
                consumed.add(id(t))
        if reedition:
            r_date, r_tracks = reedition
            consumed.update(id(t) for t in r_tracks)
            projects.append(
                Candidate(
                    key=_reedition_key(album),
                    kind=KIND_REEDITION,
                    date=r_date,
                    title=title,
                    line1_default="Réédition de",
                    line2_default=f"**{title}**",
                    size_default="grand",
                    streams=sum(track_streams(t) for t in r_tracks),
                    is_freestyle=False,
                    is_feat=common is not None,
                    cover=_cover_of(r_tracks) or _cover_of(album_tracks, modal),
                    track_ids=tuple(sorted(t.id for t in r_tracks if t.id is not None)),
                    has_cert=any(t.certs.album_entries for t in album_tracks),
                )
            )

    projects.sort(key=lambda c: (c.date, _KIND_RANK[c.kind], c.key))

    others: list[Candidate] = []
    for t in tracks:
        if id(t) in consumed or t.id is None:
            continue
        d = track_date(t)
        if d is None:
            continue
        freestyle, _ = is_freestyle(t)
        title = _display_title(t.title)
        others.append(
            Candidate(
                key=_track_key(t),
                kind=KIND_TRACK,
                date=d,
                title=title,
                line1_default=_track_line1(t),
                line2_default=f"**{title}**",
                size_default="petit",
                streams=track_streams(t),
                is_freestyle=freestyle,
                is_feat=bool(t.is_featuring),
                cover=t.media.cover_path if resolve_cover(t.media.cover_path) else None,
                track_ids=(t.id,),
                has_cert=bool(t.certs.entries),
                disabled=t.id in disabled,
            )
        )
    others.sort(key=lambda c: (c.is_freestyle, -c.streams, c.date, c.key))
    return projects + others


def sort_candidates(candidates: list[Candidate], by_date: bool) -> list[Candidate]:
    """Ordre d'affichage : « proposés » (tel quel) ou chronologique."""
    if not by_date:
        return list(candidates)
    return sorted(candidates, key=lambda c: (c.date, _KIND_RANK[c.kind], c.key))


def default_selection(candidates: list[Candidate], count: int) -> list[str]:
    """Clés cochées par défaut : tous les projets, puis les morceaux les mieux
    streamés, tronqué à `count` — rendues par DATE.

    Les freestyles ne sont PAS forcés ici (mesuré sur Isha : ils auraient évincé
    « Dans mon élément » et « Clope sur la lune », 40 M chacun, au profit d'un
    Planète Rap à 3 M) — ils sont listés à part, où l'utilisateur les voit et
    les coche s'ils comptent.
    """
    projects = sorted(
        (c for c in candidates if c.kind != KIND_TRACK), key=lambda c: (-c.streams, c.key)
    )
    # Un désactivé n'est jamais coché d'office : il ne compte pas comme morceau.
    others = sorted(
        (c for c in candidates if c.kind == KIND_TRACK and not c.disabled),
        key=lambda c: (-c.streams, c.date, c.key),
    )
    picked = projects[:count] + others[: max(0, count - len(projects))]
    picked.sort(key=lambda c: (c.date, _KIND_RANK[c.kind], c.key))
    return [c.key for c in picked]


def default_page_count(candidates: list[Candidate]) -> int:
    """4 pages s'il y a de quoi les remplir, 3 sinon."""
    return PAGES_FULL if len(candidates) >= PAGES_FULL * PAGE_SIZE else PAGES_SMALL


def cumulative_streams(tracks, at: date, disabled: frozenset[int] | set[int] = frozenset()) -> int:
    """Streams estimés des morceaux DATÉS et ACTIFS sortis au plus tard le `at`."""
    return sum(
        track_streams(t)
        for t in tracks
        if compte_pour_la_timeline(t)
        and t.id not in disabled
        and (d := track_date(t)) is not None
        and d <= at
    )


def _entries_from_defaults(candidates: list[Candidate], keys: list[str]) -> list[EntryChoice]:
    by_key = {c.key: c for c in candidates}
    return [
        EntryChoice(k, by_key[k].line1_default, by_key[k].line2_default, by_key[k].size_default)
        for k in keys
        if k in by_key
    ]


def _cert_for(candidate: Candidate, tracks_by_id: dict) -> CertSpec | None:
    """Album / réédition → certifs d'ALBUM des morceaux ; morceau → les siennes."""
    entries: list[dict] = []
    for tid in candidate.track_ids:
        t = tracks_by_id.get(tid)
        if t is None:
            continue
        entries.extend(t.certs.album_entries if candidate.kind != KIND_TRACK else t.certs.entries)
    return best_cert(entries)


def build_timeline_spec(
    tracks,
    entries: list[EntryChoice],
    style: TimelineStyle | None = None,
    *,
    candidates: list[Candidate] | None = None,
    disabled: frozenset[int] | set[int] = frozenset(),
) -> TimelineSpec:
    """Résout les entrées, pagine par 4, calcule cumuls, courbe et années.

    `ValueError` si une clé ne correspond à aucun candidat (nommée), si le nombre
    d'entrées n'est pas un multiple de `PAGE_SIZE` entre 12 et 16, si une taille
    est inconnue, ou si le cumul final est nul (rien à tracer).
    """
    style = style or TimelineStyle()
    candidates = (
        candidates if candidates is not None else build_candidates(tracks, disabled=disabled)
    )
    by_key = {c.key: c for c in candidates}

    unknown = [e.key for e in entries if e.key not in by_key]
    if unknown:
        raise ValueError(
            "Entrées introuvables parmi les candidats (album renommé, morceau supprimé ?) : "
            + ", ".join(unknown)
        )
    bad_size = [e.key for e in entries if e.size not in SIZES]
    if bad_size:
        raise ValueError("Taille inconnue (attendu grand/petit) : " + ", ".join(bad_size))
    bad_side = [e.key for e in entries if e.disc_side not in DISC_SIDES]
    if bad_side:
        raise ValueError("Côté de disque inconnu (auto/gauche/droite) : " + ", ".join(bad_side))
    forced_bg: dict[int, str] = {}
    for e in entries:
        if not e.background:
            continue
        if not 1 <= e.background <= PAGES_FULL:
            raise ValueError(f"Fond de page hors de 1-{PAGES_FULL} : {e.key}")
        if e.background in forced_bg:
            raise ValueError(
                f"Deux projets pour le fond de la page {e.background} : "
                f"{forced_bg[e.background]} et {e.key}"
            )
        forced_bg[e.background] = e.key
    allowed = {PAGES_SMALL * PAGE_SIZE, PAGES_FULL * PAGE_SIZE}
    if len(entries) not in allowed:
        raise ValueError(
            f"{len(entries)} entrée(s) : il en faut {PAGES_SMALL * PAGE_SIZE} (3 pages) "
            f"ou {PAGES_FULL * PAGE_SIZE} (4 pages)"
        )

    ordered = sorted(
        entries, key=lambda e: (by_key[e.key].date, _KIND_RANK[by_key[e.key].kind], e.key)
    )
    tracks_by_id = {t.id: t for t in tracks if t.id is not None}
    cumuls = [cumulative_streams(tracks, by_key[e.key].date, disabled) for e in ordered]
    total = cumuls[-1]
    if total <= 0:
        raise ValueError("Aucun stream estimé sur les morceaux datés : rien à tracer")

    points: list[PointSpec] = []
    seen_years: set[int] = set()
    for index, (entry, cumul) in enumerate(zip(ordered, cumuls, strict=True)):
        cand = by_key[entry.key]
        slot = index % PAGE_SIZE
        year = cand.date.year
        year_label = None
        if year not in seen_years:
            seen_years.add(year)
            year_label = str(year)
        cover_abs = resolve_cover(cand.cover)
        points.append(
            PointSpec(
                key=entry.key,
                kind=cand.kind,
                date=cand.date.isoformat(),
                year_label=year_label,
                line1=parse_marked_lines(entry.line1),
                line2=parse_marked_lines(entry.line2),
                size=entry.size,
                above=index % 2 == 0,
                cover=cand.cover if cover_abs else None,
                cover_abs=str(cover_abs.resolve()) if cover_abs else None,
                cert=_cert_for(cand, tracks_by_id),
                disc_side=_DISC_SIDE_SPEC.get(
                    entry.disc_side, "right" if slot < PAGE_SIZE / 2 else "left"
                ),
                streams=cand.streams,
                cumul=cumul,
                ratio=cumul / total,
                x=slot_x(slot, style, PAGE_SIZE),
            )
        )

    pages: list[PageSpec] = []
    page_count = len(points) // PAGE_SIZE
    lag = _curve_lag(style)
    # Sommets de TOUTES les pages en abscisse globale (page p décalée de
    # p × largeur) : les tangentes lissées se calculent sur la séquence entière,
    # sinon la jonction entre deux pages ferait un angle.
    raw_curves: list[list[tuple[float, float]]] = []
    for p in range(page_count):
        chunk = points[p * PAGE_SIZE : (p + 1) * PAGE_SIZE]
        prev_last = points[p * PAGE_SIZE - 1] if p else None
        next_first = points[(p + 1) * PAGE_SIZE] if p + 1 < page_count else None
        entry_ratio = _boundary_ratio(prev_last, chunk[0], style, lag) if prev_last else 0.0
        last_page = next_first is None
        # Dernière page : le dernier sommet est porté au bord droit du cadre.
        exit_ratio = 1.0 if last_page else _boundary_ratio(chunk[-1], next_first, style, lag)
        inner = chunk[:-1] if last_page else chunk
        raw_curves.append(
            [
                (0.0, entry_ratio),
                *((pt.x + lag, pt.ratio) for pt in inner),
                (style.page_width, exit_ratio),
            ]
        )
    slopes = _page_slopes(raw_curves, style.page_width)

    for p in range(page_count):
        chunk = tuple(points[p * PAGE_SIZE : (p + 1) * PAGE_SIZE])
        prev_last = points[p * PAGE_SIZE - 1] if p else None
        curve = tuple((x, r, m) for (x, r), m in zip(raw_curves[p], slopes[p], strict=True))
        entry_ratio, exit_ratio = curve[0][1], curve[-1][1]
        background = background_point(chunk)
        # Fond FORCÉ par l'utilisateur : la pochette de ce projet, s'il en a une.
        forced_key = forced_bg.get(p + 1)
        if forced_key:
            forced = next(pt for pt in points if pt.key == forced_key)
            if forced.cover_abs:
                background = forced
            else:
                logger.warning(
                    f"Timeline : fond de la page {p + 1} demandé sur {forced_key}, "
                    "qui n'a pas de pochette — règle automatique appliquée"
                )
        pages.append(
            PageSpec(
                index=p + 1,
                points=chunk,
                background_key=background.key if background else None,
                background_abs=background.cover_abs if background else None,
                entry_cumul=prev_last.cumul if prev_last else 0,
                entry_ratio=entry_ratio,
                exit_ratio=exit_ratio,
                curve=curve,
                cartouche_value=chunk[-1].cumul,
                cartouche_text=format_streams_short(chunk[-1].cumul),
            )
        )
    return TimelineSpec(pages=tuple(pages), total_cumul=total, style=style)


def _monotone_slopes(xs: list[float], rs: list[float]) -> list[float]:
    """Tangentes de Fritsch–Carlson : une spline cubique qui ne redescend jamais
    entre deux sommets d'une suite croissante (une Bézier « naïve » creuserait
    des vallées entre deux paliers). Pente en ratio / px."""
    n = len(xs)
    if n < 2:
        return [0.0] * n
    dx = [xs[i + 1] - xs[i] for i in range(n - 1)]
    delta = [(rs[i + 1] - rs[i]) / dx[i] if dx[i] > 0 else 0.0 for i in range(n - 1)]
    m = [0.0] * n
    m[0], m[-1] = delta[0], delta[-1]
    for i in range(1, n - 1):
        if delta[i - 1] * delta[i] <= 0:
            m[i] = 0.0  # extremum ou palier : tangente plate, pas de dépassement
        else:
            w1, w2 = 2 * dx[i] + dx[i - 1], dx[i] + 2 * dx[i - 1]
            m[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i])
    return m


def _page_slopes(
    raw_curves: list[list[tuple[float, float]]], page_width: float
) -> list[list[float]]:
    """Pentes par page, calculées sur la séquence GLOBALE : le sommet de sortie
    d'une page et celui d'entrée de la suivante sont le MÊME point (même x
    global, même ratio), ils reçoivent la même tangente."""
    xs: list[float] = []
    rs: list[float] = []
    index: list[list[int]] = []
    for p, curve in enumerate(raw_curves):
        idx = []
        for k, (x, r) in enumerate(curve):
            if p and k == 0:
                idx.append(len(xs) - 1)  # = sortie de la page précédente
                continue
            xs.append(x + p * page_width)
            rs.append(r)
            idx.append(len(xs) - 1)
        index.append(idx)
    m = _monotone_slopes(xs, rs)
    return [[m[i] for i in idx] for idx in index]


def _monotone_slopes(xs: list[float], rs: list[float]) -> list[float]:
    """Tangentes de Fritsch–Carlson : une spline cubique qui ne redescend jamais
    entre deux sommets d'une suite croissante (une Bézier « naïve » creuserait
    des vallées entre deux paliers). Pente en ratio / px."""
    n = len(xs)
    if n < 2:
        return [0.0] * n
    dx = [xs[i + 1] - xs[i] for i in range(n - 1)]
    delta = [(rs[i + 1] - rs[i]) / dx[i] if dx[i] > 0 else 0.0 for i in range(n - 1)]
    m = [0.0] * n
    m[0], m[-1] = delta[0], delta[-1]
    for i in range(1, n - 1):
        if delta[i - 1] * delta[i] <= 0:
            m[i] = 0.0  # extremum ou palier : tangente plate, pas de dépassement
        else:
            w1, w2 = 2 * dx[i] + dx[i - 1], dx[i] + 2 * dx[i - 1]
            m[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i])
    return m


def _page_slopes(
    raw_curves: list[list[tuple[float, float]]], page_width: float
) -> list[list[float]]:
    """Pentes par page, calculées sur la séquence GLOBALE : le sommet de sortie
    d'une page et celui d'entrée de la suivante sont le MÊME point (même x
    global, même ratio), ils reçoivent la même tangente."""
    xs: list[float] = []
    rs: list[float] = []
    index: list[list[int]] = []
    for p, curve in enumerate(raw_curves):
        idx = []
        for k, (x, r) in enumerate(curve):
            if p and k == 0:
                idx.append(len(xs) - 1)  # = sortie de la page précédente
                continue
            xs.append(x + p * page_width)
            rs.append(r)
            idx.append(len(xs) - 1)
        index.append(idx)
    m = _monotone_slopes(xs, rs)
    return [[m[i] for i in idx] for idx in index]


def _curve_lag(style: TimelineStyle) -> float:
    """Le décalage demandé, borné pour que le 4ᵉ sommet reste DANS sa page."""
    room = style.page_width - slot_x(PAGE_SIZE - 1, style, PAGE_SIZE) - 1.0
    return max(0.0, min(style.curve_lag, room))


def _boundary_ratio(last: PointSpec, first: PointSpec, style: TimelineStyle, lag: float) -> float:
    """Ratio de la courbe à la frontière entre la page de `last` et celle de `first` :
    interpolation linéaire entre leurs sommets DÉCALÉS, `first` étant posé une
    page plus loin."""
    x_last, x_first = last.x + lag, first.x + lag + style.page_width
    span = x_first - x_last
    t = (style.page_width - x_last) / span if span else 0.0
    return last.ratio + t * (first.ratio - last.ratio)


# ── Orchestration ────────────────────────────────────────────────────────────


FILENAME = "timeline.svg"
SUBDIR = "_timeline"


def generate_timeline(
    tracks,
    *,
    artist_name: str,
    entries: list[EntryChoice] | None = None,
    pages: int | None = None,
    style: TimelineStyle | None = None,
    output_path=None,
    disabled: frozenset[int] | set[int] = frozenset(),
    record_types: Mapping[str, str] | None = None,
) -> TimelineResult:
    """Génère `timeline.svg` + `timeline.json` pour un artiste.

    `tracks` = TOUS les morceaux de l'artiste ; `disabled` = les ids désactivés,
    qui restent proposables mais sortent du cumul. `entries` = la sélection
    (override mémorisé ou saisie) ; à défaut, `default_selection` sur `pages`
    pages (défaut : `default_page_count`).
    """
    style = style or TimelineStyle()
    disabled = frozenset(disabled)
    candidates = build_candidates(tracks, artist_name, disabled, record_types)
    if entries is None:
        count = (pages or default_page_count(candidates)) * PAGE_SIZE
        entries = _entries_from_defaults(candidates, default_selection(candidates, count))

    spec = build_timeline_spec(tracks, entries, style, candidates=candidates, disabled=disabled)

    output_path = (
        Path(output_path) if output_path else default_output_path(artist_name, SUBDIR, FILENAME)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_timeline_svg(spec, output_path)
    json_path = output_path.with_suffix(".json")
    write_timeline_json(build_payload(spec, artist_name=artist_name), json_path)

    active = [t for t in tracks if t.id not in disabled]
    dated = [t for t in active if track_date(t) is not None]
    return TimelineResult(
        spec=spec,
        path=output_path,
        json_path=json_path,
        page_count=len(spec.pages),
        entry_count=len(entries),
        total_cumul=spec.total_cumul,
        undated_count=len(active) - len(dated),
        unstreamed_count=sum(1 for t in dated if track_streams(t) == 0),
        missing_covers=tuple(
            p.key for page in spec.pages for p in page.points if p.cover_abs is None
        ),
    )


# ── Aperçu HTML ──────────────────────────────────────────────────────────────

_PREVIEW_CSS = (
    "body{margin:0;font-family:Arial,sans-serif;background:#f5f5f5}"
    "h1{font-size:16px;font-weight:normal;color:#333;margin:12px 14px 0}"
    ".grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:14px}"
    "figure{margin:0;border:1px solid #ccc;border-radius:6px;background:#fff;overflow:hidden}"
    "figure svg{width:100%;height:auto;display:block}"
    "figcaption{padding:8px 12px;font-size:15px;color:#333;border-top:1px solid #eee}"
    ".source{position:absolute;width:0;height:0;overflow:hidden}"
)


def build_preview_html(spec: TimelineSpec, svg: str, *, artist_name: str) -> str:
    """Page HTML : les pages en grille 2 × 2, à taille lisible.

    Le SVG complet est INLINÉ une fois (invisible) et chaque vignette le rejoue
    par `<use href="#page-N">` sur la fenêtre de sa page — un `<img>` sur le
    fichier SVG n'afficherait pas les pochettes (ressources externes bloquées).
    """
    style = spec.style
    figures = []
    for page in spec.pages:
        x0 = (page.index - 1) * (style.page_width + style.page_gap)
        figures.append(
            f'<figure><svg viewBox="{x0:g} 0 {style.page_width:g} {style.page_height:g}" '
            f'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
            f'<use href="#page-{page.index}"/></svg>'
            f"<figcaption>Page {page.index} — {page.cartouche_text} cumulés</figcaption></figure>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Timeline — {artist_name}</title><style>{_PREVIEW_CSS}</style></head>"
        f"<body><h1>Timeline — {artist_name} ({len(spec.pages)} pages, cumul final "
        f"{format_streams_short(spec.total_cumul)})</h1>"
        f"<div class='source'>{svg}</div>"
        f"<div class='grid'>{''.join(figures)}</div></body></html>"
    )


def generate_timeline_preview(
    tracks,
    *,
    artist_name: str,
    entries: list[EntryChoice] | None = None,
    pages: int | None = None,
    style: TimelineStyle | None = None,
    output_path=None,
    disabled: frozenset[int] | set[int] = frozenset(),
    record_types: Mapping[str, str] | None = None,
) -> Path:
    """Même rendu que `generate_timeline`, mais en page HTML d'APERÇU seulement :
    rien n'est écrit dans `timeline.svg` / `timeline.json` (l'export attendu par
    Illustrator reste celui qu'on a validé). Renvoie le chemin du HTML."""
    style = style or TimelineStyle()
    disabled = frozenset(disabled)
    candidates = build_candidates(tracks, artist_name, disabled, record_types)
    if entries is None:
        count = (pages or default_page_count(candidates)) * PAGE_SIZE
        entries = _entries_from_defaults(candidates, default_selection(candidates, count))
    spec = build_timeline_spec(tracks, entries, style, candidates=candidates, disabled=disabled)

    output_path = (
        Path(output_path)
        if output_path
        else default_output_path(artist_name, SUBDIR, "apercu.html").parent
        / "apercu"
        / "apercu.html"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    svg = write_timeline_svg(spec, None)
    output_path.write_text(build_preview_html(spec, svg, artist_name=artist_name), encoding="utf-8")
    return output_path
