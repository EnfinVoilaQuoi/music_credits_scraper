"""Payload `bubble_<kind>.json` : le jeu de données que consomme le script Illustrator.

Le script `scripts/illustrator/bubble.jsx` est du **code commun** : un seul
script pour Bubble Prod et Bubble Feat (le payload porte `kind`). Ce module
produit les **données** qui l'alimentent.

Différence assumée avec `structure_json`, qui transporte des RATIOS : ici les
coordonnées sont en **points dans la zone fixe** (860 × 520 par défaut). C'est
la condition de l'échelle constante voulue entre albums — un cercle de 55 px
doit faire 55 px sur toutes les planches. Le JSX applique un facteur unique
(`largeur du repère ÷ zone.width`) si le repère du template a été redimensionné.

Sortie déterministe (clés dans un ordre fixe, flottants arrondis) : deux
générations du même album donnent un fichier byte-identique, comme le SVG.
"""

import json

from src.dataviz.bubble_svg import BubbleSpec, SvgStyle, id_token, name_lines
from src.utils.image_downloader import find_artist_image

PAYLOAD_VERSION = 1

# Précision des coordonnées : celle du SVG (2 décimales) suffit largement à
# Illustrator, et garantit que les deux sorties décrivent le MÊME dessin.
_PRECISION = 2


def _r(value: float) -> float:
    return round(float(value), _PRECISION)


def _style_payload(style: SvgStyle) -> dict:
    """Bloc `style` : ce que le JSX doit savoir pour dessiner.

    Les réglages purement APERÇU (cadre de la zone, familles de police CSS) sont
    volontairement absents : dans Illustrator, c'est le repère du template qui
    borne le dessin, et les polices sont désignées par leur nom PostScript.
    """
    return {
        "font_bold": style.font_bold,
        "font_medium": style.font_medium,
        "uppercase_names": style.uppercase_names,
        "line_height_ratio": style.line_height_ratio,
        "label_color": style.label_color,
        "node_fill": style.node_fill,
        "node_stroke": style.node_stroke,
        "node_stroke_width": style.node_stroke_width,
        "photo_overlay_color": style.photo_overlay_color,
        "photo_overlay_opacity": style.photo_overlay_opacity,
        "badge_size": style.badge_size,
        "badge_corner_radius": style.badge_corner_radius,
        "badge_font_size": style.badge_font_size,
        "badge_fill": style.badge_fill,
        "badge_text_color": style.badge_text_color,
        "ellipse_stroke": style.ellipse_stroke,
        "ellipse_stroke_width": style.ellipse_stroke_width,
        "ellipse_fill": style.ellipse_fill,
        "ellipse_label_font_size": style.ellipse_label_font_size,
        "ellipse_label_color": style.ellipse_label_color,
        "ellipse_label_line_height": style.ellipse_label_line_height,
        "edge_color": style.edge_color,
        "edge_width": style.edge_width,
    }


def _image_payload(display: str) -> dict:
    """Photo de l'artiste : chemin ABSOLU pour le JSX, relatif pour la lecture humaine.

    Absent → `null` des deux côtés, et le JSX pose un cercle plein. C'est le cas
    courant pour les beatmakers, que Deezer référence mal : le visuel ne doit
    jamais dépendre de la présence d'une photo.
    """
    from src.config import IMAGES_DIR

    found = find_artist_image(display)
    if found is None:
        return {"image": None, "image_abs": None}
    return {"image": found.relative_to(IMAGES_DIR).as_posix(), "image_abs": str(found.resolve())}


def _node_payload(node, style: SvgStyle) -> dict:
    """Un cercle. `name_lines` = le nom DÉJÀ découpé et casse appliquée.

    Le découpage est fait ici et pas des deux côtés : l'aperçu SVG et la planche
    Illustrator doivent couper « Lewis Amber » au même endroit, sinon comparer
    les deux ne sert plus à rien.
    """
    name = node.display.upper() if style.uppercase_names else node.display
    return {
        "id": id_token(node.key),
        "name": node.display,
        "name_lines": list(name_lines(name)),
        "x": _r(node.x),
        "y": _r(node.y),
        "diameter": _r(node.size),
        "track_count": node.track_count,
        "font_size": _r(node.label_font_size),
        **_image_payload(node.display),
    }


def _group_payload(group) -> dict:
    el = group.ellipse
    return {
        "id": "--".join(id_token(k) for k in group.member_keys),
        "cx": _r(el.cx),
        "cy": _r(el.cy),
        "rx": _r(el.rx),
        "ry": _r(el.ry),
        "angle": _r(el.angle),
        "label_lines": list(group.label_lines),
        "label_x": _r(group.label_x),
        "label_y": _r(group.label_y),
        "label_angle": _r(group.label_angle),
        "track_count": group.track_count,
    }


def _edge_payload(edge) -> dict:
    return {
        "a": id_token(edge.a),
        "b": id_token(edge.b),
        "weight": edge.weight,
        "x1": _r(edge.x1),
        "y1": _r(edge.y1),
        "x2": _r(edge.x2),
        "y2": _r(edge.y2),
    }


def build_payload(spec: BubbleSpec, *, kind: str, artist_name: str, album: str, seed: int) -> dict:
    """Construit le payload complet à partir d'un `BubbleSpec` déjà résolu.

    `overflow` est transporté : le JSX doit pouvoir prévenir que la planche
    dépasse le repère, comme le fait l'app à la génération.
    """
    return {
        "version": PAYLOAD_VERSION,
        "kind": kind,
        "artist": artist_name,
        "album": album,
        "seed": seed,
        "zone": {"width": _r(spec.width), "height": _r(spec.height)},
        "overflow": [_r(v) for v in spec.overflow] if spec.overflow else None,
        "style": _style_payload(spec.style),
        "nodes": [_node_payload(n, spec.style) for n in spec.nodes],
        "groups": [_group_payload(g) for g in spec.groups],
        # Les traits artiste↔artiste sont désactivés par défaut (les bulles
        # disent déjà qui travaille avec qui) : rien à dessiner, rien à porter.
        "edges": [_edge_payload(e) for e in spec.edges] if spec.style.draw_edges else [],
    }


def write_bubble_json(payload: dict, path) -> str:
    """Sérialise `payload`. Écrit dans `path` si fourni ; renvoie la chaîne.

    Pas de `sort_keys` : l'ordre d'insertion ci-dessus est déjà déterministe et
    plus lisible à l'œil qu'un tri alphabétique.
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if path is not None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return text
