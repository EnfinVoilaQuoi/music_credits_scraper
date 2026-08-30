"""Modèle calculé (`StructureSpec`) + rendu SVG de prévisualisation (svgwrite).

Une ligne par morceau, en **4 colonnes** calées sur le template Illustrator de la
DA : numéro, titre (+ invités), barre de structure normalisée à 100 % de la durée,
et rectangle de durée dont la longueur encode la durée absolue.

Le SVG est une **prévisualisation fidèle** : mêmes couleurs, même typographie,
même géométrie que la planche finale — de quoi contrôler un album sans ouvrir
Illustrator. La planche de production, elle, est construite par
`scripts/illustrator/structure.jsx` à partir de `structure.json`.

`StructureSpec` est un modèle **pur et testable sans svgwrite**, produit par
`structure.build_structure_spec` et consommé par `write_structure_svg` comme par
`structure_json.build_payload`.

Calques empilés bas→top (`durations` < `segments` < `labels`) et ids stables
(`segment-<i>-<j>-<kind>`, `duration-<i>`, `label-title-<i>`…). Coordonnées
arrondies à précision fixe → sortie byte-identique entre deux régénérations.
"""

from dataclasses import dataclass, field

import svgwrite

from src.dataviz.bubble_svg import _fmt

# Types de section (ordre canonique = ordre de rendu de la ligne d'agrégat).
SECTION_KINDS: tuple[str, ...] = ("intro_outro", "couplet", "refrain", "pont")

# Durée de référence du rectangle de durée : au-delà, il déborde de sa colonne.
DURATION_REFERENCE_SECONDS = 300  # 5:00


def _default_colors() -> dict[str, str]:
    """Palette de la DA (fournie par l'utilisateur, 2026-08-24)."""
    return {
        "intro_outro": "#a93f3b",  # rouge
        "couplet": "#293276",  # bleu
        "refrain": "#17722e",  # vert
        "pont": "#dfaa19",  # jaune
    }


@dataclass(frozen=True)
class StructureStyle:
    """Géométrie et habillage, calqués sur le template Illustrator.

    Les cotes sont celles du template (850 × 1150 px, marge gauche 25, gouttières
    de 15). Elles pilotent la prévisualisation SVG ET voyagent dans le JSON ; côté
    Illustrator ce sont les **repères nommés** du template qui font foi.
    """

    # ── Colonnes (x, largeur) ──
    margin_left: float = 25.0
    gutter: float = 15.0
    col_index_width: float = 50.0
    col_title_width: float = 250.0
    col_bar_width: float = 400.0
    col_duration_width: float = 60.0
    zone_width: float = 850.0
    # ── Vertical ──
    zone_height: float = 1150.0
    padding_top: float = 110.0
    padding_bottom: float = 27.0
    row_height_max: float = 45.0  # plafond : évite les lignes étirées sur album court
    gap_ratio: float = 0.25  # interligne = ratio × hauteur de ligne
    project_gap: float = 55.0  # écart avant la ligne « Structure du Projet »
    # ── Barre de structure ──
    corner_radius: float = 5.5
    separator_gap: float = 4.0  # vide entre deux blocs de MÊME couleur qui se suivent
    # ── Rectangle de durée (passe sous la barre, déborde à droite) ──
    duration_overlap: float = 10.0
    duration_width_min: float = 18.0  # bord droit juste après le chiffre des minutes
    duration_fill: str = "#ffffff"
    duration_opacity: float = 0.5
    # ── Typographie (Montserrat) ──
    font_family: str = "Montserrat, sans-serif"
    font_light: str = "Montserrat-Light"  # nom PostScript, pour Illustrator
    font_semibold: str = "Montserrat-SemiBold"
    weight_light: str = "300"
    weight_semibold: str = "600"
    size_index: float = 22.0
    size_title: float = 22.0
    size_feat: float = 17.0
    size_duration: float = 22.0
    title_line_gap: float = 0.0  # entre la ligne titre et la ligne invités (resserré)
    text_color: str = "#56201d"
    baseline_ratio: float = 0.35  # centrage vertical manuel (dominant-baseline ignoré)
    # ── Prévisualisation seulement ──
    # Approximation du fond de la DA, pour que les vides entre blocs de même
    # couleur se lisent. NE PART PAS dans le JSON : Illustrator a le vrai template.
    background_fill: str = "#c4918c"
    # ── Couleurs des sections ──
    colors: dict[str, str] = field(default_factory=_default_colors)
    coord_precision: int = 2  # décimales des coordonnées (déterminisme)

    # ── Positions dérivées ──
    @property
    def col_index_x(self) -> float:
        return self.margin_left

    @property
    def col_title_x(self) -> float:
        return self.col_index_x + self.col_index_width + self.gutter

    @property
    def col_bar_x(self) -> float:
        return self.col_title_x + self.col_title_width + self.gutter

    @property
    def col_duration_x(self) -> float:
        return self.col_bar_x + self.col_bar_width + self.gutter

    @property
    def duration_width_max(self) -> float:
        return self.col_duration_width


