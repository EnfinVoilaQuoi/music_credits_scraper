"""Overrides PAR ALBUM des générateurs Bubble : seed choisi, réglages locaux.

L'étage qui manquait entre les deux extrêmes : les forces globales
(`data/bubble_style.json`, valables pour TOUTES les planches — les régler pour
un album en abîme un autre) et la retouche Illustrator (à refaire à chaque
régénération). Un choix fait pour UNE planche — la variante (seed) retenue dans
la grille d'aperçus, un `gap_solo` plus grand — est persisté ici, par album et
par générateur, et rejoué à chaque export.

Fichier : `data/bubble_overrides.json`, un objet
`{"<kind>:<artiste>:<album>": {"seed": 7, "style": {"gap": 20.0}}}` où artiste
et album sont normalisés par `normalize_title` (mêmes regroupements de graphies
que `select_album_tracks` : « Vol.3 » et « Vol. 3 » partagent leur entrée).
`style` est une surcharge PARTIELLE de `SvgStyle`, appliquée PAR-DESSUS le style
global ; clés inconnues ignorées avec un avertissement (contrat `style_io`).

Priorité du seed : explicite (CLI `--seed`, sélecteur GUI) > override > défaut.
"""

import json
from dataclasses import replace
from pathlib import Path

from src.dataviz.bubble_svg import SvgStyle
from src.dataviz.style_io import editable_fields, strip_comments
from src.utils.logger import get_logger
from src.utils.title_matching import normalize_title

logger = get_logger(__name__)

FILENAME = "bubble_overrides.json"

# Mêmes champs verrouillés que `bubble_style_io` : mécanique de rendu, pas goût.
_LOCKED = {"coord_precision"}

_HEADER = [
    "// Overrides PAR ALBUM des générateurs Bubble (Prod et Feat).",
    "//",
    '// Une entrée par planche : "<kind>:<artiste>:<album>" (clés normalisées',
    "// automatiquement — passer par la GUI ou --save-seed plutôt que d'écrire",
    '// une clé à la main). "seed" = la variante retenue ; "style" = surcharge',
    "// partielle de bubble_style.json pour CETTE planche seulement.",
    "// Les lignes // sont des commentaires, retirés à la lecture.",
]


def overrides_path() -> Path:
    """`data/bubble_overrides.json` (import lazy de la config, comme `bubble_prod`)."""
    from src.config import DATA_DIR

    return Path(DATA_DIR) / FILENAME


def override_key(kind: str, artist_name: str, album: str) -> str:
    """Clé d'une planche : générateur + artiste + album, graphies normalisées."""
    return f"{kind}:{normalize_title(artist_name or '')}:{normalize_title(album or '')}"


def load_overrides(path: Path | None = None) -> dict[str, dict]:
    """Toutes les entrées du fichier — `{}` s'il est absent ou illisible.

    Tolérant, comme les fichiers de style : une faute de frappe dans le JSON ne
    doit pas faire échouer un export, elle le fait juste repartir des défauts.
    """
    path = Path(path) if path else overrides_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(strip_comments(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Overrides Bubble illisibles ({path}) : {exc} — ignorés")
        return {}
    if not isinstance(raw, dict):
        logger.warning(f"Overrides Bubble : objet JSON attendu dans {path} — ignorés")
        return {}
    return {key: value for key, value in raw.items() if isinstance(value, dict)}


def get_override(
    overrides: dict[str, dict] | None, kind: str, artist_name: str, album: str
) -> dict:
    """L'entrée de CETTE planche, ou `{}`."""
    return (overrides or {}).get(override_key(kind, artist_name, album), {})


def resolve_seed(override: dict, explicit: int | None, default: int) -> int:
    """Priorité du seed : explicite > mémorisé pour la planche > défaut."""
    if explicit is not None:
        return explicit
    seed = override.get("seed")
    return seed if isinstance(seed, int) else default


def apply_style_override(style: SvgStyle, override: dict) -> SvgStyle:
    """Le style global surchargé par le bloc `style` de la planche (partiel)."""
    raw = override.get("style")
    if not isinstance(raw, dict) or not raw:
        return style
    editable = editable_fields(SvgStyle, _LOCKED)
    known = {key: value for key, value in raw.items() if key in editable}
    unknown = sorted(set(raw) - set(known))
    if unknown:
        logger.warning(
            f"Overrides Bubble : clés de style inconnues ignorées — {', '.join(unknown)}"
        )
    try:
        return replace(style, **known)
    except TypeError as exc:
        logger.warning(f"Overrides Bubble : bloc style invalide ({exc}) — ignoré")
        return style


def save_override(
    kind: str,
    artist_name: str,
    album: str,
    *,
    seed: int | None = None,
    style: dict | None = None,
    path: Path | None = None,
) -> Path:
    """Fusionne `seed` et/ou `style` dans l'entrée de la planche (créée au besoin).

    Relit le fichier avant d'écrire : deux planches mémorisées à la suite ne
    s'écrasent pas. Les entrées sont triées — le diff d'un fichier de données
    doit rester lisible.
    """
    path = Path(path) if path else overrides_path()
    data = load_overrides(path)
    key = override_key(kind, artist_name, album)
    entry = dict(data.get(key, {}))
    if seed is not None:
        entry["seed"] = int(seed)
    if style:
        entry["style"] = {**entry.get("style", {}), **style}
    data[key] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text("\n".join([*_HEADER, body]) + "\n", encoding="utf-8")
    return path
