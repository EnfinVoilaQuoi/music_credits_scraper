"""Payload `timeline.json` : le jeu de données que consomme le script Illustrator.

Le script `scripts/illustrator/timeline.jsx` (hors dépôt, comme les autres) pose
une page par plan de travail : pochettes, libellés, années, disques de
certification (symboles de sa bibliothèque, choisis par `body` + `palier`),
cartouche, et la courbe des streams cumulés.

Séparation des rôles côté géométrie : le JSON transporte des **ratios** pour la
courbe (part du cumul final, 0-1) et des indices de slot pour les points, jamais
des pixels de page. C'est le JSX qui convertit, à partir des **repères nommés du
template** — la maquette reste maîtresse des dimensions.

Sortie déterministe (clés dans un ordre fixe, flottants arrondis) : deux
générations donnent un fichier byte-identique, comme le SVG.
"""

import json

from src.dataviz.timeline_svg import PageSpec, PointSpec, TextRun, TimelineSpec, TimelineStyle

PAYLOAD_VERSION = 1

# Précision des ratios : 6 décimales suffisent (une courbe de 1 100 px → 1e-3 px).
_RATIO_PRECISION = 6


def _style_payload(style: TimelineStyle) -> dict:
    """Bloc `style` : ce que le JSX doit savoir. Les réglages « aperçu seulement »
    (opacité du voile, ligne, pastilles, forme du cartouche) restent ABSENTS :
    Illustrator a le vrai template et son dégradé. `disc_fill` n'y sert qu'au
    disque de SECOURS quand un symbole `certif-*` manque."""
    return {
        "page_width": style.page_width,
        "page_height": style.page_height,
        "font_light": style.font_light,
        "font_semibold": style.font_semibold,
        "size_label": style.size_label,
        "label_line_height": style.label_line_height,
        "label_gap": style.label_gap,
        "size_year": style.size_year,
        "year_offset": style.year_offset,
        "text_color": style.text_color,
        "cover_large": style.cover_large,
        "cover_small": style.cover_small,
        "cover_offset": style.cover_offset,
        "cover_radius": style.cover_radius,
        "cover_stroke": style.cover_stroke,
        "cover_stroke_width": style.cover_stroke_width,
        "disc_fill": style.disc_fill,
        "disc_overlap": style.disc_overlap,
        "disc_step": style.disc_step,
        "size_cartouche_value": style.size_cartouche_value,
        "size_cartouche": style.size_cartouche,
        "cartouche_lines": list(style.cartouche_lines),
        "curve_width": style.curve_width,
        "curve_stroke": style.curve_stroke,
        "curve_lag": style.curve_lag,
        "background_fill": style.background_fill,
        "under_curve_fill": style.under_curve_fill,
        "under_curve_opacity": style.under_curve_opacity,
        "under_curve_blend": style.under_curve_blend,
    }


def _runs_payload(runs: tuple[TextRun, ...]) -> list[dict]:
    return [{"text": run.text, "bold": run.bold} for run in runs]


def _lines_payload(lines: tuple[tuple[TextRun, ...], ...]) -> list[list[dict]]:
    """Une liste par LIGNE (un libellé peut se replier), chaque ligne = ses runs."""
    return [_runs_payload(runs) for runs in lines]


def _point_payload(point: PointSpec, slot: int) -> dict:
    return {
        "key": point.key,
        "kind": point.kind,
        "slot": slot,
        "date": point.date,
        "year": point.year_label,
        "size": point.size,
        "above": point.above,
        "line1": _lines_payload(point.line1),
        "line2": _lines_payload(point.line2),
        "cover": point.cover,
        "cover_abs": point.cover_abs,
        "cert": (
            {
                "body": point.cert.body,
                "palier": point.cert.palier,
                "multiplier": point.cert.multiplier,
                "level": point.cert.level,
                "category": point.cert.category,
            }
            if point.cert
            else None
        ),
        "disc_side": point.disc_side,
        "streams": point.streams,
        "cumul": point.cumul,
        "ratio": round(point.ratio, _RATIO_PRECISION),
    }


def _page_payload(page: PageSpec, page_width: float) -> dict:
    return {
        "index": page.index,
        "background": {"key": page.background_key, "cover_abs": page.background_abs},
        "entry": {
            "cumul": page.entry_cumul,
            "ratio": round(page.entry_ratio, _RATIO_PRECISION),
        },
        "exit": {"ratio": round(page.exit_ratio, _RATIO_PRECISION)},
        # Sommets à poser TELS QUELS : x en proportion de la largeur de page
        # (0 = bord gauche, 1 = bord droit), y en ratio du cumul final, `slope`
        # = pente lissée en ratio par unité de x_ratio (poignées de Bézier au
        # tiers du segment : voir `timeline_svg.bezier_handles`).
        "curve": [
            {
                "x_ratio": round(x / page_width, _RATIO_PRECISION),
                "ratio": round(r, _RATIO_PRECISION),
                "slope": round(m * page_width, _RATIO_PRECISION),
            }
            for x, r, m in page.curve
        ],
        "cartouche": {"value": page.cartouche_value, "text": page.cartouche_text},
        "points": [_point_payload(p, i) for i, p in enumerate(page.points)],
    }


def build_payload(spec: TimelineSpec, *, artist_name: str) -> dict:
    """Construit le payload complet à partir d'un `TimelineSpec` déjà résolu."""
    return {
        "version": PAYLOAD_VERSION,
        "artist": artist_name,
        "style": _style_payload(spec.style),
        "total_cumul": spec.total_cumul,
        "pages": [_page_payload(page, spec.style.page_width) for page in spec.pages],
    }


def write_timeline_json(payload: dict, path) -> str:
    """Sérialise `payload`. Écrit dans `path` si fourni ; renvoie la chaîne."""
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if path is not None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return text
