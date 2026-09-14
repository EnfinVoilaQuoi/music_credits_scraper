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

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.config import ARTISTS_DIR, DATABASE_URL
from src.models import Artist
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
        """Moteur SQLAlchemy Core — seul chemin d'accès à la base (E2)."""
        return self._db.engine

    def record_pending(self, track) -> None:
        """Écrit ce que `save_track` n'écrit plus : certifications et relations.

        À appeler juste APRÈS `save_track` — l'ordre est contraint, `save_track`
        attribuant l'`id` des morceaux neufs. No-op quand rien n'est marqué, donc
        l'ajouter à une boucle de sauvegarde ne coûte rien.

        Ces deux colonnes ont des écrivains dédiés parce que `[]` y disait deux
        choses opposées (cf. `TrackRepository.record_certifications`). Le marqueur
        distingue « recalculé, et vide » de « cet objet ne porte pas l'info » ; un
        morceau encore marqué en fin de flux est un enregistrement OUBLIÉ, et les
        appelants le vérifient (`certifications_non_enregistrees`).
        """
        if track.id is None:
            logger.warning(f"record_pending sans id: '{track.title}' — rien écrit")
            return
        if track.certs.needs_write and self.record_certifications(
            track.id, track.certs.entries, track.certs.album_entries
        ):
            track.certs.needs_write = False
        if track._relationships_pending and self.record_relationships(
            track.id, track.relationships
        ):
            track._relationships_pending = False
        # IDs Spotify découverts ce run (e23) : même raison d'être qu'au-dessus,
        # `save_track` n'écrit pas `track_spotify_ids`.
        if track._spotify_ids_pending and self.record_track_spotify_ids(
            track.id, track._spotify_ids_pending
        ):
            track._spotify_ids_pending = []

    @staticmethod
    def certifications_non_enregistrees(tracks) -> list[str]:
        """Titres des morceaux recalculés dont l'enregistrement a été oublié.

        Contrôle de FIN DE FLUX. Il ne peut pas vivre dans `save_track` : celui-ci
        tourne forcément AVANT l'enregistrement (attribution des ids), donc il
        verrait tous les morceaux marqués à chaque run et crierait en permanence.
        Un garde-fou qui crie toujours ne garde rien.
        """
        return [t.title for t in tracks if t.certs.needs_write or t._relationships_pending]

    def export_to_json(self, artist: Artist | str, filepath: Path | None = None):
        """Exporte les données d'un artiste en JSON.

        `artist` = un `Artist` DÉJÀ chargé (exporté tel quel, morceaux compris)
        ou un nom (rechargé depuis la base). La GUI construisait un artiste
        FILTRÉ des morceaux désactivés puis passait son NOM : l'export
        rechargeait tout et la boîte annonçait pourtant « M désactivés exclus ».
        """
        if isinstance(artist, str):
            nom = artist
            artist = self.get_artist_by_name(nom)
            if not artist:
                logger.error(f"Artiste non trouvé: {nom}")
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
            stats = {}
            # Agrégats globaux en `text()` verbatim via le moteur Core. La requête
            # « crédits complets » garde son GROUP BY + HAVING et son `.first()`
            # (= 1 dès qu'un morceau est complet — nuance figée E0, cf. REFONTE.md).
            with self.engine.connect() as conn:
                stats["total_artists"] = conn.execute(text("SELECT COUNT(*) FROM artists")).scalar()
                stats["total_tracks"] = conn.execute(text("SELECT COUNT(*) FROM tracks")).scalar()
                stats["total_credits"] = conn.execute(text("SELECT COUNT(*) FROM credits")).scalar()

                result = conn.execute(
                    text(
                        "SELECT COUNT(DISTINCT t.id) FROM tracks t "
                        "JOIN credits c ON t.id = c.track_id "
                        "WHERE c.role IN ('Producer', 'Writer') "
                        "GROUP BY t.id HAVING COUNT(DISTINCT c.role) = 2"
                    )
                ).first()
                stats["tracks_with_complete_credits"] = result[0] if result else 0

                stats["recent_errors"] = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM scraping_errors "
                        "WHERE error_time > datetime('now', '-1 day')"
                    )
                ).scalar()

            return stats

        except SQLAlchemyError as e:
            logger.error(f"Erreur lors de la récupération des statistiques: {e}")
            return {
                "total_artists": 0,
                "total_tracks": 0,
                "total_credits": 0,
                "tracks_with_complete_credits": 0,
                "recent_errors": 0,
            }
