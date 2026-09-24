"""Corrections de fiches décidées à la main — et leur MÉMOIRE (étape 3, 2026-09-24).

`data/corrections/fiches.json` (VERSIONNÉ : la base en dépend, comme
`manual_fixes.json` des certifs) : `{artiste: [entrée, …]}`. Une entrée désigne
UNE fiche (`{"genius_id": N}`, sinon `{"titre": …, "album": …}` pour une fiche
Deezer / Kworb) et porte ses actions ; `scripts/appliquer_corrections_fiches.py`
les applique.

Corriger en base ne tient qu'un tour : le run suivant réécrit ce que sa source
dit. Le fichier est donc relu par les PRODUCTEURS, à trois endroits :

  · `ids_refuses` — le gate d'identité Spotify refuse un ID retiré à la main
    (sinon Kworb ou le scraper le reposent au prochain run) ;
  · `credits_refuses` — `save_track` n'écrit pas un crédit retiré (Genius
    ressert « Matthieu Cabaret — Composer », qui est le compositing du clip) ;
  · `genius_ids_absorbes` — une page Genius FUSIONNÉE dans une autre fiche n'est
    pas recréée par le run discographie.

Aucune de ces lectures ne lève : un fichier illisible vaut « aucune correction »,
et c'est dit une fois au log.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.config import DATA_DIR
from src.utils.logger import get_logger
from src.utils.title_matching import normalize_name, normalize_title

logger = get_logger(__name__)

FICHIER = Path(DATA_DIR) / "corrections" / "fiches.json"

_cache: dict = {"chemin": None, "mtime": None, "donnees": {}}


def charger() -> dict:
    """Le fichier, relu seulement s'il a changé (appelé par morceau et par crédit)."""
    try:
        mtime = FICHIER.stat().st_mtime
    except OSError:
        return {}
    if _cache["chemin"] == FICHIER and _cache["mtime"] == mtime:
        return _cache["donnees"]
    try:
        donnees = json.loads(FICHIER.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.error(f"Corrections de fiches illisibles ({FICHIER}) : {e} — ignorées")
        donnees = {}
    _cache.update(chemin=FICHIER, mtime=mtime, donnees=donnees)
    return donnees


def designe(designation: dict, *, genius_id, titre: str | None, album: str | None) -> bool:
    """PUR. La désignation d'une entrée vise-t-elle cette fiche ?"""
    if designation.get("genius_id") is not None:
        return genius_id is not None and int(genius_id) == int(designation["genius_id"])
    # Titre EXACT : normalisé, « Boss » désignait aussi « BOSS » — et une fois
    # « Boss » fusionnée, sa désignation retombait sur la fiche GARDÉE.
    if (designation.get("titre") or "").strip() != (titre or "").strip():
        return False
    if "album" in designation:
        return normalize_title(designation["album"] or "") == normalize_title(album or "")
    return True


def entrees_du_morceau(track) -> list[dict]:
    artiste = track.artist.name if track.artist else None
    if not artiste:
        return []
    return [
        e
        for e in charger().get(artiste, [])
        if designe(
            e.get("fiche") or {}, genius_id=track.genius_id, titre=track.title, album=track.album
        )
    ]


def ids_refuses(track) -> set[str]:
    return {sid for e in entrees_du_morceau(track) for sid in e.get("retirer_id_spotify") or []}


def credits_refuses(track) -> set[tuple[str, str]]:
    """{(nom normalisé, rôle)} — le rôle est la valeur de `CreditRole`."""
    return {
        (normalize_name(c["nom"]), c["role"])
        for e in entrees_du_morceau(track)
        for c in e.get("retirer_credits") or []
    }


def genius_ids_absorbes(artiste: str) -> set[int]:
    """Pages Genius fusionnées dans une autre fiche de `artiste`."""
    return {
        int(e["fiche"]["genius_id"])
        for e in charger().get(artiste, [])
        if e.get("fusionner_dans") and (e.get("fiche") or {}).get("genius_id") is not None
    }
