"""Modèle calculé (`BubbleSpec`) + rendu SVG « layout brut » (svgwrite).

`BubbleSpec` est un modèle **pur et testable sans svgwrite** : positions et
tailles des carrés producteurs déjà résolues, une `EllipseSpec` par morceau,
arêtes de collaboration. Il est produit par `bubble_prod.build_bubble_spec` et
consommé par `write_bubble_svg`.

Le SVG est pensé pour Illustrator : **groupes-calques** empilés bas→top
(`edges` < `ellipses` < `squares` < `badges` < `labels`) et **ids stables**
(`square-<token>`, `badge-<token>`, `ellipse-track-<id>`, `edge-<a>--<b>`) que de
futurs scripts JSX pourront cibler (le rect donne directement la boîte de
recadrage d'une photo de producteur). Coordonnées arrondies (précision fixe) →
sortie byte-identique entre deux régénérations.
"""

import math
import re
from dataclasses import dataclass

import svgwrite

from src.dataviz.geometry import EllipseSpec


@dataclass(frozen=True)
class SvgStyle:
    """Paramètres de rendu (tailles, couleurs). Habillage brut, éditable ensuite."""

    # Carrés producteurs : bornes de l'échelle de participation (côté en px).
    # Très grands (post Insta, lisible sur mobile) : photo + nom y tiendront.
    square_size_min: float = 240.0
    square_size_max: float = 400.0
    corner_radius: float = 16.0
    # Badge (compteur de morceaux) ancré au milieu de l'arête basse du carré.
    badge_size: float = 58.0
    badge_corner_radius: float = 9.0
    badge_font_size: float = 29.0
    # Libellés (noms de producteurs).
    font_size: float = 34.0
    font_family: str = "Arial, sans-serif"
    name_dy_ratio: float = -0.28  # position verticale du nom dans le carré (-0.5=haut)
    name_line_height: float = 38.0  # interligne des noms multi-lignes
    uppercase_names: bool = True  # noms d'artistes en MAJUSCULES (choix utilisateur)
    # Légende d'ellipse (titres des morceaux si peu nombreux, sinon « XX morceaux »).
    label_track_threshold: int = 3  # au-delà : « N morceaux » au lieu des titres
    ellipse_label_font_size: float = 31.0
    ellipse_label_color: str = "#33475B"
    ellipse_label_gap: float = 18.0  # écart entre le bout de l'ellipse et sa légende
    ellipse_label_line_height: float = 37.0
    ellipse_label_max_angle: float = 30.0  # rotation max du texte (± degrés, reste lisible)
    # Ellipses (une par combinaison de producteurs).
    min_axis_ratio: float = 0.35  # borne l'aplatissement (duo / quasi-colinéaire)
    ellipse_margin: float = 22.0  # marge ajoutée à la demi-diagonale des carrés
    # Anti-chevauchement des carrés (passe post-layout, déterministe).
    overlap_gap: float = 48.0  # espace minimal entre deux carrés (aère le centre)
    overlap_iterations: int = 400
    # Canevas.
    canvas_scale: float = 560.0  # layout spring (~[-1,1]) → px
    hub_clearance: float = 0.8  # facteur du plancher de rayon feuille↔hub (éloigne du hub)
    radial_fill: float = 0.85  # remplissage : les feuilles s'étirent vers le bord du cadre
    main_component_scale: float = 1.0  # zoom de la composante principale (hub)
    component_gap: float = 72.0  # écart initial hub↔îlots (avant calage aux coins)
    margin: float = 48.0  # marge intérieure entre le cadre et le contenu
    # Cadre (les îlots se calent dans ses coins, le hub occupe le centre).
    draw_frame: bool = True
    frame_inset: float = 20.0  # écart entre le bord du viewBox et le cadre
    island_corner_pad: float = 36.0  # écart entre un îlot et le coin intérieur du cadre
    frame_stroke: str = "#222222"
    frame_stroke_width: float = 2.0
    frame_fill: str = "none"
    coord_precision: int = 2  # décimales des coordonnées (déterminisme)
    # Couleurs.
    draw_edges: bool = False  # traits producteur↔producteur (les bulles suffisent)
    edge_color: str = "#B0B0B0"
    edge_width: float = 1.0
    ellipse_fill: str = "#EAF2FB"
    ellipse_opacity: float = 0.55
    ellipse_stroke: str = "#5B8DEF"
    ellipse_stroke_width: float = 2.0
    square_fill: str = "#FFFFFF"
    square_stroke: str = "#222222"
    square_stroke_width: float = 2.2
    label_color: str = "#111111"
    badge_fill: str = "#222222"
    badge_text_color: str = "#FFFFFF"


@dataclass(frozen=True)
class NodeSpec:
    """Un producteur : centre (x, y), côté `size`, compteur `track_count`."""

    key: str
    display: str
    x: float
    y: float
    size: float
    track_count: int


