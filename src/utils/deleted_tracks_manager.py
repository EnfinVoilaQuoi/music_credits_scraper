"""Gestionnaire d'historique des morceaux supprimés (par genius_id).

But : quand un morceau est supprimé définitivement, on mémorise son `genius_id`
(clé STABLE entre les ré-imports, contrairement à `track.id` qui disparaît) pour
pouvoir, à la prochaine récupération de discographie, NE PAS le réajouter.
"""

import json
from datetime import datetime
from pathlib import Path

from src.config import DATA_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DeletedTracksManager:
    """Persiste l'historique des morceaux supprimés pour chaque artiste."""

    def __init__(self):
        self.deleted_tracks_dir = DATA_DIR / "deleted_tracks"
        self.deleted_tracks_dir.mkdir(exist_ok=True)
        logger.info(f"Manager des morceaux supprimés initialisé: {self.deleted_tracks_dir}")

    def _get_artist_file(self, artist_name: str) -> Path:
        safe_name = "".join(c for c in artist_name if c.isalnum() or c in (" ", "-", "_")).rstrip()
        safe_name = safe_name.replace(" ", "_").lower()
        return self.deleted_tracks_dir / f"{safe_name}_deleted.json"

    def _read(self, artist_name: str) -> dict[str, dict]:
        """Retourne un dict {genius_id(str): {genius_id, title, deleted_at}}."""
        file_path = self._get_artist_file(artist_name)
        if not file_path.exists():
            return {}
        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("deleted_tracks", [])
            out = {}
            for e in entries:
                gid = e.get("genius_id")
                if gid is not None:
                    out[str(gid)] = e
            return out
        except Exception as e:
            logger.error(f"Erreur lecture morceaux supprimés pour {artist_name}: {e}")
            return {}

    def _write(self, artist_name: str, entries: dict[str, dict]) -> bool:
        try:
            file_path = self._get_artist_file(artist_name)
            data = {
                "artist_name": artist_name,
                "deleted_tracks": list(entries.values()),
                "last_updated": str(datetime.now()),
                "version": "1.0",
            }
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"Erreur écriture morceaux supprimés pour {artist_name}: {e}")
            return False

    def add_deleted(self, artist_name: str, genius_id: int | None, title: str = "") -> bool:
        """Ajoute un morceau à l'historique des supprimés (no-op si pas de genius_id)."""
        if not genius_id:
            logger.debug(f"Suppression non mémorisée (pas de genius_id): {title}")
            return False
        entries = self._read(artist_name)
        entries[str(genius_id)] = {
            "genius_id": genius_id,
            "title": title,
            "deleted_at": str(datetime.now()),
        }
        ok = self._write(artist_name, entries)
        if ok:
            logger.info(
                f"🗂️ Mémorisé comme supprimé pour {artist_name}: {title} (genius_id={genius_id})"
            )
        return ok

    def load_deleted_ids(self, artist_name: str) -> set[int]:
        """Set des genius_id supprimés (int)."""
        out = set()
        for gid in self._read(artist_name).keys():
            try:
                out.add(int(gid))
            except (TypeError, ValueError):
                pass
        return out

    def get_deleted_entries(self, artist_name: str) -> list[dict]:
        """Liste des entrées {genius_id, title, deleted_at}, plus récentes d'abord."""
        entries = list(self._read(artist_name).values())
        entries.sort(key=lambda e: e.get("deleted_at", ""), reverse=True)
        return entries

    def remove_deleted(self, artist_name: str, genius_id: int) -> bool:
        """Retire un morceau de l'historique (pour autoriser son réajout)."""
        entries = self._read(artist_name)
        if str(genius_id) in entries:
            del entries[str(genius_id)]
            return self._write(artist_name, entries)
        return True

    def clear(self, artist_name: str) -> bool:
        file_path = self._get_artist_file(artist_name)
        try:
            if file_path.exists():
                file_path.unlink()
            return True
        except Exception as e:
            logger.error(f"Erreur suppression historique pour {artist_name}: {e}")
            return False
