"""Générateur « Structure » : la structure des morceaux d'un album, en barres.

Une ligne par morceau, barre normalisée à 100 % de la durée, découpée par type de
section (intro/outro, couplet, refrain, pont). Les sections viennent des paroles
STRUCTURÉES de Genius (`[Couplet 1 : X]`) datées par les paroles SYNCHRONISÉES
(LRC) via `lyrics_sync.extract_sections`.

Intro et outro sont **déduites du silence** : Genius tague rarement `[Intro]` /
`[Outro]`, mais il y a toujours du temps avant la 1ʳᵉ parole et après la dernière.
Les tags explicites fusionnent avec les silences déduits (même couleur, blocs
contigus fusionnés).

Module pur, GUI-indépendant et **déterministe** (aucun aléa, tri total) : deux
générations du même album produisent un SVG byte-identique. Pilotable aussi via
`scripts/structure.py`.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from src.dataviz.bubble_prod import default_output_path, list_albums, select_album_tracks
from src.dataviz.structure_json import build_payload, write_structure_json
from src.dataviz.structure_svg import (
    DURATION_REFERENCE_SECONDS,
    SECTION_KINDS,
    SegmentSpec,
    StructureSpec,
    StructureStyle,
    TrackRowSpec,
    write_structure_svg,
)
from src.utils.credit_normalize import display_name, identity_key
from src.utils.lyrics_sync import extract_sections, parse_lrc
from src.utils.title_matching import clean_display_title, normalize_title, split_title_paren

__all__ = [
    "StructureResult",
    "generate_structure",
    "list_albums",
    "section_kind",
    "select_album_tracks",
]

# Vocabulaire des en-têtes de section (repris de la regex d'affichage de
# `gui/windows/track_details.py`). Un tag inconnu retombe sur « couplet ».
_KIND_PATTERNS: tuple[tuple[str, str], ...] = (
    # Une plage non chantée — interlude, solo, passage instrumental — est une
    # intro/outro comme les autres (décision utilisateur 2026-08-24).
    ("intro_outro", r"intro|outro|interlude|instrumental|solo|piano|guitare|guitar"),
    ("pont", r"pont|bridge|pre-?chorus|pre-?refrain"),
    ("refrain", r"refrain|chorus|hook"),
    ("couplet", r"couplet|verse|partie|part"),
)
_KIND_RE = tuple((kind, re.compile(pattern, re.IGNORECASE)) for kind, pattern in _KIND_PATTERNS)

_DEFAULT_KIND = "couplet"

# Faux en-tête posé par Genius en tête de page (« [Paroles de "Genèse" : Django] ») :
# ce n'est PAS une section, il ne doit pas produire de segment.
_IGNORED_HEADER_RE = re.compile(r"^\s*paroles?\s+de\b", re.IGNORECASE)


@dataclass(frozen=True)
class StructureResult:
    """Retour de `generate_structure` : le spec rendu + le chemin du SVG.

    `unaligned_count` = sections dont la 1ʳᵉ ligne n'a pas été retrouvée dans le
    LRC : leur temps est absorbé par la section précédente. Non bloquant (le
    visuel reste juste dans les grandes masses) mais à signaler — c'est le seul
    indicateur qui dit « ce morceau mérite un coup d'œil ».

    `unnumbered_count` = morceaux sans `track_number`. Eux tombent en fin de
    tracklist par ordre alphabétique : l'ordre du visuel n'est alors PAS celui de
    l'album, et rien d'autre ne le signale.
    """

    spec: StructureSpec
    path: Path
    json_path: Path
    track_count: int
    section_count: int
    unaligned_count: int
    unnumbered_count: int


def section_kind(label: str) -> str:
    """Type de section d'un en-tête (« Couplet 1 : Isha » → « couplet »).

    L'ordre de test compte : « Pre-Chorus » doit tomber en « pont » AVANT que
    « chorus » ne l'attrape en « refrain ».
    """
    for kind, pattern in _KIND_RE:
        if pattern.search(label or ""):
            return kind
    return _DEFAULT_KIND


# ── Découpage d'un morceau ───────────────────────────────────────────────────


def _track_duration(track) -> int | None:
    duration = track.duration
    return int(duration) if duration else None


# Bornes de l'estimation de durée de la DERNIÈRE ligne chantée (secondes).
_LAST_LINE_MIN, _LAST_LINE_MAX = 1.0, 6.0

# Silence minimal entre la fin du chant d'une section et le début de la suivante
# pour valoir PLAGE INSTRUMENTALE. En dessous : simple respiration entre deux
# vers, on prolonge la section (sinon le visuel se pique d'éclats parasites).
_INSTRUMENTAL_MIN = 8.0


def _last_line_duration(lrc: str) -> float:
    """Durée estimée de la dernière ligne chantée, en secondes.

    Le LRC horodate le DÉBUT de chaque ligne, jamais sa fin : sans estimation,
    l'outro démarrerait au premier mot de la dernière ligne au lieu du dernier,
    et serait surestimée d'une ligne sur CHAQUE morceau. On prend l'intervalle
    médian entre lignes (borné : une pause instrumentale au milieu ne doit pas
    gonfler l'estimation).
    """
    times = [t for t, _ in parse_lrc(lrc)]
    gaps = sorted(b - a for a, b in zip(times, times[1:], strict=False) if b > a)
    if not gaps:
        return _LAST_LINE_MIN
    return min(_LAST_LINE_MAX, max(_LAST_LINE_MIN, gaps[len(gaps) // 2]))


def _merge_deduced(
    segments: list[tuple[str, float, float, bool]],
) -> list[tuple[str, float, float]]:
    """Absorbe les silences DÉDUITS dans la section explicite voisine de même type.

    Un `[Intro]` collé au silence initial est un seul bloc (« intro de 0 à 0:12 »).
    En revanche deux sections EXPLICITES de même type qui se suivent restent
    distinctes — deux couplets d'affilée, ou l'outro d'une partie suivie de
    l'intro de la suivante sur un 2-en-1 : le rendu les sépare d'un filet.
    """
    merged: list[tuple[str, float, float]] = []
    prev_deduced = False
    for kind, start, end, deduced in segments:
        fusionnable = merged and merged[-1][0] == kind and (deduced or prev_deduced)
        if fusionnable:
            merged[-1] = (kind, merged[-1][1], end)
        else:
            merged.append((kind, start, end))
        # Un bloc né d'une fusion avec un déduit reste « ouvert » côté droit
        # seulement si c'est le déduit qui vient d'arriver.
        prev_deduced = deduced
    return merged


def _track_segments(track, duration: int) -> tuple[SegmentSpec, ...]:
    """Segments d'un morceau, intro/outro déduites comprises.

    Règle intro/outro (hors tag explicite) : l'intro va du début du morceau au
    premier mot de la 1ʳᵉ section chantée, l'outro du dernier mot de la dernière
    section à la fin du morceau. Une intro/outro purement instrumentale est donc
    une intro/outro comme les autres, sans avoir besoin d'être taguée.

    Les sections non alignées (`start is None`) sont ignorées : leur temps est
    absorbé par la section précédente, qui court jusqu'à la suivante alignée.
    """
    sections = [
        s
        for s in extract_sections(track.lyrics.text, track.lyrics.synced)
        if s.start is not None and not _IGNORED_HEADER_RE.match(s.label)
    ]
    if not sections:
        return ()

    line_dur = _last_line_duration(track.lyrics.synced)

    raw: list[tuple[str, float, float, bool]] = []
    first_start = max(0.0, min(float(sections[0].start), duration))
    if first_start > 0:
        raw.append(("intro_outro", 0.0, first_start, True))

    for i, sec in enumerate(sections):
        is_last = i == len(sections) - 1
        start = max(0.0, min(float(sec.start), duration))
        # Le chant s'arrête à la dernière ligne de la section (+ sa durée) ; la
        # borne `end` (début de la section suivante) peut être bien plus loin.
        sung = float(sec.sung_end if sec.sung_end is not None else sec.start) + line_dur
        # Après la dernière section, c'est la fin du morceau qui borne (et non
        # `sec.end`, qui vaut le dernier timestamp — l'outro serait écrasée).
        display_end = float(duration) if is_last else float(sec.end or duration)
        end = max(start, min(sung, display_end, float(duration)))
        if end > start:
            raw.append((section_kind(sec.label), start, end, False))
        if is_last:
            continue  # la queue du morceau est l'outro déduite, ci-dessous

        # Écart chant → section suivante = plage instrumentale (solo, interlude
        # non chanté, break). En dessous du seuil c'est une respiration : on
        # prolonge la section plutôt que d'afficher un éclat parasite.
        gap_end = min(display_end, float(duration))
        if gap_end - end >= _INSTRUMENTAL_MIN:
            raw.append(("intro_outro", end, gap_end, False))
        elif gap_end > end and raw:
            kind, seg_start, _, deduced = raw[-1]
            raw[-1] = (kind, seg_start, gap_end, deduced)

    last_end = raw[-1][2] if raw else 0.0
    if duration > last_end:
        raw.append(("intro_outro", last_end, float(duration), True))

    return tuple(
        SegmentSpec(kind=kind, start=start, end=end, ratio=(end - start) / duration)
        for kind, start, end in _merge_deduced(raw)
    )


# ── Validation (bloquante) ───────────────────────────────────────────────────


def _section_stats(track) -> tuple[int, int]:
    """(sections alignées, sections ALIGNABLES) d'un morceau.

    Sont exclues du dénominateur les sections sans paroles à ancrer — faux
    en-tête Genius, en-tête de regroupement d'un 2-en-1 (« [Partie 2 : Risotto
    Gambas] »), « [Outro Instrumentale] » : les compter serait un faux échec.
    """
    sections = [
        s
        for s in extract_sections(track.lyrics.text, track.lyrics.synced)
        if s.content_lines and not _IGNORED_HEADER_RE.match(s.label)
    ]
    return sum(1 for s in sections if s.start is not None), len(sections)


def _track_problem(track) -> str | None:
    """Motif d'inexploitabilité d'un morceau, ou None s'il est utilisable."""
    if not _track_duration(track):
        return "durée manquante"
    if not (track.lyrics.text or "").strip():
        return "paroles manquantes"
    if not (track.lyrics.synced or "").strip():
        return "paroles synchronisées manquantes"
    if not any(line.lstrip().startswith("[") for line in track.lyrics.text.splitlines()):
        return "aucune section [...] dans les paroles"
    if not _track_segments(track, _track_duration(track)):
        return "aucune section alignable sur les timestamps"
    return None


