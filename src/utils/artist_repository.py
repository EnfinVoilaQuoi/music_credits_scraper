"""Repository des artistes.

Persistance liée aux `artists` : save/get/delete, détails, canal YTM, totaux
Kworb, auditeurs mensuels + historique. Utilisé comme base de `DataManager`,
qui fournit `self.engine` (moteur SQLAlchemy Core, délégué à `Database`) et, via
`TrackRepository`, `self.get_artist_tracks` (utilisé par get_artist_by_name).
Comportement constant vs l'ancien `data_manager.py` (refonte 1.5 puis bascule
Core phase E2).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text, update

from src.models import Artist
from src.persistence.binding import date_bind
from src.persistence.schema import artists, monthly_listeners_history
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ArtistRepository:
    """Persistance des artistes. Requiert `self.engine` et
    `self.get_artist_tracks` (fournis par DataManager/TrackRepository)."""

    def save_artist(self, artist: Artist) -> int:
        """Sauvegarde ou met à jour un artiste"""
        with self.engine.begin() as conn:
            if artist.id:
                # Mise à jour
                conn.execute(
                    text(
                        "UPDATE artists SET name = :name, genius_id = :genius_id, "
                        "spotify_id = :spotify_id, discogs_id = :discogs_id, updated_at = :now "
                        "WHERE id = :id"
                    ),
                    {
                        "name": artist.name,
                        "genius_id": artist.genius_id,
                        "spotify_id": artist.spotify_id,
                        "discogs_id": artist.discogs_id,
                        "now": datetime.now(),
                        "id": artist.id,
                    },
                )
            else:
                # Insertion (OR IGNORE si l'artiste existe déjà par contrainte UNIQUE)
                result = conn.execute(
                    text(
                        "INSERT OR IGNORE INTO artists (name, genius_id, spotify_id, "
                        "discogs_id, created_at, updated_at) "
                        "VALUES (:name, :genius_id, :spotify_id, :discogs_id, :now, :now)"
                    ),
                    {
                        "name": artist.name,
                        "genius_id": artist.genius_id,
                        "spotify_id": artist.spotify_id,
                        "discogs_id": artist.discogs_id,
                        "now": datetime.now(),
                    },
                )
                # rowcount == 1 → ligne insérée (nouvel id) ; 0 → déjà présente
                # (IGNORE) : on relit l'id par nom. Signal plus fiable que
                # lastrowid après un INSERT OR IGNORE ignoré.
                if result.rowcount:
                    artist.id = result.lastrowid
                else:
                    row = (
                        conn.execute(
                            text("SELECT id FROM artists WHERE name = :name"),
                            {"name": artist.name},
                        )
                        .mappings()
                        .first()
                    )
                    if row:
                        artist.id = row["id"]

            logger.info(f"Artiste sauvegardé: {artist.name} (ID: {artist.id})")
            return artist.id

    def get_artist_by_name(self, name: str) -> Artist | None:
        """Récupère un artiste par son nom - VERSION CORRIGÉE"""
        try:
            logger.debug(f"🔍 Recherche de l'artiste: '{name}'")

            with self.engine.connect() as conn:
                row = (
                    conn.execute(
                        select(
                            artists.c.id,
                            artists.c.name,
                            artists.c.genius_id,
                            artists.c.spotify_id,
                            artists.c.discogs_id,
                        ).where(artists.c.name == name)
                    )
                    .mappings()
                    .first()
                )

            if not row:
                logger.debug(f"❌ Aucun artiste trouvé pour: '{name}'")
                return None

            artist = Artist(
                id=row["id"],
                name=row["name"],
                genius_id=row["genius_id"],
                spotify_id=row["spotify_id"],
                discogs_id=row["discogs_id"],
            )

            logger.debug(f"🎤 Objet Artist créé: {artist.name} (ID: {artist.id})")

            # Charger les tracks (ouvre sa propre connexion moteur)
            try:
                artist.tracks = self.get_artist_tracks(artist.id)
                logger.info(f"🎵 {len(artist.tracks)} morceaux chargés pour {artist.name}")
            except Exception as tracks_error:
                logger.error(f"⚠️ Erreur chargement tracks: {tracks_error}")
                artist.tracks = []

            return artist

        except Exception as e:
            logger.error(f"❌ Erreur dans get_artist_by_name: {e}")
            return None

    def delete_artist(self, artist_name: str) -> bool:
        """Supprime un artiste et toutes ses données associées"""
        try:
            with self.engine.begin() as conn:
                # Récupérer l'ID de l'artiste
                artist_row = (
                    conn.execute(
                        text("SELECT id FROM artists WHERE name = :name"), {"name": artist_name}
                    )
                    .mappings()
                    .first()
                )

                if not artist_row:
                    logger.warning(f"Artiste non trouvé: {artist_name}")
                    return False

                artist_id = artist_row["id"]

                # Supprimer dans l'ordre (contraintes de clés étrangères)

                # 1. Supprimer les erreurs de scraping
                deleted_errors = conn.execute(
                    text(
                        "DELETE FROM scraping_errors WHERE track_id IN "
                        "(SELECT id FROM tracks WHERE artist_id = :aid)"
                    ),
                    {"aid": artist_id},
                ).rowcount

                # 2. Supprimer les crédits
                deleted_credits = conn.execute(
                    text(
                        "DELETE FROM credits WHERE track_id IN "
                        "(SELECT id FROM tracks WHERE artist_id = :aid)"
                    ),
                    {"aid": artist_id},
                ).rowcount

                # 2b. Supprimer les observations (pas de cascade FK, E4)
                conn.execute(
                    text(
                        "DELETE FROM observations WHERE track_id IN "
                        "(SELECT id FROM tracks WHERE artist_id = :aid)"
                    ),
                    {"aid": artist_id},
                )

                # 3. Supprimer les morceaux
                deleted_tracks = conn.execute(
                    text("DELETE FROM tracks WHERE artist_id = :aid"), {"aid": artist_id}
                ).rowcount

                # 4. Supprimer l'artiste
                deleted_artist = conn.execute(
                    text("DELETE FROM artists WHERE id = :aid"), {"aid": artist_id}
                ).rowcount

                logger.info(f"Artiste '{artist_name}' supprimé avec succès:")
                logger.info(f"  - {deleted_tracks} morceaux")
                logger.info(f"  - {deleted_credits} crédits")
                logger.info(f"  - {deleted_errors} erreurs de scraping")

                return deleted_artist > 0

        except Exception as e:
            logger.error(f"Erreur lors de la suppression de l'artiste: {e}")
            return False

    def get_artist_details(self, artist_name: str) -> dict[str, Any]:
        """Récupère les détails complets d'un artiste"""
        # Agrégats multi-tables (LEFT JOIN, GROUP BY, HAVING) exécutés en `text()`
        # verbatim via le moteur Core : fidélité comportementale maximale (une
        # traduction en `select()` n'apporterait rien ici et risquerait un écart
        # de tri/regroupement). Paramètres nommés (`:name`/`:aid`).
        try:
            with self.engine.connect() as conn:
                # Informations de base de l'artiste
                artist_row = (
                    conn.execute(
                        text(
                            "SELECT id, name, genius_id, spotify_id, discogs_id, "
                            "created_at, updated_at FROM artists WHERE name = :name"
                        ),
                        {"name": artist_name},
                    )
                    .mappings()
                    .first()
                )
                if not artist_row:
                    return {}

                artist_id = artist_row["id"]

                # Compter les morceaux et crédits
                counts = (
                    conn.execute(
                        text(
                            "SELECT COUNT(DISTINCT t.id) as tracks_count, "
                            "COUNT(DISTINCT c.id) as credits_count "
                            "FROM tracks t LEFT JOIN credits c ON t.id = c.track_id "
                            "WHERE t.artist_id = :aid"
                        ),
                        {"aid": artist_id},
                    )
                    .mappings()
                    .first()
                )

                # Morceaux récents
                recent_tracks = [
                    {
                        "title": row["title"],
                        "album": row["album"],
                        "release_date": row["release_date"],
                        "credits_count": row["credits_count"],
                    }
                    for row in conn.execute(
                        text(
                            "SELECT t.title, t.album, t.release_date, "
                            "COUNT(c.id) as credits_count "
                            "FROM tracks t LEFT JOIN credits c ON t.id = c.track_id "
                            "WHERE t.artist_id = :aid "
                            "GROUP BY t.id, t.title, t.album, t.release_date "
                            "ORDER BY t.updated_at DESC LIMIT 20"
                        ),
                        {"aid": artist_id},
                    ).mappings()
                ]

                # Crédits par rôle
                credits_by_role = {
                    row["role"]: row["count"]
                    for row in conn.execute(
                        text(
                            "SELECT role, COUNT(*) as count "
                            "FROM credits c JOIN tracks t ON c.track_id = t.id "
                            "WHERE t.artist_id = :aid GROUP BY role ORDER BY count DESC"
                        ),
                        {"aid": artist_id},
                    ).mappings()
                }

                return {
                    "name": artist_row["name"],
                    "genius_id": artist_row["genius_id"],
                    "spotify_id": artist_row["spotify_id"],
                    "discogs_id": artist_row["discogs_id"],
                    "created_at": artist_row["created_at"],
                    "updated_at": artist_row["updated_at"],
                    "tracks_count": counts["tracks_count"] if counts else 0,
                    "credits_count": counts["credits_count"] if counts else 0,
                    "recent_tracks": recent_tracks,
                    "credits_by_role": credits_by_role,
                }

        except Exception as e:
            logger.error(f"Erreur lors de la récupération des détails: {e}")
            return {}

    def get_artist_ytm_channel(self, artist_id: int):
        """Canal YTMusic épinglé pour cet artiste (UC...), ou None."""
        try:
            with self.engine.connect() as conn:
                row = (
                    conn.execute(select(artists.c.ytm_channel_id).where(artists.c.id == artist_id))
                    .mappings()
                    .first()
                )
                return row["ytm_channel_id"] if row and row["ytm_channel_id"] else None
        except Exception as e:
            logger.error(f"Erreur get_artist_ytm_channel: {e}")
            return None

    def get_artist_ytm_channel_info(self, artist_id: int) -> tuple:
        """Canal YTM épinglé + son origine : `(channel_id|None, source|None)`.

        `source ∈ {'manual', 'inferred'}` distingue une saisie GUI (jamais
        écrasée/bloquée) d'un vote (ré-effaçable si le gate le juge suspect).
        Artiste sans canal → `(None, None)`.
        """
        try:
            with self.engine.connect() as conn:
                row = (
                    conn.execute(
                        select(artists.c.ytm_channel_id, artists.c.ytm_channel_source).where(
                            artists.c.id == artist_id
                        )
                    )
                    .mappings()
                    .first()
                )
                if not row or not row["ytm_channel_id"]:
                    return (None, None)
                return (row["ytm_channel_id"], row["ytm_channel_source"])
        except Exception as e:
            logger.error(f"Erreur get_artist_ytm_channel_info: {e}")
            return (None, None)

    def set_artist_ytm_channel(
        self, artist_id: int, channel_id: str, source: str = "manual"
    ) -> bool:
        """Épingle le canal YTMusic d'un artiste (résout les homonymes).

        `source` par défaut = 'manual' (compat ascendante : la GUI épingle
        toujours du manuel). Le flux d'inférence passe `source='inferred'`.
        """
        try:
            stmt = (
                update(artists)
                .where(artists.c.id == artist_id)
                .values(ytm_channel_id=channel_id, ytm_channel_source=source)
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            logger.info(f"📌 Canal YTM épinglé pour artist_id={artist_id}: {channel_id} ({source})")
            return True
        except Exception as e:
            logger.error(f"Erreur set_artist_ytm_channel: {e}")
            return False

    def clear_artist_ytm_channel(self, artist_id: int) -> bool:
        """Dé-épingle le canal YTM (les deux colonnes à NULL).

        Utilisé quand le gate d'identité juge suspect un canal INFÉRÉ : on
        efface pour ne pas figer l'erreur (au prochain run, ré-inférence)."""
        try:
            stmt = (
                update(artists)
                .where(artists.c.id == artist_id)
                .values(ytm_channel_id=None, ytm_channel_source=None)
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            logger.info(f"📌 Canal YTM dé-épinglé pour artist_id={artist_id}")
            return True
        except Exception as e:
            logger.error(f"Erreur clear_artist_ytm_channel: {e}")
            return False

    def update_artist_kworb_totals(
        self,
        artist_id: int,
        total: int = None,
        daily: int = None,
        lead: int = None,
        feat: int = None,
        kworb_date=None,
    ) -> bool:
        """Stocke les totaux du tableau récap Kworb (page songs de l'artiste)."""
        try:
            c = artists.c
            stmt = (
                update(artists)
                .where(c.id == artist_id)
                .values(
                    kworb_total_streams=func.coalesce(total, c.kworb_total_streams),
                    kworb_daily_streams=func.coalesce(daily, c.kworb_daily_streams),
                    kworb_lead_streams=func.coalesce(lead, c.kworb_lead_streams),
                    kworb_feat_streams=func.coalesce(feat, c.kworb_feat_streams),
                    kworb_updated=func.coalesce(date_bind(kworb_date), c.kworb_updated),
                    updated_at=datetime.now(),
                )
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            return True
        except Exception as e:
            logger.error(f"Erreur update_artist_kworb_totals (artist_id={artist_id}): {e}")
            return False

    def update_artist_monthly_listeners(
        self,
        artist_id: int,
        spotify_listeners: int | None = None,
        ytm_listeners: int | None = None,
    ) -> bool:
        """Met à jour les auditeurs mensuels d'un artiste et enregistre l'historique."""
        try:
            from src.utils.streams_calculator import calculate_total_monthly_listeners

            total = calculate_total_monthly_listeners(spotify_listeners, ytm_listeners)
            c = artists.c
            upd = (
                update(artists)
                .where(c.id == artist_id)
                .values(
                    spotify_monthly_listeners=func.coalesce(
                        spotify_listeners, c.spotify_monthly_listeners
                    ),
                    ytm_monthly_listeners=func.coalesce(ytm_listeners, c.ytm_monthly_listeners),
                    updated_at=datetime.now(),
                )
            )
            ins = monthly_listeners_history.insert().values(
                artist_id=artist_id,
                spotify_listeners=spotify_listeners,
                ytm_listeners=ytm_listeners,
                total_estimated=total,
                recorded_at=datetime.now(),
            )
            with self.engine.begin() as conn:
                conn.execute(upd)
                conn.execute(ins)
            return True
        except Exception as e:
            logger.error(f"Erreur update_artist_monthly_listeners (id={artist_id}): {e}")
            return False

    def get_monthly_listeners_history(self, artist_id: int) -> list[dict[str, Any]]:
        """Retourne l'historique des auditeurs mensuels d'un artiste."""
        try:
            # `text()` brut : `recorded_at` (TIMESTAMP) doit revenir en STRING
            # verbatim comme au temps du legacy sqlite3 — un `select()` typé la
            # parserait en datetime (cf. get_artist_tracks / piège E2).
            with self.engine.connect() as conn:
                rows = (
                    conn.execute(
                        text(
                            "SELECT spotify_listeners, ytm_listeners, total_estimated, "
                            "recorded_at FROM monthly_listeners_history "
                            "WHERE artist_id = :aid ORDER BY recorded_at DESC"
                        ),
                        {"aid": artist_id},
                    )
                    .mappings()
                    .all()
                )
                return [
                    {
                        "spotify_listeners": r["spotify_listeners"],
                        "ytm_listeners": r["ytm_listeners"],
                        "total_estimated": r["total_estimated"],
                        "recorded_at": r["recorded_at"],
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.error(f"Erreur get_monthly_listeners_history (id={artist_id}): {e}")
            return []

    def update_artist_spotify_id(self, artist_id: int, spotify_id: str) -> bool:
        """Met à jour le spotify_id d'un artiste."""
        try:
            stmt = (
                update(artists)
                .where(artists.c.id == artist_id)
                .values(spotify_id=spotify_id, updated_at=datetime.now())
            )
            with self.engine.begin() as conn:
                conn.execute(stmt)
            logger.info(f"spotify_id artiste #{artist_id} mis à jour: {spotify_id}")
            return True
        except Exception as e:
            logger.error(f"Erreur update_artist_spotify_id (artist_id={artist_id}): {e}")
            return False