@dataclass(frozen=True)
class SegmentSpec:
    """Un segment de barre : un type de section et sa part de la durée."""

    kind: str
    start: float  # secondes
    end: float  # secondes
    ratio: float  # (end - start) / duration


@dataclass(frozen=True)
class TrackRowSpec:
    """Une ligne : un morceau (ou l'agrégat de l'album) et ses segments.

    `index` = rang 1-based dans la tracklist ; `0` pour la ligne d'agrégat.
    `title_paren` = parenthèse finale détachée (« (Interlude) »), rendue en Light.
    `duration_ratio` = position sur l'échelle des durées de l'album, **non bornée** :
    au-delà de 1 le rectangle de durée déborde de sa colonne (morceau > 5 min).
    """

    index: int
    title: str
    duration: int
    bpm: int | None
    segments: tuple[SegmentSpec, ...]
    duration_ratio: float
    title_paren: str | None = None
    feats: tuple[str, ...] = ()

    def duration_width(self, style: StructureStyle) -> float:
        """Largeur du rect « durée » en px, dérivée du ratio."""
        span = style.duration_width_max - style.duration_width_min
        return style.duration_width_min + self.duration_ratio * span


@dataclass(frozen=True)
class RowLayout:
    """Répartition verticale résolue : hauteur de ligne, interligne, ordonnées."""

    row_height: float
    gap: float
    row_tops: tuple[float, ...]
    project_top: float | None


@dataclass(frozen=True)
class StructureSpec:
    """Modèle complet prêt à rendre : dimensions + lignes + agrégat."""

    width: float
    height: float
    rows: tuple[TrackRowSpec, ...]
    project_row: TrackRowSpec | None
    style: StructureStyle


def format_duration(seconds: float) -> str:
    """Durée en `m:ss`, SANS bascule en heures.

    La ligne d'agrégat dépasse l'heure mais reste lue en minutes (« 62:00 »,
    pas « 1:02:00 ») : c'est la durée totale d'un projet, pas une horloge.
    """
    total = int(round(seconds))
    m, s = divmod(total, 60)
    return f"{m}:{s:02d}"


def compute_layout(row_count: int, style: StructureStyle, *, with_project: bool) -> RowLayout:
    """Répartit `row_count` lignes (+ l'agrégat) dans la hauteur utile de la zone.

    La hauteur de ligne s'adapte au nombre de morceaux mais reste **plafonnée** :
    sous une dizaine de titres, la zone n'est volontairement pas remplie (des
    lignes étirées seraient laides) et le bloc reste calé en haut.
    """
    usable = style.zone_height - style.padding_top - style.padding_bottom
    slots = row_count + (1 if with_project else 0)
    extra = style.project_gap if with_project else 0.0
    # usable = h × slots + g × (slots - 1 - with_project) + extra, avec g = ratio × h
    inner_gaps = max(0, row_count - 1) + (0 if with_project else 0)
    denom = slots + style.gap_ratio * inner_gaps
    row_height = min(style.row_height_max, (usable - extra) / denom if denom else usable)
    gap = style.gap_ratio * row_height

    tops = tuple(style.padding_top + i * (row_height + gap) for i in range(row_count))
    project_top = None
    if with_project:
        last_bottom = (tops[-1] + row_height) if tops else style.padding_top
        project_top = last_bottom + style.project_gap
    return RowLayout(row_height=row_height, gap=gap, row_tops=tops, project_top=project_top)


