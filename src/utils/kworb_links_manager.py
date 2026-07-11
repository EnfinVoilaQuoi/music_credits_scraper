"""Mémoire des décisions de rapprochement Kworb ↔ base (par artiste).

Pour les titres Kworb qui ressemblent à un morceau en base sans correspondre
exactement (« Matrix » vs « Matrix (Intro) »), l'utilisateur confirme ou rejette
UNE FOIS ; la décision est mémorisée pour ne plus redemander aux runs suivants.

Stockage : data/kworb_links/<artiste>.json
    {
      "confirmed": { "<titre kworb normalisé>": <track_id> },
      "rejected":  [ "<titre kworb normalisé>", ... ]
    }
Clé = titre Kworb normalisé (via title_matching.normalize_title) pour être
stable entre les runs malgré ponctuation/casse.
"""

import json
import re
from pathlib import Path

from src.utils.logger import get_logger
from src.utils.title_matching import normalize_title

logger = get_logger(__name__)


class KworbLinksManager:
    def __init__(self, base_dir: str = "data/kworb_links"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, artist_name: str) -> Path:
        safe = re.sub(r"[^\w\- ]", "_", artist_name or "artist")
        return self.base_dir / f"{safe}.json"

    def load(self, artist_name: str) -> dict:
        try:
            data = json.loads(self._path(artist_name).read_text(encoding="utf-8"))
        except Exception:
            data = {}
        data.setdefault("confirmed", {})
        data.setdefault("rejected", [])
        return data

    def _save(self, artist_name: str, data: dict):
        try:
            self._path(artist_name).write_text(
                json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception as e:
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
