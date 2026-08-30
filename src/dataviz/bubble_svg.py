"""Modèle calculé (`BubbleSpec`) + rendu SVG « layout brut » (svgwrite).

`BubbleSpec` est un modèle **pur et testable sans svgwrite** : positions et
diamètres des cercles artistes déjà résolus, une `EllipseSpec` par combinaison,
arêtes de collaboration. Il est produit par `bubble_prod.build_bubble_spec` et
consommé par `write_bubble_svg`.

Le SVG est un **aperçu de contrôle**, pas le livrable : la planche finale est
dessinée par `scripts/illustrator/bubble.jsx` à partir de `bubble_<kind>.json`
(mêmes coordonnées, dans la même zone fixe). Il reste pensé pour Illustrator :
**groupes-calques** empilés bas→top (`edges` < `ellipses` < `nodes` < `badges` <
`labels`) et **ids stables** (`node-<token>`, `badge-<token>`, `ellipse-<a>--<b>`,
`edge-<a>--<b>`) — le cercle donne directement la boîte de recadrage de la photo
d'artiste. Coordonnées arrondies (précision fixe) → sortie byte-identique entre
deux régénérations.
"""

import math
import re
from dataclasses import dataclass

import svgwrite

from src.dataviz.geometry import EllipseSpec


@dataclass(frozen=True)
class SvgStyle:
    """Paramètres de rendu (tailles, couleurs). Habillage brut, éditable ensuite."""

    # ── Zone de composition ──────────────────────────────────────────────────
    # Cadre FIXE, et non déduit du contenu : c'est ce qui garde l'échelle
    # comparable d'un album à l'autre (un album à 4 producteurs ne doit pas
    # produire des cercles plus gros qu'un album à 15). Reprend les cotes du
    # repère `zone-bubble` du template Illustrator.
    frame_width: float = 860.0
    frame_height: float = 520.0
    margin: float = 16.0  # marge intérieure entre le bord de la zone et le contenu
    # Cercles artistes : bornes de l'échelle de participation (DIAMÈTRE en px).
    node_size_min: float = 78.0
    node_size_max: float = 150.0
    # Badge (compteur de morceaux) ancré au milieu de l'arête basse du cercle.
    badge_size: float = 22.0
    badge_corner_radius: float = 4.0
    badge_font_size: float = 11.0
    # Nom d'artiste, centré dans le cercle. La taille suit le DIAMÈTRE :
    # `node_size_max` porte `font_size` en entier, un cercle plus petit reçoit
    # la même taille au prorata, jamais sous `font_size_min` (sinon illisible).
    font_size: float = 25.0
    # Plancher CALÉ sur la taille des titres de morceaux : un nom d'artiste ne
    # doit jamais devenir plus petit qu'un titre, la hiérarchie s'inverserait.
    font_size_min: float = 20.0
    line_height_ratio: float = 1.08  # interligne des noms multi-lignes (× font_size)
    font_family: str = "Montserrat, Arial, sans-serif"
    font_bold: str = "Montserrat-Bold"  # nom PostScript, pour Illustrator
    font_medium: str = "Montserrat-Medium"
    uppercase_names: bool = True  # noms d'artistes en MAJUSCULES (choix utilisateur)
    # Photo d'artiste : recadrée dans le cercle CÔTÉ ILLUSTRATOR seulement
    # (l'aperçu SVG ne place aucune image), voilée pour que le nom reste lisible.
    photo_overlay_color: str = "#333030"
    photo_overlay_opacity: float = 0.33
    # Légende d'ellipse (titres des morceaux si peu nombreux, sinon « XX morceaux »).
    label_track_threshold: int = 3  # au-delà : « N morceaux » au lieu des titres
    ellipse_label_font_size: float = 20.0
    ellipse_label_color: str = "#333030"
    # Le titre est posé SUR l'ellipse (texte curviligne) : `gap` est l'écart
    # entre le tracé et la ligne de base du texte, qui le tient à l'extérieur —
    # sinon le trait barre les lettres.
    ellipse_label_gap: float = 9.0
    ellipse_label_separator: str = " · "  # entre deux titres d'une même ellipse
    # Ellipses (une par combinaison de producteurs).
    min_axis_ratio: float = 0.35  # borne l'aplatissement (duo / quasi-colinéaire)
    ellipse_margin: float = 10.0  # marge ajoutée au rayon des cercles
    # Anti-chevauchement des cercles (passe post-layout, déterministe).
    overlap_gap: float = 14.0  # espace minimal entre deux cercles (aère le centre)
    overlap_iterations: int = 400
    # Canevas.
    canvas_scale: float = 190.0  # layout spring (~[-1,1]) → px
    hub_clearance: float = 0.8  # facteur du plancher de rayon feuille↔hub (éloigne du hub)
    radial_fill: float = 0.85  # remplissage : les feuilles s'étirent vers le bord de la zone
    radial_fill_islands: float = 0.62  # idem, quand des îlots doivent tenir dans les coins
    main_component_scale: float = 1.0  # zoom de la composante principale (hub)
    component_gap: float = 22.0  # écart initial hub↔îlots (avant calage aux coins)
    # Cadre : il matérialise EXACTEMENT la zone dans l'aperçu — ce qui déborde
    # se voit d'un coup d'œil (rien n'est mis à l'échelle pour rentrer).
    draw_frame: bool = True
    island_corner_pad: float = 12.0  # écart entre un îlot et le coin intérieur du cadre
    frame_stroke: str = "#C9C9C9"
    frame_stroke_width: float = 1.0
    frame_fill: str = "none"
    coord_precision: int = 2  # décimales des coordonnées (déterminisme)
    # Couleurs.
    draw_edges: bool = False  # traits producteur↔producteur (les bulles suffisent)
    edge_color: str = "#B0B0B0"
    edge_width: float = 1.0
    ellipse_fill: str = "none"
    ellipse_stroke: str = "#c98684"
    ellipse_stroke_width: float = 5.0
    node_fill: str = "#333030"
    node_stroke: str = "none"
    node_stroke_width: float = 0.0
    label_color: str = "#FFFFFF"
    badge_fill: str = "#c98684"
    badge_text_color: str = "#FFFFFF"