def _segment_bounds(
    row: TrackRowSpec, style: StructureStyle
) -> list[tuple[SegmentSpec, float, float]]:
    """Bornes x de chaque segment, avec un VIDE entre deux blocs de même couleur.

    Deux sections explicites consécutives du même type (deux couplets d'affilée,
    ou l'outro d'une partie suivie de l'intro de la suivante sur un 2-en-1) se
    confondraient en un seul aplat : on les sépare en rognant chaque côté de la
    frontière. Le fond de la planche transparaît — ce qui marche sur n'importe
    quel fond, contrairement à un filet de couleur fixe.
    """
    out: list[tuple[SegmentSpec, float, float]] = []
    cursor = style.col_bar_x
    half = style.separator_gap / 2.0
    for j, seg in enumerate(row.segments):
        x0, x1 = cursor, cursor + style.col_bar_width * seg.ratio
        cursor = x1
        if j > 0 and row.segments[j - 1].kind == seg.kind:
            x0 += half
        if j + 1 < len(row.segments) and row.segments[j + 1].kind == seg.kind:
            x1 -= half
        out.append((seg, x0, max(x0, x1)))
    return out


def _rounded_path(
    x0: float, x1: float, y: float, h: float, r: float, f, *, left: bool, right: bool
) -> str:
    """Rectangle dont SEULS les côtés demandés sont arrondis (rayon `r`).

    Sert deux besoins : le rectangle de durée (arrondi à droite seulement, ses
    coins gauches passant sous la barre) et les segments de barre — le premier
    est arrondi à gauche, le dernier à droite, les intermédiaires restent droits.
    Cette approche remplace un `clipPath` : Illustrator avertit à l'ouverture d'un
    SVG écrêté (« l'écrêtage sera perdu à la réexportation au format Tiny »).
    """
    cap = min(r, h / 2.0, max(0.0, x1 - x0) / 2.0)
    rl, rr = (cap if left else 0.0), (cap if right else 0.0)
    y1 = y + h
    parts = [f"M{f(x0 + rl)},{f(y)}", f"H{f(x1 - rr)}"]
    if rr:
        parts.append(f"A{f(rr)},{f(rr)} 0 0 1 {f(x1)},{f(y + rr)}")
    parts.append(f"V{f(y1 - rr)}")
    if rr:
        parts.append(f"A{f(rr)},{f(rr)} 0 0 1 {f(x1 - rr)},{f(y1)}")
    parts.append(f"H{f(x0 + rl)}")
    if rl:
        parts.append(f"A{f(rl)},{f(rl)} 0 0 1 {f(x0)},{f(y1 - rl)}")
    parts.append(f"V{f(y + rl)}")
    if rl:
        parts.append(f"A{f(rl)},{f(rl)} 0 0 1 {f(x0 + rl)},{f(y)}")
    parts.append("Z")
    return " ".join(parts)


def _feat_runs(feats: tuple[str, ...], style: StructureStyle) -> list[tuple[str, str]]:
    """Ligne d'invités découpée en (texte, graisse) : « feat A, B & C »."""
    runs: list[tuple[str, str]] = [("feat ", style.weight_light)]
    for i, name in enumerate(feats):
        if i:
            runs.append((" & " if i == len(feats) - 1 else ", ", style.weight_light))
        runs.append((name, style.weight_semibold))
    return runs


