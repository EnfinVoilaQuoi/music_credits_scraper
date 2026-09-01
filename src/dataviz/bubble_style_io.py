"""Réglages utilisateur des générateurs « Bubble » (Prod et Feat), persistés.

Même contrat que `structure_style_io` (voir `style_io` pour la mécanique) : le
`bubble_<kind>.json` d'un album est **régénéré** à chaque export, alors que ce
fichier-ci, `data/bubble_style.json`, persiste. Il est créé annoté au premier
export, tolère les lignes entièrement commentées, accepte un contenu partiel, et
ignore les clés inconnues avec un avertissement.

**Un seul fichier pour Prod et Feat** : les deux générateurs partagent le moteur
et la même zone de composition — deux réglages séparés donneraient deux planches
qui ne se ressemblent plus alors qu'elles se suivent dans le même post.
"""

from pathlib import Path

from src.dataviz.bubble_svg import SvgStyle
from src.dataviz.style_io import build_style, read_overrides, render_commented
from src.utils.logger import get_logger

logger = get_logger(__name__)

FILENAME = "bubble_style.json"

# Champs non réglables : mécanique de rendu, pas goût. `coord_precision` fixe la
# byte-identité ; les noms PostScript des polices doivent matcher le template.
_LOCKED = {"coord_precision"}

_SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Zone de composition.\n"
        "Côté Illustrator, c'est le repère « zone-bubble » du template qui fait\n"
        "foi ; ces cotes servent à l'aperçu SVG ET de référence d'échelle au JSX.",
        (
            ("frame_width", "Largeur de la zone, en px."),
            ("frame_height", "Hauteur de la zone, en px."),
            ("margin", "Marge intérieure entre le bord de la zone et le dessin, en px."),
            (
                "draw_frame",
                "Dessiner le cadre de la zone dans l'aperçu SVG (true/false). "
                "Il ne part JAMAIS dans Illustrator : il sert à voir ce qui déborde.",
            ),
            ("frame_stroke", "Couleur du cadre d'aperçu (hex)."),
            ("frame_stroke_width", "Épaisseur du cadre d'aperçu, en px."),
            ("frame_fill", "Remplissage du cadre d'aperçu (« none » par défaut)."),
        ),
    ),
    (
        "Cercles artistes.\n"
        "Le diamètre encode la participation : un artiste à 1 morceau fait\n"
        "node_size_min, le plus gros compte de l'album fait node_size_max. Ces\n"
        "valeurs sont ABSOLUES — c'est ce qui rend deux albums comparables.",
        (
            ("node_size_min", "Diamètre du plus petit cercle, en px."),
            ("node_size_max", "Diamètre du plus gros cercle, en px."),
            ("node_fill", "Couleur du cercle (hex) — visible aussi sous la photo."),
            ("node_stroke", "Couleur du contour du cercle (hex, ou « none »)."),
            ("node_stroke_width", "Épaisseur du contour du cercle, en px."),
            (
                "photo_overlay_color",
                "ILLUSTRATOR : couleur du voile posé sur la photo, pour que le nom reste lisible.",
            ),
            ("photo_overlay_opacity", "ILLUSTRATOR : opacité de ce voile, de 0 à 1."),
        ),
    ),
    (
        "Nom de l'artiste, centré dans son cercle.",
        (
            ("font_size", "Taille du nom sur le PLUS GROS cercle, en px."),
            (
                "font_size_min",
                "Plancher de taille : en dessous le nom est illisible, on le laisse déborder. "
                "Entre les deux, la taille suit le diamètre du cercle.",
            ),
            (
                "line_height_ratio",
                "Interligne des noms sur plusieurs mots, en multiple de la taille.",
            ),
            ("uppercase_names", "Écrire les noms en MAJUSCULES (true/false)."),
            (
                "include_instruments",
                "Faire entrer les instrumentistes dans le réseau (true/false) : les crédits "
                "« Piano », « Guitare »… deviennent des cercles, avec l'instrument sous le nom.",
            ),
            ("sub_label_ratio", "Taille de cet instrument, en fraction de celle du nom."),
            ("label_color", "Couleur du nom (hex)."),
            ("font_family", "APERÇU SVG : familles de police, la première disponible gagne."),
            ("font_bold", "ILLUSTRATOR : nom PostScript de la police des noms d'artistes."),
            ("font_medium", "ILLUSTRATOR : nom PostScript de la police des titres de morceaux."),
        ),
    ),
    (
        "Badge : le compteur de morceaux, posé en bas du cercle.",
        (
            ("badge_size", "Côté du badge, en px."),
            ("badge_corner_radius", "Rayon des coins du badge, en px."),
            ("badge_font_size", "Taille du chiffre, en px."),
            ("badge_fill", "Couleur du badge (hex)."),
            ("badge_text_color", "Couleur du chiffre (hex)."),
        ),
    ),
    (
        "Ellipses : une par combinaison d'artistes ayant travaillé ensemble.",
        (
            ("ellipse_stroke", "Couleur du tracé (hex)."),
            ("ellipse_stroke_width", "Épaisseur du tracé, en px."),
            ("ellipse_fill", "Remplissage (« none » par défaut)."),
            ("ellipse_margin", "Marge entre les cercles et l'ellipse qui les entoure, en px."),
            (
                "min_axis_ratio",
                "Aplatissement maximal : 0,35 empêche un duo de rendre une ellipse en aiguille.",
            ),
        ),
    ),
    (
        "Légendes des ellipses : les titres de morceaux, alignés sur l'ellipse.",
        (
            (
                "label_track_threshold",
                "Au-delà de ce nombre de morceaux, la légende devient « N morceaux » "
                "au lieu de lister les titres.",
            ),
            ("ellipse_label_font_size", "Taille des titres, en px."),
            ("ellipse_label_color", "Couleur des titres (hex)."),
            (
                "ellipse_label_gap",
                "Écart entre le tracé de l'ellipse et le texte posé dessus, en px "
                "(le titre est curviligne : trop peu d'écart et le trait barre les lettres).",
            ),
            (
                "ellipse_label_line_gap",
                "Écart entre deux titres d'un même ovale, en px : ils se posent sur des "
                "anneaux concentriques, l'un « sous » l'autre.",
            ),
            (
                "ellipse_label_max_angle",
                "Inclinaison maximale du texte, en degrés. C'est elle qui décide jusqu'où "
                "un titre peut glisser vers le bout de son ovale : plus on tolère, plus il "
                "part vers les bords de l'image, mais plus il penche.",
            ),
            (
                "label_weight_clearance",
                "Poids de la PLACE LIBRE autour d'un titre dans le choix de son emplacement.",
            ),
            (
                "label_weight_member",
                "Poids de sa distance à ses PROPRES cercles : c'est ce qui le rend rattachable "
                "à son ovale. Le baisser laisse les titres partir au loin.",
            ),
            ("label_weight_zone", "Poids de ce qui sortirait de la zone."),
            (
                "ellipse_label_max_arc",
                "Part maximale du tour d'ovale qu'un titre a le droit d'occuper, de 0 à 1. "
                "Au-delà il s'enroule et se lit à la verticale : le texte est alors écarté "
                "du tracé, où le tour est plus long.",
            ),
            (
                "ellipse_label_max_extra_offset",
                "Plafond de cet écartement supplémentaire, en px : au-delà, le titre ne "
                "semblerait plus appartenir à son ovale.",
            ),
        ),
    ),
    (
        "Placement — les forces qui posent les cercles.\n"
        "Le dessin est relaxé sous contraintes : les cercles s'écartent, les\n"
        "membres d'un groupe se rapprochent, un étranger est chassé de l'ovale\n"
        "d'un groupe, et le nuage grandit jusqu'à occuper la zone. Monter un\n"
        "poids accélère la mise en place mais fait osciller le dessin.",
        (
            ("gap", "Espace minimal entre deux cercles, en px."),
            ("force_cohesion", "Force de rapprochement des membres d'un même groupe, de 0 à 1."),
            (
                "force_exclusion",
                "Force qui chasse un cercle ÉTRANGER de l'ovale d'un groupe, de 0 à 1. "
                "Sans elle, un artiste qui passe par là se lit comme un membre.",
            ),
            (
                "force_repulsion",
                "Force de répartition entre cercles, en px par itération. Sans elle le nuage "
                "garde la forme de son amorce et se retrouve de guingois.",
            ),
            ("repulsion_range", "Portée de cette répartition, en px : au-delà, ils s'ignorent."),
            ("force_expansion", "Vitesse à laquelle le nuage grandit vers les bords, de 0 à 1."),
            ("seed_scale", "Échelle de l'amorce (le layout de départ), en px."),
            (
                "draw_edges",
                "Tracer les traits artiste↔artiste (true/false) — les bulles suffisent.",
            ),
            ("edge_color", "Couleur de ces traits (hex)."),
            ("edge_width", "Épaisseur de ces traits, en px."),
        ),
    ),
)

