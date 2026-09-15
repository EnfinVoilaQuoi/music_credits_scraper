"""Modèle calculé (`TimelineSpec`) + rendu SVG de prévisualisation (svgwrite).

Un carrousel de **pages** (4 par défaut, 3 pour un petit artiste), chacune une
frise horizontale de **4 points** — un projet par point, alternés au-dessus et
en dessous de la ligne : pochette (grand carré = projet de l'artiste, petit =
feat / freestyle), libellé sur deux lignes (la première en Light avec des mots
en SemiBold, la seconde en SemiBold), l'année au premier projet de l'année,
disques de certification à moitié cachés derrière la pochette, et un cartouche
« N M de streams cumulés estimés* » calé sur le 4ᵉ point de la page.

La **courbe** des streams cumulés traverse les pages : son échelle verticale est
GLOBALE (0 → cumul final) et la page N reprend là où la page N−1 s'est arrêtée
— le point de jonction est interpolé à la frontière, si bien que les pages
posées côte à côte forment un seul trait.

Le SVG est une **prévisualisation** : une seule image, les pages côte à côte.
La planche de production est construite par `scripts/illustrator/timeline.jsx`
(hors dépôt) à partir de `timeline.json`. `TimelineSpec` est un modèle pur,
produit par `timeline.build_timeline_spec` et consommé par `write_timeline_svg`
comme par `timeline_json.build_payload`.

Calques dans un ordre fixe par page (`line` < `curve` < `discs` < `covers` <
`labels` < `years` < `cartouche`), ids stables (`cover-<page>-<i>`,
`disc-<page>-<i>-<k>`, `label1-<page>-<i>`…), coordonnées arrondies à précision
fixe → sortie byte-identique entre deux régénérations.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import svgwrite

from src.dataviz.bubble_svg import _fmt

# Marquage inline des mots en SemiBold dans un libellé : « Feat avec **Scylla** ».
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

SIZES: tuple[str, ...] = ("grand", "petit")


@dataclass(frozen=True)
class TimelineStyle:
    """Géométrie et habillage d'UNE page, calqués sur le template Illustrator.

    Les cotes sont celles d'un post carré-portrait Instagram (1080 × 1350 px).
    Elles pilotent la prévisualisation SVG ET voyagent dans le JSON ; côté
    Illustrator ce sont les **repères nommés** du template qui font foi.
    """

    # ── Page et zone ──
    page_width: float = 1080.0
    page_height: float = 1350.0
    page_gap: float = 40.0  # aperçu : espace entre deux pages posées côte à côte
    zone_x: float = 60.0  # bord gauche de la zone des points
    zone_width: float = 960.0
    line_y: float = 700.0  # ordonnée de la ligne de temps
    line_stroke: str = "#ffffff"
    line_width: float = 10.0
    dot_diameter: float = 25.0  # pastille sur la ligne, à chaque point
    dot_fill: str = "#13391e"
    # ── Pochettes ──
    cover_large: float = 300.0
    cover_small: float = 200.0
    cover_offset: float = 70.0  # distance ligne → bord de la pochette
    cover_radius: float = 22.0  # coins arrondis du cadre (aperçu)
    cover_stroke: str = "#9fd8a4"
    cover_stroke_width: float = 10.0
    cover_placeholder: str = "#3b3b3b"  # pochette absente (aperçu)
    # ── Disques de certification (aperçu : disques pleins ; Illustrator : symboles) ──
    # Diamètre = CÔTÉ du carré qu'il accompagne (300 ou 200) : seul le contour
    # de la pochette dépasse du disque.
    disc_overlap: float = 0.5  # part du disque cachée derrière la pochette (0-1)
    disc_step: float = 40.0  # décalage entre deux disques d'un multi
    disc_fill: str = "#e2b93b"
    disc_hole_ratio: float = 0.18  # trou central, en proportion du diamètre
    # ── Typographie (Montserrat) ──
    font_family: str = "Montserrat, sans-serif"
    font_light: str = "Montserrat-Light"  # nom PostScript, pour Illustrator
    font_semibold: str = "Montserrat-SemiBold"
    weight_light: str = "300"
    weight_semibold: str = "600"
    size_label: float = 30.0  # les deux lignes du libellé, en pt
    label_line_height: float = 1.2  # interligne, en proportion du corps
    label_gap: float = 24.0  # pochette → libellé
    size_year: float = 50.0
    year_offset: float = 22.0  # ligne → année (côté opposé à la pochette)
    text_color: str = "#ffffff"
    baseline_ratio: float = 0.35  # centrage vertical manuel (dominant-baseline ignoré)
    # ── Cartouche des streams cumulés ──
    cartouche_x: float = 880.0
    cartouche_y: float = 320.0
    cartouche_width: float = 190.0
    cartouche_height: float = 205.0
    cartouche_radius: float = 20.0
    cartouche_fill: str = "#8fd39a"
    cartouche_text_color: str = "#1f5d2f"
    size_cartouche_value: float = 50.0
    size_cartouche: float = 30.0
    cartouche_lines: tuple[str, ...] = ("de streams", "cumulés", "estimés*")
    # ── Courbe des streams cumulés (échelle GLOBALE sur toutes les pages) ──
    # Décalage de la courbe vers la DROITE, en px : les streams d'un projet ne
    # sont pas là le jour de sa sortie, ils s'accumulent après — la courbe monte
    # donc APRÈS le point. Sur la dernière page, le dernier sommet est porté au
    # bord droit du cadre (sinon elle stagne depuis le dernier album).
    curve_lag: float = 120.0
    curve_top: float = 120.0  # ordonnée du cumul final
    curve_bottom: float = 1230.0  # ordonnée de 0
    curve_stroke: str = "#9fd8a4"
    curve_width: float = 8.0
    # ── Fond : pochette du projet le plus streamé de la page, voile vert, et la
    #    zone SOUS la courbe assombrie. Le dégradé haut→bas du voile vit dans le
    #    template Illustrator ; l'aperçu se contente d'un aplat semi-transparent.
    background_fill: str = "#167332"
    background_opacity: float = 0.82  # aperçu seulement (Illustrator : dégradé)
    under_curve_fill: str = "#13391e"
    under_curve_opacity: float = 0.3
    under_curve_blend: str = "hard-light"  # mode de fusion (« lumière crue »)
    coord_precision: int = 2  # décimales des coordonnées (déterminisme)

    def cover_size(self, size: str) -> float:
        return self.cover_large if size == "grand" else self.cover_small


@dataclass(frozen=True)
class TextRun:
    """Un segment de libellé et sa graisse."""

    text: str
    bold: bool


@dataclass(frozen=True)
class CertSpec:
    """La certification retenue pour un point : organisme, palier, nombre de disques."""

    body: str  # « SNEP », « RIAA »…
    palier: str  # palier nu normalisé : « or », « platine »…
    multiplier: int  # nombre de disques à poser
    level: str  # libellé verbatim : « Double Platine »
    category: str  # « album » / « single »


@dataclass(frozen=True)
class PointSpec:
    """Un projet posé sur la frise (coordonnées locales à sa page)."""

    key: str
    kind: str  # « album » | « reedition » | « track »
    date: str  # ISO YYYY-MM-DD
    year_label: str | None  # « 2023 » au premier projet de l'année, sinon None
    line1: tuple[tuple[TextRun, ...], ...]  # une entrée par LIGNE (retour = `\n`)
    line2: tuple[tuple[TextRun, ...], ...]
    size: str  # « grand » | « petit »
    above: bool  # pochette au-dessus de la ligne
    cover: str | None  # chemin relatif (lecture humaine)
    cover_abs: str | None  # chemin absolu (aperçu SVG et JSX)
    cert: CertSpec | None
    disc_side: str  # « right » | « left » : côté où dépassent les disques
    streams: int  # streams estimés du projet lui-même (choix du fond de page)
    cumul: int  # streams cumulés à cette date
    ratio: float  # cumul / total (0-1), position sur la courbe
    x: float  # abscisse du point (centre de la pochette)


@dataclass(frozen=True)
class PageSpec:
    """Une page : ses 4 points et la tranche de courbe qui la traverse.

    `background_key` = le point dont la pochette fait le fond de la page (le
    projet de l'artiste le plus streamé de la page, à défaut le point le plus
    streamé) ; `background_abs` son chemin absolu, None sans pochette.
    """

    index: int  # 1-based
    points: tuple[PointSpec, ...]
    background_key: str | None
    background_abs: str | None
    entry_cumul: int  # cumul au dernier point de la page précédente (0 pour la 1re)
    entry_ratio: float  # ratio de la courbe au bord GAUCHE de la page
    exit_ratio: float  # ratio de la courbe au bord DROIT de la page
    # Sommets de la courbe, (x local en px, ratio, pente en ratio/px), du bord
    # gauche au bord droit — décalage, bord de cadre et tangentes LISSÉES déjà
    # appliqués : SVG et JSX posent les mêmes poignées de Bézier (`curve_path`).
    curve: tuple[tuple[float, float, float], ...]
    cartouche_value: int
    cartouche_text: str  # « 175 M »


@dataclass(frozen=True)
class TimelineSpec:
    pages: tuple[PageSpec, ...]
    total_cumul: int
    style: TimelineStyle


# ── Briques pures ────────────────────────────────────────────────────────────


def parse_marked(text: str) -> tuple[TextRun, ...]:
    """« Feat avec **Scylla** » → (Light « Feat avec  », SemiBold « Scylla »).

    Un `**` orphelin reste littéral ; les segments vides sont omis.
    """
    runs: list[TextRun] = []
    cursor = 0
    for match in _BOLD_RE.finditer(text or ""):
        if match.start() > cursor:
            runs.append(TextRun(text[cursor : match.start()], False))
        runs.append(TextRun(match.group(1), True))
        cursor = match.end()
    if cursor < len(text or ""):
        runs.append(TextRun(text[cursor:], False))
    return tuple(runs)


def parse_marked_lines(text: str) -> tuple[tuple[TextRun, ...], ...]:
    """Un libellé sur plusieurs lignes : `\\n` littéral (tapé dans un champ de
    saisie) ou vrai retour → une entrée par ligne, chacune découpée par
    `parse_marked`. Les lignes vides sont omises."""
    raw = (text or "").replace("\\n", "\n")
    return tuple(parse_marked(ln.strip()) for ln in raw.split("\n") if ln.strip())


def plain_text(runs: tuple[TextRun, ...]) -> str:
    """Le libellé sans marquage."""
    return "".join(run.text for run in runs)


def background_point(points: tuple[PointSpec, ...]) -> PointSpec | None:
    """Le point dont la pochette fait le fond : projet de l'artiste le plus streamé,
    à défaut le point le plus streamé — parmi ceux qui ONT une pochette."""
    with_cover = [p for p in points if p.cover_abs]
    if not with_cover:
        return None
    return max(with_cover, key=lambda p: (p.kind != "track", p.streams, p.key))


def format_streams_short(value: int) -> str:
    """Nombre court du cartouche : « 175 M », « 850 k », « 0 ».

    Entier en millions, comme sur le visuel de référence ; sous le million, en
    milliers. Arrondi au plus proche.
    """
    n = int(value or 0)
    if n >= 1_000_000:
        return f"{round(n / 1_000_000)} M"
    if n >= 1_000:
        return f"{round(n / 1_000)} k"
    return str(n)


def slot_x(index: int, style: TimelineStyle, slot_count: int = 4) -> float:
    """Abscisse du point `index` (0-based) : slots équirépartis dans la zone."""
    return style.zone_x + (index + 0.5) / slot_count * style.zone_width


def curve_y(ratio: float, style: TimelineStyle) -> float:
    """Ordonnée de la courbe pour un ratio 0-1 (échelle globale)."""
    return style.curve_bottom - ratio * (style.curve_bottom - style.curve_top)


def bezier_handles(
    curve: tuple[tuple[float, float, float], ...],
) -> list[tuple[float, float, float, float]]:
    """Poignées de chaque segment, en (x, ratio) : (c1x, c1r, c2x, c2r).

    Hermite → Bézier cubique : la poignée sortante du sommet i est à un tiers
    du segment le long de sa tangente, la poignée entrante du sommet i+1 idem.
    Les pentes viennent d'une spline MONOTONE (`timeline._monotone_slopes`) : la
    courbe ne redescend jamais entre deux sommets, quelle que soit la poignée.
    """
    out = []
    for (x0, r0, m0), (x1, r1, m1) in zip(curve, curve[1:], strict=False):
        third = (x1 - x0) / 3.0
        out.append((x0 + third, r0 + m0 * third, x1 - third, r1 - m1 * third))
    return out


def curve_path(curve, style: TimelineStyle, f) -> str:
    """Le tracé SVG (`M … C …`) de la courbe d'une page, Bézier par segment."""
    x0, r0, _ = curve[0]
    parts = [f"M{f(x0)},{f(curve_y(r0, style))}"]
    for (c1x, c1r, c2x, c2r), (x1, r1, _) in zip(bezier_handles(curve), curve[1:], strict=False):
        parts.append(
            f"C{f(c1x)},{f(curve_y(c1r, style))} {f(c2x)},{f(curve_y(c2r, style))} "
            f"{f(x1)},{f(curve_y(r1, style))}"
        )
    return " ".join(parts)