@dataclass(frozen=True)
class NodeSpec:
    """Un artiste : centre (x, y), DIAMÈTRE `size`, compteur `track_count`.

    `label_font_size` est résolu ICI (dans le spec) et non à l'affichage : le
    SVG d'aperçu et la planche Illustrator doivent poser exactement la même
    taille, sans que chacun refasse le calcul de son côté.
    """

    key: str
    display: str
    x: float
    y: float
    size: float
    track_count: int
    label_font_size: float


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
    « N morceaux »). Le texte est **curviligne, posé sur l'ellipse elle-même** :
    `label_t` donne le paramètre (degrés) de la pointe où il est centré, et
    `label_sweep` le sens de parcours retenu pour qu'il se lise à l'endroit.
    `track_count` = nb de morceaux.
    """

    member_keys: tuple[str, ...]
    ellipse: EllipseSpec
    label_lines: tuple[str, ...]
    label_t: float
    label_sweep: int
    track_count: int


@dataclass(frozen=True)
class BubbleSpec:
    """Modèle complet prêt à rendre : dimensions + nœuds + arêtes + combinaisons.

    `width`/`height` valent la zone FIXE (`style.frame_width/height`).
    `frame` = `(x, y, w, h)` du cadre, ou `None` si désactivé.

    `overflow` = `(dx, dy)` de dépassement de la zone, ou `None` si le contenu
    tient. Le dessin n'est JAMAIS mis à l'échelle pour rentrer (l'échelle doit
    rester comparable entre albums) : ce qui dépasse dépasse, et se rattrape
    dans Illustrator — mais l'appelant doit pouvoir le DIRE.
    """

    width: float
    height: float
    nodes: tuple[NodeSpec, ...]
    edges: tuple[EdgeSpec, ...]
    groups: tuple[GroupShape, ...]
    style: SvgStyle
    frame: tuple[float, float, float, float] | None = None
    overflow: tuple[float, float] | None = None


def id_token(key: str) -> str:
    """Slug id-safe (NCName) dérivé d'une identity_key : `square-<token>` ciblable JSX.

    Ex. `"kalim"` → `"kalim"`, `"lucci' (fra)"` → `"lucci-fra"`. Collisions
    théoriquement possibles mais négligeables à l'échelle d'un album.
    """
    token = re.sub(r"[^a-z0-9]+", "-", key.casefold()).strip("-")
    return token or "x"


def name_lines(name: str) -> tuple[str, ...]:
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


# Échantillonnage du chemin porteur du texte curviligne. 180 segments : l'écart
# à la vraie ellipse est de l'ordre du millième de pixel, invisible, et la
# longueur d'une polyligne est SANS AMBIGUÏTÉ — ce qui permet de placer le texte
# au point exact voulu, là où un `startOffset` en pourcentage d'un arc dépend de
# la façon dont le moteur mesure le chemin.
_LABEL_PATH_SAMPLES = 180


def _label_path(ellipse: EllipseSpec, t_apex: float, sweep: int, prec: int) -> tuple[str, float]:
    """Chemin porteur d'une légende + l'offset où centrer le texte.

    Le chemin part d'un demi-tour AVANT le sommet et finit un demi-tour après,
    dans le sens de lecture retenu ; l'offset renvoyé est la longueur parcourue
    jusqu'au sommet. Le texte, ancré au milieu, s'y centre donc exactement.
    """
    step = 1.0 if sweep else -1.0
    points = []
    for i in range(_LABEL_PATH_SAMPLES + 1):
        t = t_apex + step * (-180.0 + 360.0 * i / _LABEL_PATH_SAMPLES)
        points.append(ellipse.point_at(t))
    # Longueurs calculées sur les points ARRONDIS, ceux qui partiront dans le
    # fichier : sinon l'offset décrirait un chemin légèrement différent.
    rounded = [(round(x, prec), round(y, prec)) for x, y in points]
    middle = _LABEL_PATH_SAMPLES // 2
    offset = 0.0
    for i in range(middle):
        offset += math.hypot(rounded[i + 1][0] - rounded[i][0], rounded[i + 1][1] - rounded[i][1])
    d = "M " + " L ".join(f"{_fmt(x, prec)} {_fmt(y, prec)}" for x, y in points)
    return d, offset


def _ellipse_path_d(ellipse: EllipseSpec, t_start: float, sweep: int, prec: int) -> str:
    """Contour d'ellipse en commandes de chemin, rotation CUITE dans les points.

    Démarre au paramètre `t_start` (degrés) et parcourt le tour complet en deux
    arcs (une commande `A` ne peut pas décrire un tour entier : départ et arrivée
    confondus seraient ambigus). `sweep` = sens de parcours, qui détermine le
    sens de lecture d'un texte posé dessus.
    """
    mid = t_start + (180.0 if sweep else -180.0)
    p0 = ellipse.point_at(t_start)
    p1 = ellipse.point_at(mid)
    rx, ry = _fmt(ellipse.rx, prec), _fmt(ellipse.ry, prec)
    rot = _fmt(ellipse.angle, prec)
    arc = f"A {rx} {ry} {rot} 0 {sweep}"
    return (
        f"M {_fmt(p0[0], prec)} {_fmt(p0[1], prec)} "
        f"{arc} {_fmt(p1[0], prec)} {_fmt(p1[1], prec)} "
        f"{arc} {_fmt(p0[0], prec)} {_fmt(p0[1], prec)} Z"
    )


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
    g_nodes = dwg.g(id="nodes")
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
        # Tracé en <path> et non en <ellipse> : c'est ce qui permet d'y poser du
        # texte curviligne (<textPath> exige un <path>). La rotation est CUITE
        # dans les points plutôt que portée par un attribut `transform` — un
        # `transform` sur le chemin référencé ne s'applique pas au texte qui le
        # suit, la légende partirait ailleurs que son ovale.
        outline = dwg.path(
            d=_ellipse_path_d(el, 0.0, 1, prec),
            fill=style.ellipse_fill,
            stroke=style.ellipse_stroke,
            stroke_width=f(style.ellipse_stroke_width),
            id=f"ellipse-{set_token}",
        )
        g_ellipses.add(outline)

        text_content = style.ellipse_label_separator.join(gs.label_lines)
        if not text_content:
            continue
        # Chemin PORTEUR du texte : la même ellipse, écartée vers l'extérieur
        # (sinon le trait barre les lettres) et démarrée à l'ANTIPODE de la
        # pointe visée. L'ellipse ayant une symétrie centrale, la moitié de son
        # périmètre tombe exactement sur l'antipode : un texte centré à
        # `startOffset="50%"` atterrit donc pile sur la pointe, sans avoir à
        # intégrer la longueur d'arc.
        offset = style.ellipse_stroke_width / 2.0 + style.ellipse_label_gap
        path_id = f"ellipse-labelpath-{set_token}"
        d, start_offset = _label_path(el.inflated(offset), gs.label_t, gs.label_sweep, prec)
        g_ellipse_labels.add(dwg.path(d=d, fill="none", stroke="none", id=path_id))
        text = dwg.text(
            "",
            font_size=f(style.ellipse_label_font_size),
            font_family=style.font_family,
            font_weight="500",
            fill=style.ellipse_label_color,
            id=f"ellipse-label-{set_token}",
        )
        text.add(
            svgwrite.text.TextPath(
                path=f"#{path_id}",
                text=text_content,
                startOffset=f(start_offset),
                text_anchor="middle",
            )
        )
        g_ellipse_labels.add(text)

    # ── Calques 3-5 : cercles, badges, libellés ──
    for n in spec.nodes:
        token = id_token(n.key)
        half = n.size / 2.0
        # Cercle plein : c'est le REPLI (aucune photo). Côté Illustrator, le
        # même cercle sert de boîte de recadrage à la photo d'artiste.
        g_nodes.add(
            dwg.circle(
                center=(f(n.x), f(n.y)),
                r=f(half),
                fill=style.node_fill,
                stroke=style.node_stroke,
                stroke_width=f(style.node_stroke_width),
                id=f"node-{token}",
            )
        )
        # Badge : petit carré arrondi à cheval sur le bas du cercle.
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
        # Nom CENTRÉ dans le cercle, par-dessus la photo voilée. Multi-lignes :
        # un mot par ligne, initiales collées (cf. `name_lines`) ; le bloc est
        # centré verticalement, donc décalé d'une demi-hauteur vers le haut.
        # Centrage vertical MANUEL (+0.35 em) : `dominant-baseline` est ignoré
        # par Illustrator.
        name = n.display.upper() if style.uppercase_names else n.display
        lines = name_lines(name)
        line_height = n.label_font_size * style.line_height_ratio
        label_group = dwg.g(id=f"label-{token}")
        base_y = n.y - (len(lines) - 1) * line_height / 2.0 + n.label_font_size * 0.35
        for i, line in enumerate(lines):
            label_group.add(
                dwg.text(
                    line,
                    insert=(f(n.x), f(base_y + i * line_height)),
                    text_anchor="middle",
                    font_size=f(n.label_font_size),
                    font_family=style.font_family,
                    font_weight="bold",
                    fill=style.label_color,
                )
            )
        g_labels.add(label_group)

    # Ordre d'empilement : les légendes d'ellipses passent AU-DESSUS des cercles
    # (le « N solo » du hub est écrit DANS son cercle — dessous, il serait
    # masqué par le disque plein).
    for group in (
        g_frame,
        g_edges,
        g_ellipses,
        g_nodes,
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
