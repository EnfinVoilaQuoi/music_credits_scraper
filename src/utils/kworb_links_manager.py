"""Mémoire des décisions de rapprochement Kworb ↔ base (par artiste).

Pour les titres Kworb qui ressemblent à un morceau en base sans correspondre
exactement (« Matrix » vs « Matrix (Intro) »), l'utilisateur confirme ou rejette
UNE FOIS ; la décision est mémorisée pour ne plus redemander aux runs suivants.

Stockage : data/kworb_links/<artiste>.json
    {
      "confirmed": { "<titre kworb normalisé>": <track_id> },
      "rejected":  [ "<titre kworb normalisé>", ... ],
      "decisions": { "<titre kworb normalisé>": {"kind": "...", "track_id": <id|null>} }
    }
`decisions` (2026-09-21) porte la taxonomie des variantes — rendition / existant
/ collab / tiers / ignore — et l'emporte sur les deux autres.
Clé = titre Kworb normalisé (via title_matching.normalize_title) pour être
stable entre les runs malgré ponctuation/casse.
"""

import re
from pathlib import Path

from src.config import DATA_DIR
from src.utils.fichiers_json import MemoireIllisible, ecrire_atomique, lire_memoire
from src.utils.logger import get_logger
from src.utils.title_matching import normalize_title

logger = get_logger(__name__)


class KworbLinksManager:
    def __init__(self, base_dir: str | None = None):
        # Ancré sur DATA_DIR : relatif au dossier courant, un lancement depuis
        # ailleurs lisait et écrivait une mémoire FANTÔME (même piège que BRMA).
        self.base_dir = Path(base_dir) if base_dir else Path(DATA_DIR) / "kworb_links"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, artist_name: str) -> Path:
        safe = re.sub(r"[^\w\- ]", "_", artist_name or "artist")
        return self.base_dir / f"{safe}.json"

    def load(self, artist_name: str) -> dict:
        try:
            data = lire_memoire(self._path(artist_name))
        except MemoireIllisible as e:
            # Lu comme vide pour décider, mais JAMAIS réécrit : la première
            # décision mémorisée effaçait toutes les autres (2026-10-11).
            logger.error(f"Décisions Kworb illisibles, elles ne seront pas réécrites : {e}")
            data = {"_illisible": True}
        data.setdefault("confirmed", {})
        data.setdefault("rejected", [])
        data.setdefault("decisions", {})
        return data

    def _save(self, artist_name: str, data: dict):
        if data.get("_illisible"):
            logger.error(f"Décisions Kworb de {artist_name} illisibles : rien n'est écrit")
            return
        try:
            ecrire_atomique(self._path(artist_name), data, indent=1)
        except (OSError, TypeError) as e:
            logger.error(f"Sauvegarde décisions Kworb échouée ({artist_name}): {e}")

    def confirm(self, artist_name: str, kworb_title: str, track_id: int):
        data = self.load(artist_name)
        norm = normalize_title(kworb_title)
        data["confirmed"][norm] = track_id
        if norm in data["rejected"]:
            data["rejected"].remove(norm)
        self._save(artist_name, data)
        logger.info(f"🔗 Kworb décision mémorisée : '{kworb_title}' → track #{track_id}")

    def reject(self, artist_name: str, kworb_title: str):
        data = self.load(artist_name)
        norm = normalize_title(kworb_title)
        if norm not in data["rejected"]:
            data["rejected"].append(norm)
        data["confirmed"].pop(norm, None)
        self._save(artist_name, data)
        logger.info(f"🚫 Kworb décision mémorisée : '{kworb_title}' rejeté")

    def decide(self, artist_name: str, kworb_title: str, kind: str, track_id: int | None = None):
        """Décision de la taxonomie des variantes (2026-09-21), mémorisée par titre.

        `kind` ∈ rendition (rattachée au morceau `track_id`) · existant / collab /
        tiers (la ligne Kworb appartient au morceau `track_id`, existant ou créé)
        · ignore. Elle REMPLACE un rejet ou une confirmation antérieurs : ces
        deux-là répondaient à une autre question (« même morceau ? »).
        """
        data = self.load(artist_name)
        norm = normalize_title(kworb_title)
        data["decisions"][norm] = {"kind": kind, "track_id": track_id}
        if norm in data["rejected"]:
            data["rejected"].remove(norm)
        data["confirmed"].pop(norm, None)
        self._save(artist_name, data)
        logger.info(f"🔗 Kworb décision mémorisée : '{kworb_title}' → {kind} (#{track_id})")