def cover_box(point: PointSpec, style: TimelineStyle) -> tuple[float, float, float]:
    """(x0, y0, côté) de la pochette d'un point."""
    side = style.cover_size(point.size)
    x0 = point.x - side / 2.0
    if point.above:
        y0 = style.line_y - style.cover_offset - side
    else:
        y0 = style.line_y + style.cover_offset
    return x0, y0, side


# ── Rendu SVG ────────────────────────────────────────────────────────────────


def _rounded_rect_path(x0: float, y0: float, w: float, h: float, r: float, f) -> str:
    """Rectangle aux quatre coins arrondis, en chemin (pas de `rx` : Illustrator
    les convertit en tracés de toute façon, et le chemin reste éditable)."""
    r = min(r, w / 2.0, h / 2.0)
    x1, y1 = x0 + w, y0 + h
    return " ".join(
        [
            f"M{f(x0 + r)},{f(y0)}",
            f"H{f(x1 - r)}",
            f"A{f(r)},{f(r)} 0 0 1 {f(x1)},{f(y0 + r)}",
            f"V{f(y1 - r)}",
            f"A{f(r)},{f(r)} 0 0 1 {f(x1 - r)},{f(y1)}",
            f"H{f(x0 + r)}",
            f"A{f(r)},{f(r)} 0 0 1 {f(x0)},{f(y1 - r)}",
            f"V{f(y0 + r)}",
            f"A{f(r)},{f(r)} 0 0 1 {f(x0 + r)},{f(y0)}",
            "Z",
        ]
    )


