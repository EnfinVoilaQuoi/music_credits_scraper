"""Payload `structure.json` : le jeu de données que consomme le script Illustrator.

Le script `scripts/illustrator/structure.jsx` est du **code commun** versionné —
il se corrige une fois pour tous les albums. Ce module produit les **données** qui
l'alimentent : une entrée par ligne, plus le style (police, palette, cotes).

Séparation des rôles côté géométrie : le JSON transporte des **ratios** (part de
la durée pour un segment, position sur l'échelle des durées pour le rectangle de
durée), jamais des pixels de barre. C'est le JSX qui convertit, à partir des
**repères nommés du template** — la maquette reste maîtresse des dimensions.

Sortie déterministe (clés dans un ordre fixe, flottants arrondis) : deux
générations du même album donnent un fichier byte-identique, comme le SVG.
"""

import json

from src.dataviz.structure_svg import (
    DURATION_REFERENCE_SECONDS,
    StructureSpec,
    StructureStyle,
    TrackRowSpec,
    format_duration,
)

PAYLOAD_VERSION = 1

# Précision des ratios : 6 décimales suffisent (une barre de 400 px → 4e-4 px).
_RATIO_PRECISION = 6


def _style_payload(style: StructureStyle) -> dict:
    """Bloc `style` : tout ce que le JSX doit savoir pour dessiner.

    `background_fill` est volontairement ABSENT : il n'existe que pour la
    prévisualisation SVG, Illustrator ayant le vrai fond du template.
    """
    return {
        "font_light": style.font_light,
        "font_semibold": style.font_semibold,
        "size_index": style.size_index,
        "size_title": style.size_title,
        "size_feat": style.size_feat,
        "size_duration": style.size_duration,
        "title_line_gap": style.title_line_gap,
        "text_color": style.text_color,
        "colors": dict(sorted(style.colors.items())),
        "duration_fill": style.duration_fill,
        "duration_opacity": style.duration_opacity,
        "duration_overlap": style.duration_overlap,
        "duration_width_min": style.duration_width_min,
        "duration_reference_seconds": DURATION_REFERENCE_SECONDS,
        "corner_radius": style.corner_radius,
        "separator_gap": style.separator_gap,
        "row_height_max": style.row_height_max,
        "gap_ratio": style.gap_ratio,
        "project_gap": style.project_gap,
    }


def _row_payload(row: TrackRowSpec) -> dict:
    return {
        "index": row.index,
        "title": row.title,
        "title_paren": row.title_paren,
        "feats": list(row.feats),
        "duration": format_duration(row.duration),
        "duration_seconds": row.duration,
        "duration_ratio": round(row.duration_ratio, _RATIO_PRECISION),
        "bpm": row.bpm,
        "segments": [
            {"kind": seg.kind, "ratio": round(seg.ratio, _RATIO_PRECISION)} for seg in row.segments
        ],
    }


def build_payload(spec: StructureSpec, *, artist_name: str, album: str) -> dict:
    """Construit le payload complet à partir d'un `StructureSpec` déjà résolu."""
    return {
        "version": PAYLOAD_VERSION,
        "artist": artist_name,
        "album": album,
        "style": _style_payload(spec.style),
        "rows": [_row_payload(row) for row in spec.rows],
        "project_row": _row_payload(spec.project_row) if spec.project_row else None,
    }


def write_structure_json(payload: dict, path) -> str:
    """Sérialise `payload`. Écrit dans `path` si fourni ; renvoie la chaîne.

    Pas de `sort_keys` : l'ordre d'insertion ci-dessus est déjà déterministe et
    plus lisible à l'œil qu'un tri alphabétique.
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if path is not None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return text
