"""Réglages utilisateur du générateur « Structure », persistés entre exports.

Le `structure.json` d'un album est **régénéré** à chaque export : y modifier une
valeur ne survit pas à la génération suivante. Les réglages durables vivent donc
dans un fichier à part, `data/structure_style.json`, relu à chaque export.

Le fichier est **créé annoté au premier export** : chaque réglage y est précédé
d'une ligne de commentaire `//` qui dit à quoi il sert, dans quelle unité, et où
il agit (aperçu SVG, planche Illustrator, ou les deux). JSON n'admet pas les
commentaires : on tolère donc les lignes commençant par `//`, retirées avant le
parse. Seules les lignes ENTIÈREMENT commentées sont supportées — pas de `//` en
fin de ligne de valeur, ce qui éviterait d'avoir à distinguer un commentaire d'un
`//` à l'intérieur d'une chaîne.

Une clé inconnue est ignorée avec un avertissement (plutôt que de faire échouer
tout l'export sur une faute de frappe), et un fichier partiel reste valide.
"""

from pathlib import Path

from src.dataviz.structure_svg import StructureStyle
from src.dataviz.style_io import build_style, read_overrides, render_commented
from src.utils.logger import get_logger

logger = get_logger(__name__)

FILENAME = "structure_style.json"

# Champs non réglables depuis le fichier : ils décrivent la mécanique de rendu,
# pas le goût. `colors` est traité à part (fusion clé à clé).
_LOCKED = {"colors", "coord_precision"}

# Le fichier écrit à la création : sections, et une ligne d'explication par clé.
# L'ordre est celui de la lecture, pas celui de la dataclass.
_SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Colonnes et zone — APERÇU SVG UNIQUEMENT.\n"
        "Dans Illustrator ce sont les repères nommés du template qui font foi\n"
        "(zone-structure, col-numero, col-titre, col-barre, col-duree).",
        (
            ("zone_width", "Largeur totale de la planche, en px."),
            ("zone_height", "Hauteur totale de la planche, en px."),
            ("margin_left", "Marge avant la 1re colonne, en px."),
            ("gutter", "Espace entre deux colonnes, en px."),
            ("col_index_width", "Largeur de la colonne 1 (numéro de piste), en px."),
            ("col_title_width", "Largeur de la colonne 2 (titre + invités), en px."),
            (
                "col_bar_width",
                "Largeur de la colonne 3 : la barre entière = 100 % de la durée du morceau.",
            ),
            (
                "col_duration_width",
                "Largeur de la colonne 4 = débordement MAXIMAL du rectangle de durée, en px.",
            ),
        ),
    ),
    (
        "Répartition verticale des lignes — SVG ET Illustrator.",
        (
            ("padding_top", "Espace avant la 1re ligne, en px."),
            ("padding_bottom", "Espace après la dernière ligne, en px."),
            (
                "row_height_max",
                "Plafond de hauteur d'une ligne, en px. NE SE VOIT QUE SUR UN ALBUM COURT : "
                "au-delà d'une douzaine de morceaux la contrainte de place mord avant. "
                "Sous ce plafond le bloc reste calé en haut et laisse du vide en bas.",
            ),
            (
                "gap_ratio",
                "Interligne, en PROPORTION de la hauteur de ligne (0.25 = un quart). "
                "Pas des px : l'interligne suit la densité de la tracklist.",
            ),
            (
                "project_gap",
                "Écart supplémentaire avant la ligne « Structure du Projet », en px.",
            ),
        ),
    ),
    (
        "Barre de structure — SVG ET Illustrator.",
        (
            (
                "corner_radius",
                "Rayon des coins arrondis, en px. S'applique aux deux bouts de la barre "
                "et au bord droit du rectangle de durée ; les jonctions internes "
                "entre segments restent droites.",
            ),
            (
                "separator_gap",
                "Vide entre deux blocs de MÊME couleur qui se suivent, en px. "
                "Sans lui, deux couplets d'affilée (ou l'outro d'une partie suivie de "
                "l'intro de la suivante) se confondraient en un seul aplat. "
                "Le fond de la planche transparaît dans ce vide.",
            ),
        ),
    ),
    (
        "Rectangle de durée — SVG ET Illustrator.\n"
        "Il passe SOUS la barre et déborde à droite ; il ne contient pas le texte,\n"
        "il glisse dessous. Absent sur la ligne « Structure du Projet ».",
        (
            (
                "duration_overlap",
                "De combien il passe sous la barre, en px. Évite tout liseré de fond "
                "à la jonction.",
            ),
            (
                "duration_width_min",
                "Longueur MINIMALE du rectangle, en px : son bord droit doit tomber "
                "juste après le chiffre des minutes. À caler sur la chasse réelle "
                "de Montserrat.",
            ),
            ("duration_fill", "Couleur de remplissage, en hexadécimal."),
            ("duration_opacity", "Opacité, de 0 (invisible) à 1 (opaque). 0.5 = 50 %."),
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
            ("size_index", "Corps du numéro de piste (colonne 1), en pt."),
            ("size_title", "Corps du titre et de sa parenthèse (colonne 2), en pt."),
            ("size_feat", "Corps de la ligne d'invités (colonne 2), en pt."),
            ("size_duration", "Corps de la durée (colonne 4), en pt."),
            (
                "title_line_gap",
                "Écart entre la ligne de titre et la ligne d'invités, en px. "
                "0 = les deux lignes se touchent (les interlignes propres à la "
                "police suffisent).",
            ),
            ("text_color", "Couleur de TOUS les textes, en hexadécimal."),
            (
                "baseline_ratio",
                "Centrage vertical manuel des textes de l'APERÇU SVG, en proportion "
                "du corps. N'y toucher que si le texte paraît décalé dans le SVG : "
                "l'attribut dominant-baseline est ignoré par Illustrator, d'où ce "
                "réglage. Sans effet sur la planche Illustrator.",
            ),
        ),
    ),
    (
        "Fond — APERÇU SVG UNIQUEMENT, jamais transmis à Illustrator.",
        (
            (
                "background_fill",
                "Approximation du fond de la DA, pour que les vides entre blocs de "
                "même couleur se lisent dans l'aperçu.",
            ),
        ),
    ),
)