def write_structure_svg(spec: StructureSpec, path=None) -> str:
    """Sérialise `spec` en SVG. Écrit dans `path` si fourni ; renvoie la chaîne."""
    style = spec.style
    prec = style.coord_precision

    def f(v):
        return _fmt(v, prec)

    dwg = svgwrite.Drawing(size=(f(spec.width), f(spec.height)), profile="full", debug=False)
    dwg.attribs["viewBox"] = f"0 0 {f(spec.width)} {f(spec.height)}"
    dwg.add(dwg.rect(insert=(0, 0), size=("100%", "100%"), fill=style.background_fill))

    g_durations = dwg.g(id="durations")
    g_segments = dwg.g(id="segments")
    g_labels = dwg.g(id="labels")

    layout = compute_layout(len(spec.rows), style, with_project=spec.project_row is not None)
    h = layout.row_height

    def text(content, x, y, size, weight, anchor="start", ident=None):
        node = dwg.text(
            content,
            insert=(f(x), f(y)),
            font_size=f(size),
            font_family=style.font_family,
            font_weight=weight,
            fill=style.text_color,
            **({"text_anchor": anchor} if anchor != "start" else {}),
            **({"id": ident} if ident else {}),
        )
        node.attribs["xml:space"] = "preserve"
        return node

    def draw_row(row: TrackRowSpec, y: float, suffix: str):
        mid = y + h / 2.0
        bar_right = style.col_bar_x + style.col_bar_width

        # ── Rect « durée » : sous la barre, débordant à droite ──
        # Pas sur la ligne d'agrégat : sa « durée » est celle de l'album entier,
        # hors de l'échelle des morceaux — un rectangle y serait trompeur.
        if row.index:
            g_durations.add(
                dwg.path(
                    d=_rounded_path(
                        bar_right - style.duration_overlap,
                        style.col_duration_x + row.duration_width(style),
                        y,
                        h,
                        style.corner_radius,
                        f,
                        left=False,
                        right=True,
                    ),
                    fill=style.duration_fill,
                    fill_opacity=f(style.duration_opacity),
                    id=f"duration-{suffix}",
                )
            )

        # ── Segments : seuls les bouts de la barre sont arrondis ──
        bounds = _segment_bounds(row, style)
        bar = dwg.g(id=f"row-{suffix}")
        for j, (seg, x0, x1) in enumerate(bounds):
            bar.add(
                dwg.path(
                    d=_rounded_path(
                        x0,
                        x1,
                        y,
                        h,
                        style.corner_radius,
                        f,
                        left=j == 0,
                        right=j == len(bounds) - 1,
                    ),
                    fill=style.colors.get(seg.kind, style.background_fill),
                    id=f"segment-{suffix}-{j}-{seg.kind}",
                )
            )
        g_segments.add(bar)

        # ── Colonne 1 : numéro, centré ──
        if row.index:
            g_labels.add(
                text(
                    f"{row.index:02d}",
                    style.col_index_x + style.col_index_width / 2.0,
                    mid + style.size_index * style.baseline_ratio,
                    style.size_index,
                    style.weight_light,
                    anchor="middle",
                    ident=f"label-index-{suffix}",
                )
            )

        # ── Colonne 2 : titre (+ parenthèse en Light) et invités ──
        title_y = mid + style.size_title * style.baseline_ratio
        if row.feats:
            title_y -= (style.size_feat + style.title_line_gap) / 2.0
        node = text("", style.col_title_x, title_y, style.size_title, style.weight_semibold)
        node.attribs["id"] = f"label-title-{suffix}"
        node.add(dwg.tspan(row.title, font_weight=style.weight_semibold))
        if row.title_paren:
            node.add(dwg.tspan(f" {row.title_paren}", font_weight=style.weight_light))
        g_labels.add(node)

        if row.feats:
            feat_y = title_y + style.size_feat + style.title_line_gap
            feat_node = text("", style.col_title_x, feat_y, style.size_feat, style.weight_light)
            feat_node.attribs["id"] = f"label-feat-{suffix}"
            for content, weight in _feat_runs(row.feats, style):
                feat_node.add(dwg.tspan(content, font_weight=weight))
            g_labels.add(feat_node)

        # ── Colonne 4 : durée, PAR-DESSUS son rectangle ──
        g_labels.add(
            text(
                format_duration(row.duration),
                style.col_duration_x,
                mid + style.size_duration * style.baseline_ratio,
                style.size_duration,
                style.weight_semibold,
                ident=f"label-duration-{suffix}",
            )
        )

    for row, top in zip(spec.rows, layout.row_tops, strict=True):
        draw_row(row, top, str(row.index))
    if spec.project_row is not None and layout.project_top is not None:
        draw_row(spec.project_row, layout.project_top, "project")

    # Empilement explicite bas → haut (calques Illustrator).
    for group in (g_durations, g_segments, g_labels):
        dwg.add(group)

    svg = dwg.tostring()
    if path is not None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
    return svg