@dataclass(frozen=True)
class EdgeSpec:
    """Une arête de collaboration (segment entre deux centres de carrés)."""

    a: str  # identity_key
    b: str
    weight: int
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True)
class GroupShape:
    """Une combinaison de producteurs : ellipse englobante + légende ancrée.

    `member_keys` = les producteurs de la combinaison (sert à l'id stable).
    `label_lines` = ce qui s'affiche (titres si peu de morceaux, sinon
    « N morceaux »), aligné **le long du grand axe de l'ellipse** (`label_angle`,
    degrés, normalisé pour rester lisible), ancré à la pointe extérieure de
    l'ellipse. `track_count` = nb de morceaux.
    """

    member_keys: tuple[str, ...]
    ellipse: EllipseSpec
    label_lines: tuple[str, ...]
    label_x: float
    label_y: float
    label_angle: float
    track_count: int


@dataclass(frozen=True)
class BubbleSpec:
    """Modèle complet prêt à rendre : dimensions + nœuds + arêtes + combinaisons.

    `frame` = `(x, y, w, h)` du cadre carré, ou `None` si désactivé.
    """

    width: float
    height: float
    nodes: tuple[NodeSpec, ...]
    edges: tuple[EdgeSpec, ...]
    groups: tuple[GroupShape, ...]
    style: SvgStyle
    frame: tuple[float, float, float, float] | None = None


def id_token(key: str) -> str:
    """Slug id-safe (NCName) dérivé d'une identity_key : `square-<token>` ciblable JSX.

    Ex. `"kalim"` → `"kalim"`, `"lucci' (fra)"` → `"lucci-fra"`. Collisions
    théoriquement possibles mais négligeables à l'échelle d'un album.
    """
    token = re.sub(r"[^a-z0-9]+", "-", key.casefold()).strip("-")
    return token or "x"


def _name_lines(name: str) -> tuple[str, ...]:
    """Découpe un nom en lignes : un mot par ligne dans le carré.

    Les morceaux très courts (initiales « D. », particules) restent collés au
    mot voisin : « Price D. » tient sur UNE ligne (une lettre isolée serait
    bizarre), « Lewis Amber » passe sur deux.
    """
    lines: list[str] = []
    for part in name.split(" "):
        if lines and (len(part) <= 2 or len(lines[-1]) <= 2):
            lines[-1] += " " + part
        else:
            lines.append(part)
    return tuple(lines)


def _fmt(value: float, prec: int) -> str:
    """Formate une coordonnée avec une précision fixe (byte-identique, pas de -0)."""
    rounded = round(float(value), prec)
    if rounded == 0:
        rounded = 0.0  # écrase les -0.0
    return f"{rounded:.{prec}f}"


