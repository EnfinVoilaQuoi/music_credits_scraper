"""Gestionnaire de persistance — façade.

`DataManager` est la façade unique utilisée par la GUI et les scripts. Elle
compose `Database` (connexion + schéma + migrations) et hérite des repositories
`ArtistRepository` et `TrackRepository` (une responsabilité par module). L'API
publique est inchangée : aucun appelant ne bouge. Les méthodes transverses
(export JSON, statistiques globales, import des certifications) restent ici.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import ARTISTS_DIR, DATABASE_URL
from src.utils.artist_repository import ArtistRepository
from src.utils.db import Database
from src.utils.logger import get_logger
from src.utils.track_repository import TrackRepository

logger = get_logger(__name__)


class DataManager(ArtistRepository, TrackRepository):
    """Gère la persistance des données (façade sur Database + repositories)."""

    def __init__(self):
        # Persistance pure : ouvrir la base. Les certifications ne sont PLUS
        # importées ici (elles vivent dans certif_snep.csv, lu paresseusement par
        # le matcher unifié) — le constructeur ne déclenche aucun import CSV.
        self._db = Database(DATABASE_URL.replace("sqlite:///", ""))

    @property
    def db_path(self) -> str:
        """Chemin du fichier SQLite (compat : appelé par la GUI)."""
        return self._db.db_path

    @property
    def engine(self):
        """Moteur SQLAlchemy Core — utilisé par les repositories migrés (E2)."""
        return self._db.engine

    def _get_connection(self):
        """Context manager de connexion sqlite3 — délégué à Database (compat GUI).

        Chemin legacy en cours de remplacement par `self.engine` (Core, E2).
        """
        return self._db.connect()

    def export_to_json(self, artist_name: str, filepath: Path | None = None):
        """Exporte les données d'un artiste en JSON"""
        artist = self.get_artist_by_name(artist_name)
        if not artist:
            logger.error(f"Artiste non trouvé: {artist_name}")
            return None

        # Déterminer le chemin du fichier
        if filepath is None:
            filename = f"{artist.name.replace(' ', '_').lower()}_credits.json"
            filepath = ARTISTS_DIR / filename

        # Préparer les données
        data = {
            "artist": artist.to_dict(),
            "tracks": [track.to_dict() for track in artist.tracks],
            "export_date": datetime.now().isoformat(),
            "total_tracks": len(artist.tracks),
            "total_music_credits": sum(len(t.get_music_credits()) for t in artist.tracks),
            "total_video_credits": sum(len(t.get_video_credits()) for t in artist.tracks),
            "total_all_credits": sum(len(t.credits) for t in artist.tracks),
        }

        # Sauvegarder
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"Données exportées vers: {filepath}")
        return filepath

    def get_statistics(self) -> dict[str, Any]:
        """Retourne des statistiques sur la base de données"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                stats = {}

                # Nombre d'artistes
                cursor.execute("SELECT COUNT(*) FROM artists")
                stats["total_artists"] = cursor.fetchone()[0]

                # Nombre de morceaux
                cursor.execute("SELECT COUNT(*) FROM tracks")
                stats["total_tracks"] = cursor.fetchone()[0]

                # Nombre de crédits
                cursor.execute("SELECT COUNT(*) FROM credits")
                stats["total_credits"] = cursor.fetchone()[0]

                # Morceaux avec crédits complets
                cursor.execute("""
                    SELECT COUNT(DISTINCT t.id)
                    FROM tracks t
                    JOIN credits c ON t.id = c.track_id
                    WHERE c.role IN ('Producer', 'Writer')
                    GROUP BY t.id
                    HAVING COUNT(DISTINCT c.role) = 2
                """)
                result = cursor.fetchone()
                stats["tracks_with_complete_credits"] = result[0] if result else 0

                # Erreurs récentes
                cursor.execute("""
                    SELECT COUNT(*) FROM scraping_errors
                    WHERE error_time > datetime('now', '-1 day')
                """)
                stats["recent_errors"] = cursor.fetchone()[0]

                return stats

        except Exception as e:
            logger.error(f"Erreur lors de la récupération des statistiques: {e}")
            return {
                "total_artists": 0,
                "total_tracks": 0,
                "total_credits": 0,
                "tracks_with_complete_credits": 0,
                "recent_errors": 0,
            }