# ── Construction du spec ─────────────────────────────────────────────────────


def _sort_key(track):
    """Tri total et déterministe : `track_number` d'abord, titre en départage."""
    return (track.track_number is None, track.track_number or 0, normalize_title(track.title or ""))


def _duration_ratio(duration: int, dmin: int, dmax: int) -> float:
    """Position du morceau sur l'échelle des durées de l'album, **non bornée**.

    Échelle min/max de l'album, mais la borne haute est plafonnée à 5:00
    (`DURATION_REFERENCE_SECONDS`) : un morceau plus long sort de sa colonne et
    attire l'œil — c'est rare, donc c'est l'effet recherché. Si aucun morceau ne
    dépasse 5:00, le comportement est celui d'une simple échelle min/max.

    Album à durée unique (aucune gradation à montrer) → 0 pour tous (même garde
    que `_node_size` dans `bubble_prod`).
    """
    ref_max = min(dmax, DURATION_REFERENCE_SECONDS)
    if ref_max <= dmin:  # tout l'album dépasse la référence → repli min/max pur
        ref_max = dmax
    if ref_max == dmin:
        return 0.0
    return max(0.0, (duration - dmin) / (ref_max - dmin))


def _row_feats(track) -> tuple[str, ...]:
    """Invités d'un morceau, graphie nettoyée et dédupliquée, ordre préservé.

    `featured_artists_list` lit le champ dédié (string CSV de Genius) et retombe
    sur les crédits `Featured Artist` — c'est le seul accès à utiliser.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in track.featured_artists_list:
        name = display_name(raw)
        key = identity_key(raw)
        if not name or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return tuple(out)


def _project_row(rows: tuple[TrackRowSpec, ...], style: StructureStyle) -> TrackRowSpec:
    """Ligne d'agrégat : part de chaque type, **pondérée par la durée**.

    Le rouge est réparti en deux à égalité (début et fin), puis couplet, refrain
    et pont chacun agrégé — visuel symétrique d'une ligne de morceau. Hors échelle
    des durées (c'est l'album entier) → ratio 1, largeur maximale.
    """
    total = sum(row.duration for row in rows)
    shares = dict.fromkeys(SECTION_KINDS, 0.0)
    for row in rows:
        for seg in row.segments:
            shares[seg.kind] += (seg.end - seg.start) / total

    red = shares["intro_outro"] / 2.0
    ordered = (
        ("intro_outro", red),
        ("couplet", shares["couplet"]),
        ("refrain", shares["refrain"]),
        ("pont", shares["pont"]),
        ("intro_outro", red),
    )
    cursor = 0.0
    segments = []
    for kind, ratio in ordered:
        if ratio <= 0:
            continue
        start = cursor * total
        cursor += ratio
        segments.append(SegmentSpec(kind=kind, start=start, end=cursor * total, ratio=ratio))

    return TrackRowSpec(
        index=0,
        title="Structure du Projet",
        duration=total,
        bpm=None,
        segments=tuple(segments),
        duration_ratio=1.0,
    )


def _row_spec(track, index: int, duration: int, dmin: int, dmax: int) -> TrackRowSpec:
    title, paren = split_title_paren(clean_display_title(track.title or ""))
    return TrackRowSpec(
        index=index,
        title=title,
        duration=duration,
        bpm=track.audio.bpm,
        segments=_track_segments(track, duration),
        duration_ratio=_duration_ratio(duration, dmin, dmax),
        title_paren=paren,
        feats=_row_feats(track),
    )


def build_structure_spec(tracks, style: StructureStyle, *, with_project: bool = True):
    """`StructureSpec` des morceaux fournis (déjà validés et triés)."""
    durations = [_track_duration(t) for t in tracks]
    dmin, dmax = min(durations), max(durations)

    rows = tuple(
        _row_spec(track, i, duration, dmin, dmax)
        for i, (track, duration) in enumerate(zip(tracks, durations, strict=True), start=1)
    )

    project_row = _project_row(rows, style) if with_project else None
    return StructureSpec(
        width=style.zone_width,
        height=style.zone_height,
        rows=rows,
        project_row=project_row,
        style=style,
    )


# ── Orchestration ────────────────────────────────────────────────────────────


def generate_structure(
    tracks,
    album: str,
    *,
    artist_name: str = "",
    style: StructureStyle | None = None,
    with_project: bool = True,
    output_path=None,
) -> StructureResult:
    """Génère le SVG « Structure » de `album` et renvoie un `StructureResult`.

    Lève `ValueError` si l'album n'a aucun morceau, ou si au moins un morceau est
    inexploitable (message listant les titres fautifs et leur motif) : un visuel
    troué serait pire qu'une erreur explicite.
    """
    style = style or StructureStyle()
    album_tracks = select_album_tracks(tracks, album)
    if not album_tracks:
        raise ValueError(f"Aucun morceau trouvé pour l'album « {album} »")

    album_tracks = sorted(album_tracks, key=_sort_key)
    problems = [(t.title or "?", p) for t in album_tracks if (p := _track_problem(t))]
    if problems:
        details = "\n".join(f"  • {title} — {reason}" for title, reason in problems)
        raise ValueError(
            f"Structure impossible pour l'album « {album} » : "
            f"{len(problems)} morceau(x) sur {len(album_tracks)} sans données exploitables.\n"
            f"{details}"
        )

    spec = build_structure_spec(album_tracks, style, with_project=with_project)

    if output_path is None:
        output_path = default_output_path(artist_name, album, "structure.svg")
    output_path = Path(output_path)
    write_structure_svg(spec, output_path)

    # Le JSON part toujours à côté du SVG : c'est lui que le script Illustrator
    # consomme, le SVG n'étant qu'une prévisualisation.
    json_path = output_path.with_suffix(".json")
    write_structure_json(build_payload(spec, artist_name=artist_name, album=album), json_path)

    stats = [_section_stats(t) for t in album_tracks]
    return StructureResult(
        spec=spec,
        path=output_path,
        json_path=json_path,
        track_count=len(spec.rows),
        section_count=sum(len(row.segments) for row in spec.rows),
        unaligned_count=sum(total - aligned for aligned, total in stats),
        unnumbered_count=sum(1 for t in album_tracks if t.track_number is None),
    )
