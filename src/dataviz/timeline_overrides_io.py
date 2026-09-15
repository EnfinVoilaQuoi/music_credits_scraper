"""Overrides PAR ARTISTE du générateur « Timeline » : la sélection et ses libellés.

Ce que l'heuristique ne peut pas décider — LESQUELS des ~30 candidats font les
16 points, et le texte éditorial de chaque point (« Passage chez **Colors
Studios** avec **Karma** ») — est mémorisé ici et rejoué à chaque export.

Fichier : `data/timeline_overrides.json`, un objet `{"<artiste>": {"pages": 4,
"entries": [{"key", "line1", "line2", "size"}, …]}}` où l'artiste est normalisé
par `normalize_title`. Les clés d'entrée sont celles de `timeline.Candidate`
(`album:<titre normalisé>`, `reedition:<…>`, `track:<id>`) : passer par la GUI
plutôt que d'en écrire une à la main. L'ordre du tableau est celui de la saisie
(diff lisible) ; le rendu retrie par date de toute façon.

Priorité : entrées explicites (GUI, CLI) > mémorisées > sélection par défaut.
"""

import json
from pathlib import Path

from src.dataviz.style_io import strip_comments
from src.dataviz.timeline import (
    DISC_SIDES,
    KIND_ALBUM,
    KIND_REEDITION,
    KIND_TRACK,
    PAGES_FULL,
    PAGES_SMALL,
    EntryChoice,
)
from src.dataviz.timeline_svg import SIZES
from src.utils.logger import get_logger
from src.utils.title_matching import normalize_title

logger = get_logger(__name__)

FILENAME = "timeline_overrides.json"

_KEY_PREFIXES = tuple(f"{kind}:" for kind in (KIND_ALBUM, KIND_REEDITION, KIND_TRACK))

_HEADER = [
    "// Overrides PAR ARTISTE du générateur Timeline.",
    "//",
    '// Une entrée par artiste (clé normalisée automatiquement) : "pages" = 3 ou 4,',
    '// "entries" = les projets retenus, avec leurs libellés. Les mots entre **',
    "// sont rendus en SemiBold. Passer par le bouton « Mémoriser » de l'onglet",
    "// Timeline plutôt que d'écrire une clé de projet à la main.",
    "// Les lignes // sont des commentaires, retirés à la lecture.",
]


def overrides_path() -> Path:
    """`data/timeline_overrides.json` (import lazy de la config, comme `bubble_prod`)."""
    from src.config import DATA_DIR

    return Path(DATA_DIR) / FILENAME


def override_key(artist_name: str) -> str:
    return normalize_title(artist_name or "")


def load_overrides(path: Path | None = None) -> dict[str, dict]:
    """Toutes les entrées du fichier — `{}` s'il est absent ou illisible."""
    path = Path(path) if path else overrides_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(strip_comments(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Overrides Timeline illisibles ({path}) : {exc} — ignorés")
        return {}
    if not isinstance(raw, dict):
        logger.warning(f"Overrides Timeline : objet JSON attendu dans {path} — ignorés")
        return {}
    return {key: value for key, value in raw.items() if isinstance(value, dict)}


def get_override(overrides: dict[str, dict] | None, artist_name: str) -> dict:
    """L'entrée de CET artiste, ou `{}`."""
    return (overrides or {}).get(override_key(artist_name), {})


def entries_from_override(override: dict) -> list[EntryChoice] | None:
    """Les entrées mémorisées, validées — `None` s'il n'y en a pas.

    Une entrée invalide (clé sans préfixe connu, taille inconnue, champ non
    textuel) est ignorée avec un avertissement : une ligne cassée ne doit pas
    faire perdre les quinze autres.
    """
    raw = override.get("entries")
    if not isinstance(raw, list) or not raw:
        return None
    entries: list[EntryChoice] = []
    for item in raw:
        if not isinstance(item, dict):
            logger.warning(f"Overrides Timeline : entrée ignorée (objet attendu) — {item!r}")
            continue
        key = item.get("key")
        size = item.get("size", "grand")
        side = item.get("disc_side", "auto")
        background = item.get("background", 0)
        line1, line2 = item.get("line1", ""), item.get("line2", "")
        if (
            not isinstance(key, str)
            or not key.startswith(_KEY_PREFIXES)
            or size not in SIZES
            or side not in DISC_SIDES
            or not isinstance(background, int)
            or isinstance(background, bool)
            or not 0 <= background <= PAGES_FULL
            or not isinstance(line1, str)
            or not isinstance(line2, str)
        ):
            logger.warning(f"Overrides Timeline : entrée invalide ignorée — {item!r}")
            continue
        entries.append(
            EntryChoice(
                key=key, line1=line1, line2=line2, size=size, disc_side=side, background=background
            )
        )
    return entries or None


def pages_from_override(override: dict) -> int | None:
    pages = override.get("pages")
    return pages if pages in (PAGES_SMALL, PAGES_FULL) else None


def save_override(
    artist_name: str,
    *,
    entries: list[EntryChoice],
    pages: int,
    path: Path | None = None,
) -> Path:
    """Remplace la sélection mémorisée de l'artiste (les autres artistes sont relus
    et conservés)."""
    path = Path(path) if path else overrides_path()
    data = load_overrides(path)
    data[override_key(artist_name)] = {
        "pages": int(pages),
        "entries": [
            {
                "key": e.key,
                "line1": e.line1,
                "line2": e.line2,
                "size": e.size,
                "disc_side": e.disc_side,
                "background": e.background,
            }
            for e in entries
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text("\n".join([*_HEADER, body]) + "\n", encoding="utf-8")
    return path