def write_timeline_svg(spec: TimelineSpec, path=None) -> str:
    """Sérialise `spec` en SVG (pages côte à côte). Écrit dans `path` si fourni."""
    style = spec.style
    prec = style.coord_precision

    def f(v):
        return _fmt(v, prec)

    n_pages = len(spec.pages)
    width = n_pages * style.page_width + max(0, n_pages - 1) * style.page_gap
    height = style.page_height
    dwg = svgwrite.Drawing(size=(f(width), f(height)), profile="full", debug=False)
    dwg.attribs["viewBox"] = f"0 0 {f(width)} {f(height)}"

    def text(runs, x, y, size, color, anchor="middle", ident=None):
        node = dwg.text(
            "",
            insert=(f(x), f(y)),
            font_size=f(size),
            font_family=style.font_family,
            fill=color,
            text_anchor=anchor,
            **({"id": ident} if ident else {}),
        )
        node.attribs["xml:space"] = "preserve"
        for run in runs:
            node.add(
                dwg.tspan(
                    run.text,
                    font_weight=style.weight_semibold if run.bold else style.weight_light,
                )
            )
        return node

    for page in spec.pages:
        offset_x = (page.index - 1) * (style.page_width + style.page_gap)
        g_page = dwg.g(id=f"page-{page.index}", transform=f"translate({f(offset_x)},0)")
        page_size = (f(style.page_width), f(style.page_height))
        # ── Fond : pochette recadrée (`slice` = sans clipPath), voile vert ──
        g_bg = dwg.g(id=f"background-{page.index}")
        g_bg.add(dwg.rect(insert=(0, 0), size=page_size, fill=style.background_fill))
        if page.background_abs:
            g_bg.add(
                dwg.image(
                    href=Path(page.background_abs).as_uri(),
                    insert=(0, 0),
                    size=page_size,
                    preserveAspectRatio="xMidYMid slice",
                )
            )
            g_bg.add(
                dwg.rect(
                    insert=(0, 0),
                    size=page_size,
                    fill=style.background_fill,
                    fill_opacity=f(style.background_opacity),
                )
            )
        g_page.add(g_bg)
        g_line = dwg.g(id=f"line-{page.index}")
        g_curve = dwg.g(id=f"curve-{page.index}")
        g_covers = dwg.g(id=f"covers-{page.index}")
        g_discs = dwg.g(id=f"discs-{page.index}")
        g_labels = dwg.g(id=f"labels-{page.index}")
        g_years = dwg.g(id=f"years-{page.index}")
        g_cartouche = dwg.g(id=f"cartouche-{page.index}")

        # ── Ligne de temps + pastilles ──
        g_line.add(
            dwg.line(
                start=(0, f(style.line_y)),
                end=(f(style.page_width), f(style.line_y)),
                stroke=style.line_stroke,
                stroke_width=f(style.line_width),
            )
        )
        for i, point in enumerate(page.points):
            g_line.add(
                dwg.circle(
                    center=(f(point.x), f(style.line_y)),
                    r=f(style.dot_diameter / 2.0),
                    fill=style.dot_fill,
                    id=f"dot-{page.index}-{i}",
                )
            )

        # ── Courbe : bord gauche → points → bord droit ──
        d = curve_path(page.curve, style, f)
        # Zone SOUS la courbe, assombrie en mode de fusion.
        under = dwg.path(
            d=f"{d} L{f(style.page_width)},{f(style.page_height)} L0,{f(style.page_height)} Z",
            fill=style.under_curve_fill,
            fill_opacity=f(style.under_curve_opacity),
            id=f"under-curve-{page.index}",
        )
        under.attribs["style"] = f"mix-blend-mode:{style.under_curve_blend}"
        g_curve.add(under)
        g_curve.add(
            dwg.path(
                d=d,
                fill="none",
                stroke=style.curve_stroke,
                stroke_width=f(style.curve_width),
                stroke_linejoin="round",
                stroke_linecap="round",
            )
        )

        for i, point in enumerate(page.points):
            x0, y0, side = cover_box(point, style)
            cy = y0 + side / 2.0

            # ── Disques : derrière la pochette, dépassant d'un côté ──
            if point.cert is not None:
                d = side
                sign = 1.0 if point.disc_side == "right" else -1.0
                # Centre du 1er disque : caché à `disc_overlap` derrière le bord.
                edge = point.x + sign * side / 2.0
                cx0 = edge + sign * d * (0.5 - style.disc_overlap)
                # Le disque du DESSOUS est le plus éloigné : dessiné en premier.
                for k in reversed(range(point.cert.multiplier)):
                    cx = cx0 + sign * k * style.disc_step
                    disc = dwg.g(id=f"disc-{page.index}-{i}-{k}")
                    disc.add(dwg.circle(center=(f(cx), f(cy)), r=f(d / 2.0), fill=style.disc_fill))
                    disc.add(
                        dwg.circle(
                            center=(f(cx), f(cy)),
                            r=f(d * style.disc_hole_ratio / 2.0),
                            fill=style.background_fill,
                        )
                    )
                    g_discs.add(disc)

            # ── Pochette : image (ou aplat) + cadre arrondi ──
            cover = dwg.g(id=f"cover-{page.index}-{i}")
            if point.cover_abs:
                # URI `file://` : un chemin Windows nu n'est pas résolu par les
                # navigateurs, et le JSX lit le chemin brut dans le JSON.
                cover.add(
                    dwg.image(
                        href=Path(point.cover_abs).as_uri(),
                        insert=(f(x0), f(y0)),
                        size=(f(side), f(side)),
                        preserveAspectRatio="xMidYMid slice",
                    )
                )
            else:
                cover.add(
                    dwg.rect(
                        insert=(f(x0), f(y0)), size=(f(side), f(side)), fill=style.cover_placeholder
                    )
                )
            cover.add(
                dwg.path(
                    d=_rounded_rect_path(x0, y0, side, side, style.cover_radius, f),
                    fill="none",
                    stroke=style.cover_stroke,
                    stroke_width=f(style.cover_stroke_width),
                )
            )
            g_covers.add(cover)

            # ── Libellé : ligne 1 (Light) puis ligne 2 (SemiBold), chacune pouvant
            #    se replier ; posé du côté de la pochette opposé à la ligne de temps ──
            lh = style.size_label * style.label_line_height
            lines = [("label1", ln) for ln in point.line1] + [("label2", ln) for ln in point.line2]
            if point.above:
                first_y = y0 - style.label_gap - (len(lines) - 1) * lh
            else:
                first_y = y0 + side + style.label_gap + style.size_label
            for j, (tag, runs) in enumerate(lines):
                g_labels.add(
                    text(
                        runs,
                        point.x,
                        first_y + j * lh,
                        style.size_label,
                        style.text_color,
                        ident=f"{tag}-{page.index}-{i}-{j}",
                    )
                )

            # ── Année : près de la ligne, du côté opposé à la pochette ──
            if point.year_label:
                if point.above:
                    yy = style.line_y + style.year_offset + style.size_year
                else:
                    yy = style.line_y - style.year_offset
                g_years.add(
                    text(
                        (TextRun(point.year_label, True),),
                        point.x,
                        yy,
                        style.size_year,
                        style.text_color,
                        ident=f"year-{page.index}-{i}",
                    )
                )

        # ── Cartouche ──
        g_cartouche.add(
            dwg.path(
                d=_rounded_rect_path(
                    style.cartouche_x,
                    style.cartouche_y,
                    style.cartouche_width,
                    style.cartouche_height,
                    style.cartouche_radius,
                    f,
                ),
                fill=style.cartouche_fill,
            )
        )
        cx = style.cartouche_x + style.cartouche_width / 2.0
        line_h = style.size_cartouche * style.label_line_height
        block = style.size_cartouche_value + line_h * len(style.cartouche_lines)
        top = style.cartouche_y + (style.cartouche_height - block) / 2.0
        g_cartouche.add(
            text(
                (TextRun(page.cartouche_text, True),),
                cx,
                top + style.size_cartouche_value,
                style.size_cartouche_value,
                style.cartouche_text_color,
                ident=f"cartouche-value-{page.index}",
            )
        )
        for j, line in enumerate(style.cartouche_lines):
            g_cartouche.add(
                text(
                    (TextRun(line, False),),
                    cx,
                    top + style.size_cartouche_value + (j + 1) * line_h,
                    style.size_cartouche,
                    style.cartouche_text_color,
                )
            )

        # Empilement explicite bas → haut (calques Illustrator).
        for group in (g_line, g_curve, g_discs, g_covers, g_labels, g_years, g_cartouche):
            g_page.add(group)
        dwg.add(g_page)

    svg = dwg.tostring()
    if path is not None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
    return svg