def write_bubble_svg(spec: BubbleSpec, path=None) -> str:
    """Sérialise `spec` en SVG. Écrit dans `path` si fourni ; renvoie la chaîne."""
    style = spec.style
    prec = style.coord_precision

    def f(v):
        return _fmt(v, prec)

    dwg = svgwrite.Drawing(size=(f(spec.width), f(spec.height)), profile="full", debug=False)
    dwg.attribs["viewBox"] = f"0 0 {f(spec.width)} {f(spec.height)}"

    g_frame = dwg.g(id="frame")
    g_edges = dwg.g(id="edges")
    g_ellipses = dwg.g(id="ellipses")
    g_ellipse_labels = dwg.g(id="ellipse-labels")
    g_squares = dwg.g(id="squares")
    g_badges = dwg.g(id="badges")
    g_labels = dwg.g(id="labels")

    # ── Calque 0 : cadre (les îlots se calent dans ses coins) ──
    if spec.frame is not None:
        fx, fy, fw, fh = spec.frame
        g_frame.add(
            dwg.rect(
                insert=(f(fx), f(fy)),
                size=(f(fw), f(fh)),
                fill=style.frame_fill,
                stroke=style.frame_stroke,
                stroke_width=f(style.frame_stroke_width),
                id="frame-border",
            )
        )

    # ── Calque 1 : arêtes de collaboration (optionnelles) ──
    if style.draw_edges:
        for e in spec.edges:
            g_edges.add(
                dwg.line(
                    start=(f(e.x1), f(e.y1)),
                    end=(f(e.x2), f(e.y2)),
                    stroke=style.edge_color,
                    stroke_width=f(style.edge_width),
                    id=f"edge-{id_token(e.a)}--{id_token(e.b)}",
                )
            )

    # ── Calques 2-3 : ellipses (une par combinaison de producteurs) + légendes ──
    for gs in spec.groups:
        el = gs.ellipse
        set_token = "--".join(id_token(k) for k in gs.member_keys)
        ellipse = dwg.ellipse(
            center=(f(el.cx), f(el.cy)),
            r=(f(el.rx), f(el.ry)),
            fill=style.ellipse_fill,
            fill_opacity=style.ellipse_opacity,
            stroke=style.ellipse_stroke,
            stroke_width=f(style.ellipse_stroke_width),
            id=f"ellipse-{set_token}",
        )
        # Rotation explicite (contrôle exact de la chaîne → byte-identique).
        ellipse.attribs["transform"] = f"rotate({f(el.angle)} {f(el.cx)} {f(el.cy)})"
        g_ellipses.add(ellipse)
        # Légende alignée le long du grand axe de l'ellipse, ancrée à la pointe
        # extérieure. Lignes multiples empilées perpendiculairement à l'axe.
        # Centrage vertical MANUEL (+0.35 em de baseline) : `dominant-baseline`
        # est ignoré par Illustrator et inégal selon les renderers.
        ra = math.radians(gs.label_angle)
        perp_x, perp_y = -math.sin(ra), math.cos(ra)
        # Empiler vers l'extérieur (à l'opposé du centre de l'ellipse).
        if perp_x * (gs.label_x - el.cx) + perp_y * (gs.label_y - el.cy) < 0:
            perp_x, perp_y = -perp_x, -perp_y
        for i, line in enumerate(gs.label_lines):
            lx = gs.label_x + perp_x * i * style.ellipse_label_line_height
            ly = gs.label_y + perp_y * i * style.ellipse_label_line_height
            ly += style.ellipse_label_font_size * 0.35
            text = dwg.text(
                line,
                insert=(f(lx), f(ly)),
                text_anchor="middle",
                font_size=f(style.ellipse_label_font_size),
                font_family=style.font_family,
                fill=style.ellipse_label_color,
                id=f"ellipse-label-{set_token}-{i}",
            )
            text.attribs["transform"] = f"rotate({f(gs.label_angle)} {f(lx)} {f(ly)})"
            g_ellipse_labels.add(text)

    # ── Calques 3-5 : carrés, badges, libellés ──
    for n in spec.nodes:
        token = id_token(n.key)
        half = n.size / 2.0
        # Carré centré, coins arrondis.
        g_squares.add(
            dwg.rect(
                insert=(f(n.x - half), f(n.y - half)),
                size=(f(n.size), f(n.size)),
                rx=f(style.corner_radius),
                ry=f(style.corner_radius),
                fill=style.square_fill,
                stroke=style.square_stroke,
                stroke_width=f(style.square_stroke_width),
                id=f"square-{token}",
            )
        )
        # Badge : petit carré arrondi à cheval sur le milieu de l'arête basse.
        bsize = style.badge_size
        bx = n.x - bsize / 2.0
        by = n.y + half - bsize / 2.0
        g_badges.add(
            dwg.rect(
                insert=(f(bx), f(by)),
                size=(f(bsize), f(bsize)),
                rx=f(style.badge_corner_radius),
                ry=f(style.badge_corner_radius),
                fill=style.badge_fill,
                id=f"badge-{token}",
            )
        )
        # Chiffre centré dans le badge : centrage vertical MANUEL (+0.35 em),
        # `dominant-baseline` n'étant pas fiable (ignoré par Illustrator).
        g_badges.add(
            dwg.text(
                str(n.track_count),
                insert=(
                    f(bx + bsize / 2.0),
                    f(by + bsize / 2.0 + style.badge_font_size * 0.35),
                ),
                text_anchor="middle",
                font_size=f(style.badge_font_size),
                font_family=style.font_family,
                fill=style.badge_text_color,
                id=f"badge-count-{token}",
            )
        )
        # Libellé (nom) dans le carré, vers le haut (la photo occupera le bas).
        # Multi-lignes : un mot par ligne, initiales collées (cf. _name_lines).
        name = n.display.upper() if style.uppercase_names else n.display
        label_group = dwg.g(id=f"label-{token}")
        base_y = n.y + style.name_dy_ratio * n.size + style.font_size * 0.35
        for i, line in enumerate(_name_lines(name)):
            label_group.add(
                dwg.text(
                    line,
                    insert=(f(n.x), f(base_y + i * style.name_line_height)),
                    text_anchor="middle",
                    font_size=f(style.font_size),
                    font_family=style.font_family,
                    fill=style.label_color,
                )
            )
        g_labels.add(label_group)

    # Ordre d'empilement : les légendes d'ellipses passent AU-DESSUS des carrés
    # (le « N solo » du hub est écrit DANS son carré — sous les carrés, il
    # serait masqué par le rect blanc).
    for group in (
        g_frame,
        g_edges,
        g_ellipses,
        g_squares,
        g_ellipse_labels,
        g_badges,
        g_labels,
    ):
        dwg.add(group)

    svg = dwg.tostring()
    if path is not None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
    return svg