_INTRO = [
    "// Réglages des générateurs « Bubble Prod » et « Bubble Feat ».",
    "//",
    "// Édite une valeur, relance l'export : elle sera reprise.",
    "// NE MODIFIE PAS le bubble_prod.json / bubble_feat.json d'un album — ils",
    "// sont régénérés à chaque export, ta valeur y serait écrasée. C'est CE",
    "// fichier qui persiste, et il vaut pour les DEUX générateurs.",
    "//",
    "// Les lignes // sont des commentaires (JSON n'en admet pas nativement,",
    "// ils sont retirés à la lecture). Une clé inconnue est ignorée avec un",
    "// avertissement dans les logs. Le fichier peut être partiel : tout ce qui",
    "// manque garde sa valeur par défaut. Supprime-le pour repartir de zéro.",
]


def style_path() -> Path:
    """`data/bubble_style.json` (import lazy de la config, comme `bubble_prod`)."""
    from src.config import DATA_DIR

    return Path(DATA_DIR) / FILENAME


def default_payload() -> dict:
    """Valeurs par défaut, dans l'ordre de lecture du fichier annoté."""
    base = SvgStyle()
    return {key: getattr(base, key) for _, entries in _SECTIONS for key, _help in entries}


def _render_commented(payload: dict) -> str:
    return render_commented(_INTRO, _SECTIONS, payload)


def write_default_style(path: Path | None = None) -> Path:
    """(Re)crée le fichier de réglages annoté, avec les valeurs par défaut."""
    path = Path(path) if path else style_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_commented(default_payload()), encoding="utf-8")
    logger.info(f"🎛 Réglages « Bubble » écrits : {path}")
    return path


def load_style(path: Path | None = None) -> SvgStyle:
    """`SvgStyle` par défaut, surchargé par le fichier de réglages.

    Crée le fichier s'il n'existe pas — l'utilisateur découvre ainsi la liste
    complète des valeurs réglables, commentées, sans avoir à lire le code.
    """
    path = Path(path) if path else style_path()
    if not path.exists():
        try:
            write_default_style(path)
        except OSError as exc:
            logger.warning(f"Réglages « Bubble » non créés ({path}) : {exc}")
        return SvgStyle()

    read = read_overrides(path, SvgStyle, _LOCKED, "Bubble")
    if read is None:
        return SvgStyle()
    overrides, _raw = read
    return build_style(SvgStyle, overrides, "Bubble")
