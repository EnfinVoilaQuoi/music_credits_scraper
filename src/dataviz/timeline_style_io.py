"""Réglages utilisateur du générateur « Timeline », persistés entre exports.

Même contrat que `structure_style_io` : `data/timeline_style.json`, créé annoté
au premier export (une ligne `//` d'explication par réglage), relu à chaque
export, partiel admis, clé inconnue ignorée avec avertissement. Le
`timeline.json` d'un artiste est REGÉNÉRÉ à chaque export : y modifier une
valeur ne survit pas.
"""

from pathlib import Path

from src.dataviz.style_io import build_style, read_overrides, render_commented
from src.dataviz.timeline_svg import TimelineStyle
from src.utils.logger import get_logger

logger = get_logger(__name__)

FILENAME = "timeline_style.json"

# Mécanique de rendu, pas goût.
_LOCKED = {"coord_precision"}

_SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Page et zone — APERÇU SVG UNIQUEMENT.\n"
        "Dans Illustrator ce sont les repères nommés du template qui font foi\n"
        "(zone-timeline, slot-1..4, cartouche, courbe). La ligne de temps et ses\n"
        "pastilles sont FIXES dans le template : elles ne partent pas dans le JSON.",
        (
            ("page_width", "Largeur d'une page, en px (post Instagram : 1080)."),
            ("page_height", "Hauteur d'une page, en px (1350)."),
            ("page_gap", "Espace entre deux pages posées côte à côte dans l'aperçu, en px."),
            ("zone_x", "Bord gauche de la zone des 4 points, en px."),
            ("zone_width", "Largeur de la zone des 4 points, en px (slots équirépartis)."),
            ("line_y", "Ordonnée de la ligne de temps, en px."),
            ("line_stroke", "Couleur de la ligne de temps, en hexadécimal."),
            ("line_width", "Épaisseur de la ligne de temps, en pt."),
            ("dot_diameter", "Diamètre de la pastille posée sur la ligne à chaque point, en px."),
            ("dot_fill", "Couleur de la pastille, en hexadécimal."),
        ),
    ),
    (
        "Pochettes — SVG ET Illustrator.",
        (
            ("cover_large", "Côté du GRAND carré (projet de l'artiste), en px."),
            ("cover_small", "Côté du PETIT carré (feat, freestyle), en px."),
            ("cover_offset", "Distance entre la ligne de temps et le bord de la pochette, en px."),
            ("cover_radius", "Rayon des coins arrondis du cadre, aperçu SVG uniquement."),
            ("cover_stroke", "Couleur du cadre, aperçu SVG uniquement."),
            ("cover_stroke_width", "Épaisseur du contour extérieur, en pt."),
            ("cover_placeholder", "Couleur de l'aplat quand la pochette manque, aperçu."),
        ),
    ),
    (
        "Disques de certification — SVG ET Illustrator.\n"
        "Diamètre = côté du carré qu'ils accompagnent (300 ou 200). L'aperçu\n"
        "dessine des disques pleins ; Illustrator pose les SYMBOLES du template\n"
        "(certif-<organisme>-<palier>).",
        (
            (
                "disc_overlap",
                "Part du disque CACHÉE derrière la pochette, de 0 (à côté) à 1 "
                "(entièrement dessous). 0.5 = à moitié.",
            ),
            ("disc_step", "Décalage entre deux disques d'un multi (2x Platine…), en px."),
            ("disc_fill", "Couleur des disques, aperçu SVG uniquement."),
            ("disc_hole_ratio", "Trou central, en proportion du diamètre, aperçu."),
        ),
    ),
    (
        "Typographie — SVG ET Illustrator.\n" "Les tailles sont en points (pt).",
        (
            (
                "font_light",
                "Nom PostScript de la graisse Light — c'est CE nom qu'Illustrator "
                "cherche. Si la police manque, le texte est posé dans la police par "
                "défaut (visible à l'œil, à corriger à la main).",
            ),
            ("font_semibold", "Nom PostScript de la graisse SemiBold, même remarque."),
            (
                "font_family",
                "Pile de polices CSS pour l'APERÇU SVG uniquement (Illustrator "
                "utilise font_light / font_semibold).",
            ),
            ("weight_light", "Graisse CSS correspondant à Light, aperçu SVG uniquement."),
            ("weight_semibold", "Graisse CSS correspondant à SemiBold, aperçu SVG uniquement."),
            (
                "size_label",
                "Corps du libellé, en pt. Ligne 1 en Light (mots entre ** en SemiBold), "
                "ligne 2 en SemiBold ; un \\n dans le champ replie la ligne.",
            ),
            ("label_line_height", "Interligne du libellé, en proportion du corps (1.2)."),
            ("label_gap", "Espace entre la pochette et le libellé, en px."),
            ("size_year", "Corps de l'année, en pt."),
            (
                "year_offset",
                "Distance entre la ligne de temps et l'année (posée du côté OPPOSÉ à "
                "la pochette), en px.",
            ),
            ("text_color", "Couleur des libellés et des années, en hexadécimal."),
            (
                "baseline_ratio",
                "Centrage vertical manuel des textes de l'APERÇU SVG, en proportion "
                "du corps (dominant-baseline est ignoré par Illustrator).",
            ),
        ),
    ),
    (
        "Cartouche des streams cumulés. La FORME est fixe dans le template\n"
        "(cotes et couleurs = aperçu seulement) ; seuls le texte et ses corps voyagent.",
        (
            ("cartouche_x", "Bord gauche du cartouche, en px (aperçu)."),
            ("cartouche_y", "Bord haut du cartouche, en px (aperçu)."),
            ("cartouche_width", "Largeur du cartouche, en px (aperçu)."),
            ("cartouche_height", "Hauteur du cartouche, en px (aperçu)."),
            ("cartouche_radius", "Rayon des coins, en px (aperçu)."),
            ("cartouche_fill", "Couleur de fond, aperçu SVG uniquement."),
            ("cartouche_text_color", "Couleur du texte du cartouche, aperçu SVG uniquement."),
            ("size_cartouche_value", "Corps du chiffre (« 175 M »), en pt."),
            ("size_cartouche", "Corps des lignes sous le chiffre, en pt."),
            (
                "cartouche_lines",
                "Les lignes sous le chiffre, une par élément de la liste.",
            ),
        ),
    ),
    (
        "Courbe des streams cumulés — SVG ET Illustrator.\n"
        "Échelle verticale GLOBALE : 0 en bas, cumul final en haut, sur toutes\n"
        "les pages ; la page N reprend là où la page N-1 s'est arrêtée.",
        (
            (
                "curve_lag",
                "Décalage de la courbe vers la DROITE, en px : les streams s'accumulent "
                "APRÈS la sortie, la courbe monte donc après le point. Sur la dernière "
                "page le dernier sommet est porté au bord droit du cadre.",
            ),
            ("curve_top", "Ordonnée du cumul FINAL, en px (aperçu)."),
            ("curve_bottom", "Ordonnée de 0, en px (aperçu)."),
            ("curve_stroke", "Couleur de la courbe, en hexadécimal."),
            ("curve_width", "Épaisseur de la courbe, en px."),
        ),
    ),
    (
        "Fond de page — pochette du projet le plus streamé de la page, recadrée,\n"
        "sous un voile vert (dégradé haut→bas dans le template Illustrator), et\n"
        "la zone SOUS la courbe assombrie en mode de fusion.",
        (
            ("background_fill", "Couleur du voile, en hexadécimal — SVG ET Illustrator."),
            (
                "background_opacity",
                "Opacité du voile dans l'APERÇU SVG (Illustrator a son dégradé), de 0 à 1.",
            ),
            ("under_curve_fill", "Couleur de la zone sous la courbe, en hexadécimal."),
            ("under_curve_opacity", "Opacité de la zone sous la courbe, de 0 à 1 (0.3 = 30 %)."),
            (
                "under_curve_blend",
                "Mode de fusion CSS de la zone sous la courbe (hard-light = lumière crue).",
            ),
        ),
    ),
)