_COLORS_HELP = (
    "Couleurs des 4 types de section — SVG ET Illustrator.\n"
    "Fusionnées clé à clé : tu peux n'en redéfinir qu'une.\n"
    "  intro_outro : intro, outro, interlude, solo, passage instrumental\n"
    "  couplet     : couplet, verse, partie\n"
    "  refrain     : refrain, chorus, hook\n"
    "  pont        : pont, bridge, pre-chorus"
)


def style_path() -> Path:
    """`data/structure_style.json` (import lazy de la config, comme `bubble_prod`)."""
    from src.config import DATA_DIR

    return Path(DATA_DIR) / FILENAME


def default_payload() -> dict:
    """Valeurs par défaut, dans l'ordre de lecture du fichier annoté."""
    base = StructureStyle()
    payload = {}
    for _, entries in _SECTIONS:
        for key, _help in entries:
            payload[key] = getattr(base, key)
    payload["colors"] = dict(base.colors)
    return payload


_INTRO = [
    "// Réglages du générateur « Structure ».",
    "//",
    "// Édite une valeur, relance l'export : elle sera reprise.",
    "// NE MODIFIE PAS le structure.json d'un album — il est régénéré à chaque",
    "// export, ta valeur y serait écrasée. C'est CE fichier qui persiste.",
    "//",
    "// Les lignes // sont des commentaires (JSON n'en admet pas nativement,",
    "// ils sont retirés à la lecture). Une clé inconnue est ignorée avec un",
    "// avertissement dans les logs. Le fichier peut être partiel : tout ce qui",
    "// manque garde sa valeur par défaut. Supprime-le pour repartir de zéro.",
]


def _render_commented(payload: dict) -> str:
    """Sérialise `payload` en JSON annoté (lignes `//` avant chaque réglage)."""
    return render_commented(_INTRO, _SECTIONS, payload, trailing=(_COLORS_HELP, "colors"))


def write_default_style(path: Path | None = None) -> Path:
    """(Re)crée le fichier de réglages annoté, avec les valeurs par défaut."""
    path = Path(path) if path else style_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_commented(default_payload()), encoding="utf-8")
    logger.info(f"🎛 Réglages « Structure » écrits : {path}")
    return path


def load_style(path: Path | None = None) -> StructureStyle:
    """`StructureStyle` par défaut, surchargé par le fichier de réglages.

    Crée le fichier s'il n'existe pas — l'utilisateur découvre ainsi la liste
    complète des valeurs réglables, commentées, sans avoir à lire le code.
    """
    path = Path(path) if path else style_path()
    if not path.exists():
        try:
            write_default_style(path)
        except OSError as exc:
            logger.warning(f"Réglages « Structure » non créés ({path}) : {exc}")
        return StructureStyle()

    read = read_overrides(path, StructureStyle, _LOCKED, "Structure")
    if read is None:
        return StructureStyle()
    overrides, raw = read

    # Couleurs : fusion clé à clé, pour qu'un fichier partiel reste valide.
    colors = dict(StructureStyle().colors)
    if isinstance(raw.get("colors"), dict):
        colors.update({k: v for k, v in raw["colors"].items() if k in colors})

    return build_style(StructureStyle, overrides, "Structure", colors=colors)
