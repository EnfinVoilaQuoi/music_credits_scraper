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
    frame_width: float = 850.0
    frame_height: float = 600.0
    # Marge NULLE par défaut : la maquette a déjà la sienne autour du repère, en
    # remettre une ici ne ferait que rétrécir le réseau pour rien.
    margin: float = 0.0
    # Cercles artistes : bornes de l'échelle de participation (DIAMÈTRE en px).
    node_size_min: float = 88.0
    node_size_max: float = 160.0
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
    # Deux titres sur un même ovale se posent sur DEUX anneaux concentriques —
    # l'équivalent d'un retour à la ligne sur une courbe. Écart entre eux :
    ellipse_label_line_gap: float = 24.0
    # Inclinaison maximale du texte : au-delà il devient pénible à lire. C'est
    # elle qui décide jusqu'où un titre peut glisser vers le bout de son ovale.
    ellipse_label_max_angle: float = 28.0
    # Instrumentistes : crédits « Piano », « Guitar »… ajoutés au réseau, avec
    # l'instrument sous le nom de l'artiste, en plus petit.
    # Poids du score de placement des titres (cf. `bubble_labels`). Trois termes
    # en pixels, donc directement comparables : la place autour du texte, sa
    # distance à ses propres cercles — c'est ce qui le rend rattachable à son
    # ovale — et ce qui sortirait de la zone.
    label_weight_clearance: float = 2.2
    label_weight_member: float = 1.6
    label_weight_zone: float = 3.0
    include_instruments: bool = True
    sub_label_ratio: float = 0.62  # taille du sous-titre, en fraction du nom
    # Part maximale du tour d'ellipse qu'un titre a le droit d'occuper : au-delà
    # il s'enroule et se lit à la verticale. La couronne s'écarte pour y tenir.
    ellipse_label_max_arc: float = 0.42
    # Plafond de cet écartement, en px. BAS par principe (18 et non 55) : l'écart
    # au tracé doit être quasi FIXE, c'est lui qui rattache le titre à son ovale
    # — un écart qui varie se lit comme un défaut (« certains partent trop loin
    # de leur cercle », 2026-09-01). Un titre trop long est COUPÉ EN DEUX plutôt
    # qu'écarté (ci-dessous) ; ce plafond ne rattrape que le reliquat.
    ellipse_label_max_extra_offset: float = 18.0
    # Couper en deux un titre SEUL trop long pour le tour de son ovale, et poser
    # la 1ʳᵉ moitié en haut, la 2ᵈᵉ en bas (cas d'un producteur solo au titre
    # long). Sinon il faudrait l'écarter loin, ou le laisser s'enrouler.
    split_long_titles: bool = True
    # Séparateur employé quand deux titres d'un même ovale doivent s'écrire À LA
    # SUITE, faute d'une seconde zone lisible autour du tracé (ovale traversé
    # par plusieurs autres). Choix utilisateur : une puce, bien détachée.
    label_join_separator: str = "•"
    # Hauteur d'une CAPITALE, en fraction de la taille de police. Sous l'ovale
    # les lettres poussent vers lui : la ligne de base recule d'exactement cette
    # hauteur, pas d'une police entière — sinon le texte du bas se retrouve
    # ~6 px plus loin du tracé que celui du haut, et l'asymétrie se voit
    # (« les deux parties ne sont pas au même écartement », 2026-09-01).
    # 0,72 est la valeur de Montserrat (grotesque géométrique).
    cap_height_ratio: float = 0.72
    # Dégagement minimal d'un titre autour de lui, en fraction de sa taille :
    # en dessous, il vaut mieux le poser ailleurs, quitte à renoncer au côté
    # préféré — un titre qui touche un cercle ne se lit plus.
    label_min_clearance_ratio: float = 0.5
    # Ellipses (une par combinaison de producteurs).
    min_axis_ratio: float = 0.35  # borne l'aplatissement (duo / quasi-colinéaire)
    ellipse_margin: float = 10.0  # marge ajoutée au rayon des cercles
    # ── Placement : les poids des forces de la relaxation ────────────────────
    # (cf. `bubble_layout`). Ils remplacent une pile de passes correctives ; les
    # monter accélère la mise en place mais fait osciller le nuage.
    gap: float = 14.0  # espace minimal entre deux cercles, quels qu'ils soient
    # Espace SUPPLÉMENTAIRE autour d'une bulle SOLO (aucune collaboration sur
    # l'album) : son ovale n'entre pas dans le jeu des contraintes (3 tentatives
    # annulées, JOURNAL 2026-09-01) — c'est donc la DISTANCE qui l'isole du
    # réseau, sinon elle se fond dans le tas et se lit comme un membre.
    # 70 et non 40 : à 40, un solo restait « trop proche » des ovales voisins
    # (Mammouth/M.A.N, Keur/Labrador) — verdict utilisateur A/B du 2026-09-01.
    gap_solo: float = 70.0
    # Les membres d'un groupe se rapprochent. 0,12 et non 0,06 : à 0,06 la
    # cohésion ne faisait pas le poids face à Lloyd (0,22), qui étale les
    # membres à travers la zone — l'ellipse englobante suivait et devenait
    # ÉNORME (mesuré 15,4× l'aire de ses membres sur J.000.$), au point de
    # capturer des étrangers. Doubler la cohésion règle les deux d'un coup
    # (15,4× → 3,6×, 6 captures → 0). Au-delà de 0,20 les planches se
    # dégradent en sens inverse (Swing : 30 captures à 0,45).
    force_cohesion: float = 0.12
    # La cohésion suit la place OCCUPÉE : une valeur unique ne convient pas aux
    # deux régimes (albums denses ≈ 22-25 % de la zone, petits ≈ 7-11 %) — celle
    # qui empêche l'ovale d'enfler sur un dense TASSE un petit album, qui a
    # proportionnellement plus de place. `cohesion_density_ref` = l'occupation
    # au-delà de laquelle `force_cohesion` s'applique en entier ; en dessous
    # elle décroît proportionnellement, jamais sous `cohesion_scale_min`.
    cohesion_density_ref: float = 0.22
    cohesion_scale_min: float = 0.30
    force_exclusion: float = 0.5  # un étranger est chassé de l'ellipse d'un groupe
    force_disjunction: float = 20.0  # écarte deux ovales sans membre commun (px/itération)
    force_spread: float = 0.22  # répartition homogène dans la zone (relaxation de Lloyd)
    # Décalage anti-grille (px, déterministe). DÉSACTIVÉ par défaut : à 34 px il
    # éparpillait les membres d'un groupe en 2D → ellipses rondes et « obèses »,
    # solos poussés dans les interstices des hubs. Verdict utilisateur A/B du
    # 2026-09-01 : le léger effet de rangées gêne moins que cet éparpillement.
    spread_jitter: float = 0.0
    lloyd_cell: float = 20.0  # finesse de l'échantillonnage de la zone, en px
    seed_scale: float = 190.0  # échelle de l'amorce (spring layout ~[-1,1]) → px
    # Cadre : il matérialise EXACTEMENT la zone dans l'aperçu — ce qui déborde
    # se voit d'un coup d'œil (rien n'est mis à l'échelle pour rentrer).
    draw_frame: bool = True
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

    `badge_text` = ce qu'affiche le badge, pas toujours le simple compte : un
    producteur qui a des morceaux à plusieurs ET des solos porte « 15 (7 Solos) ».
    `sub_label` = son instrument, posé sous son nom en plus petit.
    """

    key: str
    display: str
    x: float
    y: float
    size: float
    track_count: int
    label_font_size: float
    badge_text: str = ""
    sub_label: str = ""


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
class LabelRing:
    """Un titre posé sur son anneau : le texte, son écart au tracé, où le centrer.

    Un anneau par titre : deux morceaux sur un même ovale s'empilent sur deux
    couronnes concentriques plutôt que de s'aligner en une seule longue ligne,
    qui ferait le tour de l'ovale et deviendrait illisible.
    """

    text: str
    offset: float
    t: float
    sweep: int


@dataclass(frozen=True)
class GroupShape:
    """Une combinaison de producteurs : ellipse englobante + légende ancrée.

    `member_keys` = les producteurs de la combinaison (sert à l'id stable).
    `label_lines` = ce qui s'affiche (titres si peu de morceaux, sinon
    « N morceaux ») ; `rings` en est le rendu résolu, un anneau par ligne. Le
    texte est **curviligne, posé sur l'ellipse elle-même**. `track_count` = nb
    de morceaux.
    """

    member_keys: tuple[str, ...]
    ellipse: EllipseSpec
    label_lines: tuple[str, ...]
    rings: tuple[LabelRing, ...]
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


def _display_bleed(spec: BubbleSpec) -> float:
    """De combien élargir le viewBox pour ne rien couper — pas un pixel de plus.

    Mesuré sur le dessin RÉEL et non sur le pire cas théorique : sinon un album
    dont aucun titre ne dépasse serait affiché entouré d'un vide, et paraîtrait
    plus petit qu'il n'est.
    """
    over = 0.0
    for gs in spec.groups:
        outer = max((r.offset for r in gs.rings), default=0.0)
        pad = outer + spec.style.ellipse_label_font_size
        x0, y0, x1, y1 = gs.ellipse.bbox()
        over = max(
            over, -(x0 - pad), -(y0 - pad), (x1 + pad) - spec.width, (y1 + pad) - spec.height
        )
    return max(0.0, over)


def write_bubble_svg(spec: BubbleSpec, path=None) -> str:
    """Sérialise `spec` en SVG. Écrit dans `path` si fourni ; renvoie la chaîne."""
    style = spec.style
    prec = style.coord_precision

    def f(v):
        return _fmt(v, prec)

    # Débord d'AFFICHAGE : les titres curvilignes rident juste à l'extérieur de
    # leur ovale et peuvent donc dépasser la zone de quelques dizaines de pixels.
    # Le viewBox les laisse voir plutôt que de les couper — c'est un aperçu de
    # contrôle, un titre tronqué ferait croire à un bug. Le cadre dessiné, lui,
    # reste la VRAIE zone : ce qui est dehors se voit.
    bleed = _display_bleed(spec)
    dwg = svgwrite.Drawing(
        size=(f(spec.width + 2 * bleed), f(spec.height + 2 * bleed)), profile="full", debug=False
    )
    dwg.attribs["viewBox"] = (
        f"{f(-bleed)} {f(-bleed)} {f(spec.width + 2 * bleed)} {f(spec.height + 2 * bleed)}"
    )

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

        # Un anneau par titre, du plus proche du tracé au plus éloigné.
        for i, ring in enumerate(gs.rings):
            path_id = f"ellipse-labelpath-{set_token}-{i}"
            d, start_offset = _label_path(el.inflated(ring.offset), ring.t, ring.sweep, prec)
            g_ellipse_labels.add(dwg.path(d=d, fill="none", stroke="none", id=path_id))
            text = dwg.text(
                "",
                font_size=f(style.ellipse_label_font_size),
                font_family=style.font_family,
                font_weight="500",
                fill=style.ellipse_label_color,
                id=f"ellipse-label-{set_token}-{i}",
            )
            text.add(
                svgwrite.text.TextPath(
                    path=f"#{path_id}",
                    text=ring.text,
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
        # Badge à cheval sur le bas du cercle. Sa LARGEUR suit son texte : il ne
        # porte pas toujours un simple compte (« 15 (7 Solos) »).
        bsize = style.badge_size
        text_value = n.badge_text or str(n.track_count)
        bwidth = max(bsize, len(text_value) * style.badge_font_size * 0.62 + bsize * 0.5)
        bx = n.x - bwidth / 2.0
        by = n.y + half - bsize / 2.0
        g_badges.add(
            dwg.rect(
                insert=(f(bx), f(by)),
                size=(f(bwidth), f(bsize)),
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
                text_value,
                insert=(
                    f(bx + bwidth / 2.0),
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
        sub_size = n.label_font_size * style.sub_label_ratio
        # Le bloc ENTIER (nom + instrument) est centré : sinon ajouter une ligne
        # sous le nom décale l'ensemble vers le haut du cercle.
        total = (len(lines) - 1) * line_height + (sub_size * 1.35 if n.sub_label else 0.0)
        label_group = dwg.g(id=f"label-{token}")
        base_y = n.y - total / 2.0 + n.label_font_size * 0.35
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
        if n.sub_label:
            label_group.add(
                dwg.text(
                    n.sub_label,
                    insert=(f(n.x), f(base_y + (len(lines) - 1) * line_height + sub_size * 1.35)),
                    text_anchor="middle",
                    font_size=f(sub_size),
                    font_family=style.font_family,
                    font_weight="500",
                    fill=style.label_color,
                    id=f"instrument-{token}",
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