def style_path() -> Path:
    """`data/timeline_style.json` (import lazy de la config, comme `bubble_prod`)."""
    from src.config import DATA_DIR

    return Path(DATA_DIR) / FILENAME


def default_payload() -> dict:
    """Valeurs par défaut, dans l'ordre de lecture du fichier annoté."""
    base = TimelineStyle()
    payload = {}
    for _, entries in _SECTIONS:
        for key, _help in entries:
            value = getattr(base, key)
            payload[key] = list(value) if isinstance(value, tuple) else value
    return payload


_INTRO = [
    "// Réglages du générateur « Timeline ».",
    "//",
    "// Édite une valeur, relance l'export : elle sera reprise.",
    "// NE MODIFIE PAS le timeline.json d'un artiste — il est régénéré à chaque",
    "// export, ta valeur y serait écrasée. C'est CE fichier qui persiste.",
    "// La sélection des projets et leurs libellés vivent ailleurs :",
    "// data/timeline_overrides.json (bouton « Mémoriser » de l'onglet Timeline).",
    "//",
    "// Les lignes // sont des commentaires (JSON n'en admet pas nativement,",
    "// ils sont retirés à la lecture). Une clé inconnue est ignorée avec un",
    "// avertissement dans les logs. Le fichier peut être partiel : tout ce qui",
    "// manque garde sa valeur par défaut. Supprime-le pour repartir de zéro.",
]


def write_default_style(path: Path | None = None) -> Path:
    """(Re)crée le fichier de réglages annoté, avec les valeurs par défaut."""
    path = Path(path) if path else style_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_commented(_INTRO, _SECTIONS, default_payload()), encoding="utf-8")
    logger.info(f"🎛 Réglages « Timeline » écrits : {path}")
    return path


def load_style(path: Path | None = None) -> TimelineStyle:
    """`TimelineStyle` par défaut, surchargé par le fichier de réglages (créé s'il manque)."""
    path = Path(path) if path else style_path()
    if not path.exists():
        try:
            write_default_style(path)
        except OSError as exc:
            logger.warning(f"Réglages « Timeline » non créés ({path}) : {exc}")
        return TimelineStyle()

    read = read_overrides(path, TimelineStyle, _LOCKED, "Timeline")
    if read is None:
        return TimelineStyle()
    overrides, _raw = read
    # JSON ne connaît pas les tuples : la dataclass, elle, en attend un (hashable).
    if isinstance(overrides.get("cartouche_lines"), list):
        overrides["cartouche_lines"] = tuple(str(x) for x in overrides["cartouche_lines"])
    return build_style(TimelineStyle, overrides, "Timeline")
