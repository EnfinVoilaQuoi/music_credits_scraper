"""Gestionnaire de sauvegarde et chargement des données"""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import ARTISTS_DIR, DATA_DIR, DATABASE_URL
from src.models import Artist, Credit, Track
from src.utils.logger import get_logger
from src.utils.track_mapper import track_from_row

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Migrations de schéma versionnées (PRAGMA user_version)
# ──────────────────────────────────────────────────────────────────────────
#
# Séquence FIGÉE : une entrée par évolution, dans l'ordre, JAMAIS modifiée
# après coup (on ajoute uniquement à la fin). Les CREATE TABLE de
# _init_database posent le schéma de départ ; toute colonne ajoutée depuis
# est une ligne ci-dessous. Remplace les anciens ALTER TABLE en try/except
# avalé (cause du bug AUDIT §3.1 : ordre/exception silencieuse).
#
# Bootstrap : sur une base existante passée par l'ancien mécanisme
# (user_version encore 0, colonnes déjà présentes), chaque ADD COLUMN est
# sauté si sa colonne existe déjà, puis user_version est posé. Une fois
# user_version = N, ces migrations ne sont plus jamais rejouées.
_MIGRATIONS: list[tuple[int, str]] = [
    # artists — auditeurs mensuels + totaux Kworb
    (1, "ALTER TABLE artists ADD COLUMN spotify_monthly_listeners INTEGER"),
    (2, "ALTER TABLE artists ADD COLUMN ytm_monthly_listeners INTEGER"),
    (3, "ALTER TABLE artists ADD COLUMN ytm_channel_id TEXT"),
    (4, "ALTER TABLE artists ADD COLUMN kworb_total_streams INTEGER"),
    (5, "ALTER TABLE artists ADD COLUMN kworb_daily_streams INTEGER"),
    (6, "ALTER TABLE artists ADD COLUMN kworb_lead_streams INTEGER"),
    (7, "ALTER TABLE artists ADD COLUMN kworb_feat_streams INTEGER"),
    (8, "ALTER TABLE artists ADD COLUMN kworb_updated TIMESTAMP"),
    # tracks — colonnes historiques (filet pour bases intermédiaires) puis
    # enrichissements (isrc, vote BPM, key/mode, paroles synchro, streams…)
    (9, "ALTER TABLE tracks ADD COLUMN is_featuring BOOLEAN DEFAULT 0"),
    (10, "ALTER TABLE tracks ADD COLUMN primary_artist_name TEXT"),
    (11, "ALTER TABLE tracks ADD COLUMN featured_artists TEXT"),
    (12, "ALTER TABLE tracks ADD COLUMN lyrics TEXT"),
    (13, "ALTER TABLE tracks ADD COLUMN has_lyrics BOOLEAN DEFAULT 0"),
    (14, "ALTER TABLE tracks ADD COLUMN lyrics_scraped_at TIMESTAMP"),
    (15, "ALTER TABLE tracks ADD COLUMN isrc TEXT"),
    (16, "ALTER TABLE tracks ADD COLUMN bpm_source TEXT"),
    (17, "ALTER TABLE tracks ADD COLUMN bpm_confidence INTEGER"),
    (18, "ALTER TABLE tracks ADD COLUMN key_mode_source TEXT"),
    (19, "ALTER TABLE tracks ADD COLUMN reccobeats_resolution TEXT"),
    (20, "ALTER TABLE tracks ADD COLUMN secondary_role TEXT"),
    (21, "ALTER TABLE tracks ADD COLUMN bpm_alt INTEGER"),
    (22, "ALTER TABLE tracks ADD COLUMN lyrics_source TEXT"),
    (23, "ALTER TABLE tracks ADD COLUMN lyrics_synced TEXT"),
    (24, "ALTER TABLE tracks ADD COLUMN lyrics_synced_source TEXT"),
    (25, "ALTER TABLE tracks ADD COLUMN lyrics_synced_confidence INTEGER"),
    (26, "ALTER TABLE tracks ADD COLUMN relationships TEXT"),
    (27, "ALTER TABLE tracks ADD COLUMN certifications TEXT"),
    (28, "ALTER TABLE tracks ADD COLUMN album_certifications TEXT"),
    (29, "ALTER TABLE tracks ADD COLUMN musical_key TEXT"),
    (30, "ALTER TABLE tracks ADD COLUMN key TEXT"),
    (31, "ALTER TABLE tracks ADD COLUMN mode TEXT"),
    (32, "ALTER TABLE tracks ADD COLUMN time_signature TEXT"),
    (33, "ALTER TABLE tracks ADD COLUMN anecdotes TEXT"),
    (34, "ALTER TABLE tracks ADD COLUMN spotify_page_title TEXT"),
    (35, "ALTER TABLE tracks ADD COLUMN spotify_streams INTEGER"),
    (36, "ALTER TABLE tracks ADD COLUMN spotify_daily_streams INTEGER"),
    (37, "ALTER TABLE tracks ADD COLUMN spotify_streams_updated TIMESTAMP"),
    (38, "ALTER TABLE tracks ADD COLUMN ytm_streams INTEGER"),
    (39, "ALTER TABLE tracks ADD COLUMN ytm_streams_updated TIMESTAMP"),
    (40, "ALTER TABLE tracks ADD COLUMN youtube_url TEXT"),
    (41, "ALTER TABLE tracks ADD COLUMN youtube_url_source TEXT"),
    (42, "ALTER TABLE tracks ADD COLUMN album_override INTEGER"),
    # albums — streams YTM + éditions Spotify agrégées
    (43, "ALTER TABLE albums ADD COLUMN ytm_streams INTEGER"),
    (44, "ALTER TABLE albums ADD COLUMN ytm_streams_updated TIMESTAMP"),
    (45, "ALTER TABLE albums ADD COLUMN spotify_album_ids TEXT"),
    # Backfill : historiquement youtube_url n'était écrit que par Genius (media).
    # Idempotent (clause WHERE) — ne s'exécute qu'une fois grâce au versionnage.
    (
        46,
        "UPDATE tracks SET youtube_url_source = 'genius_media' "
        "WHERE youtube_url IS NOT NULL AND youtube_url != '' "
        "AND (youtube_url_source IS NULL OR youtube_url_source = '')",
    ),
]

_ADD_COLUMN_RE = re.compile(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", re.IGNORECASE)


def _table_columns(cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def run_migrations(cursor) -> None:
    """Applique les migrations de schéma en attente (voir _MIGRATIONS)."""
    current = cursor.execute("PRAGMA user_version").fetchone()[0]
    latest = _MIGRATIONS[-1][0] if _MIGRATIONS else 0
    if current >= latest:
        return

    # Colonnes présentes par table — consultées uniquement pour le bootstrap
    # depuis user_version = 0 (base déjà migrée par l'ancien mécanisme).
    present: dict[str, set[str]] = {}

    for version, sql in _MIGRATIONS:
        if version <= current:
            continue
        m = _ADD_COLUMN_RE.match(sql)
        if m:
            table, column = m.group(1), m.group(2)
            if table not in present:
                present[table] = _table_columns(cursor, table)
            if column not in present[table]:
                cursor.execute(sql)
                present[table].add(column)
                logger.info(f"✅ Migration {version} : {table}.{column} ajoutée")
        else:
            # Migration de données (backfill), idempotente par sa clause WHERE
            cursor.execute(sql)
            logger.info(f"✅ Migration {version} appliquée (données)")
        cursor.execute(f"PRAGMA user_version = {version}")


class DataManager:
    """Gère la persistance des données"""

    def __init__(self):
        self.db_path = DATABASE_URL.replace("sqlite:///", "")
        self._init_database()
        # Import tardif pour éviter la circularité
        try:
            from src.api.snep_certifications import get_snep_manager
            from src.utils.certification_enricher import CertificationEnricher

            self.certification_enricher = CertificationEnricher()
            self.snep_manager = get_snep_manager()
            self._initialize_certifications()
        except ImportError:
            # Si les modules ne sont pas encore créés
            self.certification_enricher = None
            self.snep_manager = None

    def _init_database(self):
        """Initialise la base de données"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Table des artistes
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS artists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    genius_id INTEGER,
                    spotify_id TEXT,
                    discogs_id INTEGER,
                    spotify_monthly_listeners INTEGER,
                    ytm_monthly_listeners INTEGER,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
            """)

            # Table historique des auditeurs mensuels
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS monthly_listeners_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artist_id INTEGER NOT NULL,
                    spotify_listeners INTEGER,
                    ytm_listeners INTEGER,
                    total_estimated INTEGER,
                    recorded_at TIMESTAMP,
                    FOREIGN KEY (artist_id) REFERENCES artists (id)
                )
            """)

            # Table des morceaux — DOIT être créée AVANT le bloc de migration
            # ci-dessous (bug historique : les ALTER s'exécutaient avant le
            # CREATE TABLE → base neuve figée sur le vieux schéma, cf. AUDIT.md §3.1).
            # Inclut les colonnes historiques (lyrics, is_featuring...) qui
            # n'étaient couvertes ni par l'ancien CREATE TABLE ni par les migrations.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    artist_id INTEGER NOT NULL,
                    album TEXT,
                    track_number INTEGER,
                    release_date TIMESTAMP,
                    genius_id INTEGER,
                    spotify_id TEXT,
                    discogs_id INTEGER,
                    bpm INTEGER,
                    duration INTEGER,
                    genre TEXT,
                    genius_url TEXT,
                    spotify_url TEXT,
                    youtube_url TEXT,
                    is_featuring BOOLEAN DEFAULT 0,
                    primary_artist_name TEXT,
                    featured_artists TEXT,
                    lyrics TEXT,
                    has_lyrics BOOLEAN DEFAULT 0,
                    lyrics_scraped_at TIMESTAMP,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP,
                    last_scraped TIMESTAMP,
                    FOREIGN KEY (artist_id) REFERENCES artists (id),
                    UNIQUE(title, artist_id)
                )
            """)

            # Table des crédits
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS credits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    role_detail TEXT,
                    source TEXT,
                    FOREIGN KEY (track_id) REFERENCES tracks (id),
                    UNIQUE(track_id, name, role, role_detail)
                )
            """)

            # Table des erreurs de scraping
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scraping_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id INTEGER NOT NULL,
                    error_message TEXT,
                    error_time TIMESTAMP,
                    FOREIGN KEY (track_id) REFERENCES tracks (id)
                )
            """)

            # Table des albums (données Kworb Spotify)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS albums (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    artist_id INTEGER NOT NULL,
                    spotify_streams INTEGER,
                    spotify_daily_streams INTEGER,
                    spotify_streams_updated TIMESTAMP,
                    FOREIGN KEY (artist_id) REFERENCES artists (id),
                    UNIQUE(title, artist_id)
                )
            """)

            # Migrations de schéma versionnées (PRAGMA user_version) : toutes
            # les évolutions de colonnes depuis le schéma de départ ci-dessus.
            run_migrations(cursor)

            conn.commit()
            logger.info("Base de données initialisée")

    def _initialize_certifications(self):
        """Initialise la base de données des certifications au premier lancement"""
        try:
            # Vérifier si le CSV existe et l'importer - nom exact du fichier SNEP
            csv_path = Path(DATA_DIR) / "certifications" / "snep" / "certif-.csv"
            if csv_path.exists():
                logger.info("🔄 Importation initiale des certifications SNEP...")
                success = self.snep_manager.import_from_csv(csv_path)
                if success:
                    logger.info("✅ Certifications SNEP importées avec succès")
                else:
                    logger.warning("⚠️ Problème lors de l'import des certifications")
        except Exception as e:
            logger.error(f"Erreur initialisation certifications: {e}")

    @contextmanager
    def _get_connection(self):
        """Context manager pour les connexions à la base de données"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def save_artist(self, artist: Artist) -> int:
        """Sauvegarde ou met à jour un artiste"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            if artist.id:
                # Mise à jour
                cursor.execute(
                    """
                    UPDATE artists 
                    SET name = ?, genius_id = ?, spotify_id = ?, 
                        discogs_id = ?, updated_at = ?
                    WHERE id = ?
                """,
                    (
                        artist.name,
                        artist.genius_id,
                        artist.spotify_id,
                        artist.discogs_id,
                        datetime.now(),
                        artist.id,
                    ),
                )
            else:
                # Insertion (OR IGNORE si l'artiste existe déjà par contrainte UNIQUE)
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO artists (name, genius_id, spotify_id,
                                                   discogs_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        artist.name,
                        artist.genius_id,
                        artist.spotify_id,
                        artist.discogs_id,
                        datetime.now(),
                        datetime.now(),
                    ),
                )
                if cursor.lastrowid:
                    artist.id = cursor.lastrowid
                else:
                    # L'artiste existait déjà — récupérer son ID
                    cursor.execute("SELECT id FROM artists WHERE name = ?", (artist.name,))
                    row = cursor.fetchone()
                    if row:
                        artist.id = row["id"]

            conn.commit()
            logger.info(f"Artiste sauvegardé: {artist.name} (ID: {artist.id})")
            return artist.id

    def save_track(self, track: Track) -> int:
        """Sauvegarde ou met à jour un morceau avec musical_key et time_signature"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            if not track.artist or not track.artist.id:
                raise ValueError("Le morceau doit avoir un artiste avec un ID")

            cursor.execute(
                """
                SELECT id, is_featuring, primary_artist_name, featured_artists, 
                    lyrics, has_lyrics, lyrics_scraped_at FROM tracks 
                WHERE title = ? AND artist_id = ?
            """,
                (track.title, track.artist.id),
            )

            existing_track = cursor.fetchone()

            if existing_track:
                track.id = existing_track["id"]

                # Préserver les infos existantes
                if existing_track["is_featuring"] and not hasattr(track, "is_featuring"):
                    track.is_featuring = bool(existing_track["is_featuring"])
                    track.primary_artist_name = existing_track["primary_artist_name"]
                    track.featured_artists = existing_track["featured_artists"]

                if existing_track["lyrics"] and not hasattr(track, "lyrics"):
                    track.lyrics = existing_track["lyrics"]
                    track.has_lyrics = bool(existing_track["has_lyrics"])
                    track.lyrics_scraped_at = existing_track["lyrics_scraped_at"]

                # Sérialiser les certifications en JSON
                certifications_json = (
                    json.dumps(getattr(track, "certifications", []))
                    if hasattr(track, "certifications")
                    else "[]"
                )
                album_certifications_json = (
                    json.dumps(getattr(track, "album_certifications", []))
                    if hasattr(track, "album_certifications")
                    else "[]"
                )
                relationships_json = json.dumps(getattr(track, "relationships", []) or [])

                # UPDATE NON-DESTRUCTIF : COALESCE préserve la valeur existante
                # quand le track entrant n'a pas la donnée (None). Évite qu'un
                # re-fetch de discographie (API Genius, champs vides) écrase
                # les données enrichies (lyrics, BPM, key, spotify_id...).
                cursor.execute(
                    """
                    UPDATE tracks
                    SET album = COALESCE(?, album),
                        track_number = COALESCE(?, track_number),
                        release_date = COALESCE(?, release_date),
                        genius_id = COALESCE(?, genius_id),
                        spotify_id = COALESCE(?, spotify_id),
                        discogs_id = COALESCE(?, discogs_id),
                        isrc = COALESCE(?, isrc),
                        bpm = COALESCE(?, bpm),
                        bpm_source = COALESCE(?, bpm_source),
                        bpm_confidence = COALESCE(?, bpm_confidence),
                        key_mode_source = COALESCE(?, key_mode_source),
                        reccobeats_resolution = COALESCE(?, reccobeats_resolution),
                        bpm_alt = COALESCE(?, bpm_alt),
                        duration = COALESCE(?, duration),
                        genre = COALESCE(?, genre),
                        key = COALESCE(?, key),
                        mode = COALESCE(?, mode),
                        musical_key = COALESCE(?, musical_key),
                        time_signature = COALESCE(?, time_signature),
                        genius_url = COALESCE(?, genius_url),
                        spotify_url = COALESCE(?, spotify_url),
                        youtube_url = COALESCE(?, youtube_url),
                        youtube_url_source = COALESCE(?, youtube_url_source),
                        is_featuring = ?,
                        primary_artist_name = COALESCE(?, primary_artist_name),
                        featured_artists = COALESCE(?, featured_artists),
                        secondary_role = COALESCE(?, secondary_role),
                        lyrics = COALESCE(?, lyrics),
                        lyrics_scraped_at = COALESCE(?, lyrics_scraped_at),
                        lyrics_source = COALESCE(?, lyrics_source),
                        lyrics_synced = COALESCE(?, lyrics_synced),
                        lyrics_synced_source = COALESCE(?, lyrics_synced_source),
                        lyrics_synced_confidence = COALESCE(?, lyrics_synced_confidence),
                        has_lyrics = CASE WHEN ? IS NOT NULL THEN 1 ELSE has_lyrics END,
                        anecdotes = COALESCE(?, anecdotes),
                        certifications = CASE WHEN ? = '[]' THEN certifications ELSE ? END,
                        album_certifications = CASE WHEN ? = '[]' THEN album_certifications ELSE ? END,
                        relationships = CASE WHEN ? = '[]' THEN relationships ELSE ? END,
                        updated_at = ?,
                        last_scraped = COALESCE(?, last_scraped)
                    WHERE id = ?
                """,
                    (
                        track.album,
                        getattr(track, "track_number", None),
                        track.release_date,
                        track.genius_id,
                        track.spotify_id,
                        track.discogs_id,
                        getattr(track, "isrc", None),
                        track.bpm,
                        getattr(track, "bpm_source", None),
                        getattr(track, "bpm_confidence", None),
                        getattr(track, "key_mode_source", None),
                        getattr(track, "reccobeats_resolution", None),
                        getattr(track, "bpm_alt", None),
                        track.duration,
                        track.genre,
                        getattr(track, "key", None),
                        getattr(track, "mode", None),
                        getattr(track, "musical_key", None),
                        getattr(track, "time_signature", None),
                        track.genius_url,
                        track.spotify_url,
                        getattr(track, "youtube_url", None),
                        getattr(track, "youtube_url_source", None),
                        getattr(track, "is_featuring", False),
                        getattr(track, "primary_artist_name", None),
                        getattr(track, "featured_artists", None),
                        getattr(track, "secondary_role", None),
                        getattr(track, "lyrics", None),
                        getattr(track, "lyrics_scraped_at", None),
                        getattr(track, "lyrics_source", None),
                        getattr(track, "lyrics_synced", None),
                        getattr(track, "lyrics_synced_source", None),
                        getattr(track, "lyrics_synced_confidence", None),
                        getattr(track, "lyrics", None),
                        getattr(track, "anecdotes", None),
                        certifications_json,
                        certifications_json,
                        album_certifications_json,
                        album_certifications_json,
                        relationships_json,
                        relationships_json,
                        datetime.now(),
                        track.last_scraped,
                        track.id,
                    ),
                )
            else:
                # Sérialiser les certifications en JSON
                certifications_json = (
                    json.dumps(getattr(track, "certifications", []))
                    if hasattr(track, "certifications")
                    else "[]"
                )
                album_certifications_json = (
                    json.dumps(getattr(track, "album_certifications", []))
                    if hasattr(track, "album_certifications")
                    else "[]"
                )
                relationships_json = json.dumps(getattr(track, "relationships", []) or [])

                # INSERT avec key, mode, musical_key, time_signature, anecdotes, certifications et spotify_page_title
                cursor.execute(
                    """
                    INSERT INTO tracks (
                        title, artist_id, album, track_number, release_date,
                        genius_id, spotify_id, discogs_id, isrc,
                        bpm, bpm_source, bpm_confidence, key_mode_source, reccobeats_resolution, bpm_alt, duration, genre, key, mode, musical_key, time_signature,
                        genius_url, spotify_url, youtube_url, youtube_url_source,
                        is_featuring, primary_artist_name, featured_artists, secondary_role,
                        lyrics, lyrics_scraped_at, lyrics_source, lyrics_synced, lyrics_synced_source, lyrics_synced_confidence, has_lyrics, anecdotes,
                        certifications, album_certifications, relationships, spotify_page_title,
                        created_at, updated_at, last_scraped
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        track.title,
                        track.artist.id,
                        track.album,
                        getattr(track, "track_number", None),
                        track.release_date,
                        track.genius_id,
                        track.spotify_id,
                        track.discogs_id,
                        getattr(track, "isrc", None),
                        track.bpm,
                        getattr(track, "bpm_source", None),
                        getattr(track, "bpm_confidence", None),
                        getattr(track, "key_mode_source", None),
                        getattr(track, "reccobeats_resolution", None),
                        getattr(track, "bpm_alt", None),
                        track.duration,
                        track.genre,
                        getattr(track, "key", None),
                        getattr(track, "mode", None),
                        getattr(track, "musical_key", None),
                        getattr(track, "time_signature", None),
                        track.genius_url,
                        track.spotify_url,
                        getattr(track, "youtube_url", None),
                        getattr(track, "youtube_url_source", None),
                        getattr(track, "is_featuring", False),
                        getattr(track, "primary_artist_name", None),
                        getattr(track, "featured_artists", None),
                        getattr(track, "secondary_role", None),
                        getattr(track, "lyrics", None),
                        getattr(track, "lyrics_scraped_at", None),
                        getattr(track, "lyrics_source", None),
                        getattr(track, "lyrics_synced", None),
                        getattr(track, "lyrics_synced_source", None),
                        getattr(track, "lyrics_synced_confidence", None),
                        bool(getattr(track, "lyrics", None)),
                        getattr(track, "anecdotes", None),
                        certifications_json,
                        album_certifications_json,
                        relationships_json,
                        getattr(track, "spotify_page_title", None),
                        datetime.now(),
                        datetime.now(),
                        track.last_scraped,
                    ),
                )
                track.id = cursor.lastrowid

            # Supprimer les anciens crédits avant d'ajouter les nouveaux
            if track.id:
                cursor.execute("DELETE FROM credits WHERE track_id = ?", (track.id,))

                # Sauvegarder les nouveaux crédits
                for credit in track.credits:
                    self._save_credit(cursor, track.id, credit)

            # Sauvegarder les erreurs
            for error in track.scraping_errors:
                cursor.execute(
                    """
                    INSERT INTO scraping_errors (track_id, error_message, error_time)
                    VALUES (?, ?, ?)
                """,
                    (track.id, error, datetime.now()),
                )

            conn.commit()

            lyrics_info = (
                f", Paroles: {bool(getattr(track, 'lyrics', None))}"
                if hasattr(track, "lyrics")
                else ""
            )
            logger.info(
                f"Morceau sauvegardé: {track.title} (ID: {track.id}, Featuring: {getattr(track, 'is_featuring', False)}{lyrics_info})"
            )
            return track.id

    def _save_credit(self, cursor, track_id: int, credit: Credit):
        """Sauvegarde un crédit - VERSION SIMPLIFIÉE SANS VÉRIFICATION UNIQUE"""
        try:
            cursor.execute(
                """
                INSERT INTO credits (track_id, name, role, role_detail, source)
                VALUES (?, ?, ?, ?, ?)
            """,
                (track_id, credit.name, credit.role.value, credit.role_detail, credit.source),
            )
        except Exception as e:
            # Log mais ne pas arrêter le processus pour un crédit
            logger.debug(f"Erreur lors de la sauvegarde du crédit {credit.name}: {e}")

    def get_artist_by_name(self, name: str) -> Artist | None:
        """Récupère un artiste par son nom - VERSION CORRIGÉE"""
        try:
            logger.debug(f"🔍 Recherche de l'artiste: '{name}'")

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, name, genius_id, spotify_id, discogs_id FROM artists WHERE name = ?",
                    (name,),
                )
                row = cursor.fetchone()

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

                # Charger les tracks
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

    def get_artist_tracks(self, artist_id: int) -> list[Track]:
        """Récupère tous les morceaux d'un artiste - VERSION SANS YOUTUBE_URL"""
        tracks = []

        try:
            logger.info(f"🔍 Chargement des tracks pour artist_id: {artist_id}")

            with self._get_connection() as conn:
                cursor = conn.cursor()

                # ✅ ÉTAPE 1: Récupérer d'abord les infos de l'artiste
                cursor.execute(
                    "SELECT id, name, genius_id, spotify_id, discogs_id FROM artists WHERE id = ?",
                    (artist_id,),
                )
                artist_row = cursor.fetchone()

                if not artist_row:
                    logger.error(f"❌ Artiste avec ID {artist_id} non trouvé")
                    return tracks

                # ✅ ÉTAPE 2: Créer l'objet Artist
                from src.models import Artist

                artist = Artist(
                    id=artist_row["id"],
                    name=artist_row["name"],
                    genius_id=artist_row["genius_id"],
                    spotify_id=artist_row["spotify_id"],
                    discogs_id=artist_row["discogs_id"],
                )

                # Vérifier le nombre total
                cursor.execute("SELECT COUNT(*) FROM tracks WHERE artist_id = ?", (artist_id,))
                total_count = cursor.fetchone()[0]
                logger.info(f"📊 {total_count} tracks trouvés en base")

                if total_count == 0:
                    return tracks

                # Accès par nom de colonne (sqlite3.Row) : l'ordre des colonnes
                # ne compte plus, le schéma est garanti par _init_database.
                cursor.execute(
                    "SELECT * FROM tracks WHERE artist_id = ? ORDER BY title",
                    (artist_id,),
                )

                rows = cursor.fetchall()
                logger.info(f"📦 {len(rows)} lignes récupérées")

                # Création des objets Track via le mapper (coercitions centralisées)
                for i, row in enumerate(rows):
                    try:
                        track = track_from_row(row, artist)
                        if track is None:
                            continue

                        # Chargement crédits (a besoin du curseur → hors mapper)
                        try:
                            track.credits = self._get_track_credits(cursor, row["id"])
                        except Exception:
                            track.credits = []

                        tracks.append(track)

                        if i < 5:
                            logger.info(f"✅ Track {i+1}: {track.title}")

                    except Exception as track_error:
                        logger.error(f"❌ Erreur track {i}: {track_error}")
                        continue

                # Compter les tracks avec musical_key
                tracks_with_key = sum(
                    1 for t in tracks if hasattr(t, "musical_key") and t.musical_key
                )
                logger.info(
                    f"✅ {len(tracks)} tracks chargés avec succès ({tracks_with_key} avec musical_key)"
                )

        except Exception as e:
            logger.error(f"❌ Erreur dans get_artist_tracks: {e}")

        return tracks

    def _get_track_credits(self, cursor, track_id: int) -> list[Credit]:
        """Récupère les crédits d'un morceau - VERSION ROBUSTE"""
        credits = []

        try:
            cursor.execute("SELECT * FROM credits WHERE track_id = ?", (track_id,))
            credit_rows = cursor.fetchall()

            for row in credit_rows:
                try:
                    name = row["name"]
                    role_str = row["role"]
                    role_detail = row["role_detail"]
                    source = row["source"] or "genius"

                    if name and role_str:
                        from src.models import Credit, CreditRole

                        # Conversion du rôle string vers enum
                        try:
                            role = CreditRole(role_str)
                        except ValueError:
                            role = CreditRole.OTHER

                        credit = Credit(
                            name=str(name),
                            role=role,
                            role_detail=role_detail,
                            source=str(source),
                        )
                        credits.append(credit)

                except Exception as credit_error:
                    logger.debug(f"Erreur crédit: {credit_error}")
                    continue

        except Exception as e:
            logger.debug(f"Erreur _get_track_credits: {e}")

        return credits

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

    def delete_artist(self, artist_name: str) -> bool:
        """Supprime un artiste et toutes ses données associées"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Récupérer l'ID de l'artiste
                cursor.execute("SELECT id FROM artists WHERE name = ?", (artist_name,))
                artist_row = cursor.fetchone()

                if not artist_row:
                    logger.warning(f"Artiste non trouvé: {artist_name}")
                    return False

                artist_id = artist_row["id"]

                # Supprimer dans l'ordre (contraintes de clés étrangères)

                # 1. Supprimer les erreurs de scraping
                cursor.execute(
                    "DELETE FROM scraping_errors WHERE track_id IN (SELECT id FROM tracks WHERE artist_id = ?)",
                    (artist_id,),
                )
                deleted_errors = cursor.rowcount

                # 2. Supprimer les crédits
                cursor.execute(
                    "DELETE FROM credits WHERE track_id IN (SELECT id FROM tracks WHERE artist_id = ?)",
                    (artist_id,),
                )
                deleted_credits = cursor.rowcount

                # 3. Supprimer les morceaux
                cursor.execute("DELETE FROM tracks WHERE artist_id = ?", (artist_id,))
                deleted_tracks = cursor.rowcount

                # 4. Supprimer l'artiste
                cursor.execute("DELETE FROM artists WHERE id = ?", (artist_id,))
                deleted_artist = cursor.rowcount

                conn.commit()

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
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Informations de base de l'artiste
                cursor.execute(
                    """
                    SELECT id, name, genius_id, spotify_id, discogs_id, created_at, updated_at
                    FROM artists WHERE name = ?
                """,
                    (artist_name,),
                )

                artist_row = cursor.fetchone()
                if not artist_row:
                    return {}

                artist_id = artist_row["id"]

                # Compter les morceaux et crédits
                cursor.execute(
                    """
                    SELECT 
                        COUNT(DISTINCT t.id) as tracks_count,
                        COUNT(DISTINCT c.id) as credits_count
                    FROM tracks t
                    LEFT JOIN credits c ON t.id = c.track_id
                    WHERE t.artist_id = ?
                """,
                    (artist_id,),
                )

                counts = cursor.fetchone()

                # Morceaux récents
                cursor.execute(
                    """
                    SELECT 
                        t.title, 
                        t.album, 
                        t.release_date,
                        COUNT(c.id) as credits_count
                    FROM tracks t
                    LEFT JOIN credits c ON t.id = c.track_id
                    WHERE t.artist_id = ?
                    GROUP BY t.id, t.title, t.album, t.release_date
                    ORDER BY t.updated_at DESC
                    LIMIT 20
                """,
                    (artist_id,),
                )

                recent_tracks = []
                for row in cursor.fetchall():
                    recent_tracks.append(
                        {
                            "title": row["title"],
                            "album": row["album"],
                            "release_date": row["release_date"],
                            "credits_count": row["credits_count"],
                        }
                    )

                # Crédits par rôle
                cursor.execute(
                    """
                    SELECT role, COUNT(*) as count
                    FROM credits c
                    JOIN tracks t ON c.track_id = t.id
                    WHERE t.artist_id = ?
                    GROUP BY role
                    ORDER BY count DESC
                """,
                    (artist_id,),
                )

                credits_by_role = {}
                for row in cursor.fetchall():
                    credits_by_role[row["role"]] = row["count"]

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
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT ytm_channel_id FROM artists WHERE id = ?", (artist_id,)
                ).fetchone()
                return row["ytm_channel_id"] if row and row["ytm_channel_id"] else None
        except Exception as e:
            logger.error(f"Erreur get_artist_ytm_channel: {e}")
            return None

    def set_artist_ytm_channel(self, artist_id: int, channel_id: str) -> bool:
        """Épingle le canal YTMusic d'un artiste (résout les homonymes)."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE artists SET ytm_channel_id = ? WHERE id = ?", (channel_id, artist_id)
                )
                conn.commit()
                logger.info(f"📌 Canal YTM épinglé pour artist_id={artist_id}: {channel_id}")
                return True
        except Exception as e:
            logger.error(f"Erreur set_artist_ytm_channel: {e}")
            return False

    def delete_track(self, track_id: int) -> bool:
        """Supprime définitivement un morceau et ses données associées"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM credits WHERE track_id = ?", (track_id,))
                cursor.execute("DELETE FROM scraping_errors WHERE track_id = ?", (track_id,))
                cursor.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
                deleted = cursor.rowcount
                conn.commit()
                logger.info(f"🗑️ Track {track_id} supprimé ({deleted} ligne(s))")
                return deleted > 0
        except Exception as e:
            logger.error(f"Erreur suppression track {track_id}: {e}")
            return False

    def merge_tracks(self, keep_id: int, delete_id: int) -> bool:
        """Fusionne delete_id dans keep_id : transfère les crédits (en écartant
        ceux déjà présents à l'identique sur le morceau conservé) et les erreurs
        de scraping, puis supprime la ligne en doublon. Même mécanique que
        scripts/merge_duplicates.py + dédup. Le BACKUP est à faire par l'appelant
        AVANT (règle projet : backup avant toute opération destructive)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Crédits : ne transférer que ceux absents du morceau conservé
                cursor.execute(
                    """
                    DELETE FROM credits WHERE track_id = ? AND EXISTS (
                        SELECT 1 FROM credits k WHERE k.track_id = ?
                          AND k.name = credits.name AND k.role = credits.role
                          AND IFNULL(k.role_detail, '') = IFNULL(credits.role_detail, '')
                    )""",
                    (delete_id, keep_id),
                )
                cursor.execute(
                    "UPDATE credits SET track_id = ? WHERE track_id = ?", (keep_id, delete_id)
                )
                transferred = cursor.rowcount
                cursor.execute(
                    "UPDATE scraping_errors SET track_id = ? WHERE track_id = ?",
                    (keep_id, delete_id),
                )
                cursor.execute("DELETE FROM tracks WHERE id = ?", (delete_id,))
                conn.commit()
                logger.info(
                    f"🔀 Track {delete_id} fusionné dans {keep_id} "
                    f"({transferred} crédit(s) transféré(s))"
                )
                return True
        except Exception as e:
            logger.error(f"Erreur fusion track {delete_id} → {keep_id}: {e}")
            return False

    def force_update_track_credits(self, track: Track) -> int:
        """Force la mise à jour complète des crédits d'un morceau - VERSION PRÉSERVANT FEATURES"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # ✅ CORRECTION: Récupérer les infos featuring AVANT suppression
                cursor.execute(
                    """
                    SELECT is_featuring, primary_artist_name, featured_artists 
                    FROM tracks WHERE id = ?
                """,
                    (track.id,),
                )

                featuring_info = cursor.fetchone()

                if featuring_info:
                    # Préserver les infos featuring sur l'objet track
                    track.is_featuring = bool(featuring_info["is_featuring"])
                    track.primary_artist_name = featuring_info["primary_artist_name"]
                    track.featured_artists = featuring_info["featured_artists"]
                    logger.info(f"🔒 Infos featuring préservées pour {track.title}")
                else:
                    track.is_featuring = False

                # Supprimer TOUS les anciens crédits
                cursor.execute("DELETE FROM credits WHERE track_id = ?", (track.id,))
                deleted_count = cursor.rowcount
                logger.info(f"🗑️ {deleted_count} anciens crédits supprimés pour '{track.title}'")

                # Supprimer les anciennes erreurs de scraping
                cursor.execute("DELETE FROM scraping_errors WHERE track_id = ?", (track.id,))
                deleted_errors = cursor.rowcount
                if deleted_errors > 0:
                    logger.info(f"🗑️ {deleted_errors} anciennes erreurs supprimées")

                # Remettre à zéro les métadonnées de scraping (MAIS PRÉSERVER FEATURING)
                cursor.execute(
                    """
                    UPDATE tracks 
                    SET last_scraped = NULL,
                        genre = CASE 
                            WHEN genre IS NOT NULL AND genre != '' THEN genre 
                            ELSE NULL 
                        END
                    WHERE id = ?
                """,
                    (track.id,),
                )

                # Sauvegarder les nouveaux crédits
                for credit in track.credits:
                    self._save_credit(cursor, track.id, credit)

                # Mettre à jour le track complet (EN PRÉSERVANT LES FEATURES)
                cursor.execute(
                    """
                    UPDATE tracks 
                    SET album = ?, track_number = ?, release_date = ?, 
                        genius_id = ?, spotify_id = ?, discogs_id = ?,
                        bpm = ?, duration = ?, genre = ?,
                        genius_url = ?, spotify_url = ?,
                        is_featuring = ?, primary_artist_name = ?, featured_artists = ?,
                        updated_at = ?, last_scraped = ?
                    WHERE id = ?
                """,
                    (
                        track.album,
                        getattr(track, "track_number", None),
                        track.release_date,
                        track.genius_id,
                        track.spotify_id,
                        track.discogs_id,
                        track.bpm,
                        track.duration,
                        track.genre,
                        track.genius_url,
                        track.spotify_url,
                        getattr(track, "is_featuring", False),
                        getattr(track, "primary_artist_name", None),
                        getattr(track, "featured_artists", None),
                        datetime.now(),
                        track.last_scraped,
                        track.id,
                    ),
                )

                # Sauvegarder les nouvelles erreurs s'il y en a
                for error in track.scraping_errors:
                    cursor.execute(
                        """
                        INSERT INTO scraping_errors (track_id, error_message, error_time)
                        VALUES (?, ?, ?)
                    """,
                        (track.id, error, datetime.now()),
                    )

                conn.commit()

                new_credits_count = len(track.credits)
                logger.info(
                    f"✅ Mise à jour forcée terminée pour '{track.title}': {new_credits_count} nouveaux crédits (Featuring préservé: {getattr(track, 'is_featuring', False)})"
                )

                return new_credits_count

        except Exception as e:
            logger.error(f"❌ Erreur lors de la mise à jour forcée: {e}")
            return 0

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

    # ──────────────────────────────────────────────────────────────────────────
    # Kworb — streams Spotify
    # ──────────────────────────────────────────────────────────────────────────

    def update_track_spotify_streams(
        self, track_id: int, streams: int, daily_streams: int, updated_at=None
    ) -> bool:
        """Met à jour les streams Kworb d'un morceau.

        updated_at : date "Last updated" de la page Kworb (fraîcheur réelle),
        sinon now().
        """
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tracks
                    SET spotify_streams = ?, spotify_daily_streams = ?, spotify_streams_updated = ?
                    WHERE id = ?
                """,
                    (streams, daily_streams, updated_at or datetime.now(), track_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur update_track_spotify_streams (track_id={track_id}): {e}")
            return False

    def update_track_spotify_id(self, track_id: int, spotify_id: str) -> bool:
        """Backfill du Spotify Track ID (ex: depuis les liens des pages Kworb).
        Ne remplace jamais un ID existant."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tracks SET spotify_id = ?
                    WHERE id = ? AND (spotify_id IS NULL OR spotify_id = '')
                """,
                    (spotify_id, track_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur update_track_spotify_id (track_id={track_id}): {e}")
            return False

    def clear_track_album(self, track_id: int) -> bool:
        """Détache un morceau de son album (édition MANUELLE : album_override=1
        empêche l'API de re-remplir le champ au prochain prefill)."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tracks SET album = NULL, album_override = 1, updated_at = ?
                    WHERE id = ?
                """,
                    (datetime.now(), track_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur clear_track_album (track_id={track_id}): {e}")
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
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE artists
                    SET kworb_total_streams = COALESCE(?, kworb_total_streams),
                        kworb_daily_streams = COALESCE(?, kworb_daily_streams),
                        kworb_lead_streams  = COALESCE(?, kworb_lead_streams),
                        kworb_feat_streams  = COALESCE(?, kworb_feat_streams),
                        kworb_updated       = COALESCE(?, kworb_updated),
                        updated_at = ?
                    WHERE id = ?
                """,
                    (total, daily, lead, feat, kworb_date, datetime.now(), artist_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur update_artist_kworb_totals (artist_id={artist_id}): {e}")
            return False

    def upsert_album(
        self,
        artist_id: int,
        title: str,
        streams: int,
        daily_streams: int,
        spotify_album_ids: str = None,
        updated_at=None,
    ) -> bool:
        """Insère ou met à jour un album avec ses données Kworb.

        spotify_album_ids : IDs Spotify des éditions agrégées, séparés par des
        virgules (un même titre peut couvrir plusieurs éditions — streams sommés
        par l'appelant).
        """
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO albums (title, artist_id, spotify_streams, spotify_daily_streams,
                                        spotify_streams_updated, spotify_album_ids)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(title, artist_id) DO UPDATE SET
                        spotify_streams = excluded.spotify_streams,
                        spotify_daily_streams = excluded.spotify_daily_streams,
                        spotify_streams_updated = excluded.spotify_streams_updated,
                        spotify_album_ids = COALESCE(excluded.spotify_album_ids, spotify_album_ids)
                """,
                    (
                        title,
                        artist_id,
                        streams,
                        daily_streams,
                        updated_at or datetime.now(),
                        spotify_album_ids,
                    ),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur upsert_album (artist_id={artist_id}, title={title!r}): {e}")
            return False

    def get_albums_for_artist(self, artist_id: int) -> list[dict[str, Any]]:
        """Retourne les albums d'un artiste triés par streams décroissants."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT title, spotify_streams, spotify_daily_streams,
                           spotify_streams_updated, ytm_streams
                    FROM albums WHERE artist_id = ?
                    ORDER BY spotify_streams DESC
                """,
                    (artist_id,),
                )
                rows = cursor.fetchall()
                return [
                    {
                        "title": row["title"],
                        "spotify_streams": row["spotify_streams"],
                        "spotify_daily_streams": row["spotify_daily_streams"],
                        "spotify_streams_updated": row["spotify_streams_updated"],
                        "ytm_streams": row["ytm_streams"],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.error(f"Erreur get_albums_for_artist (artist_id={artist_id}): {e}")
            return []

    def update_track_ytm_streams(self, track_id: int, streams: int) -> bool:
        """Met à jour les streams YouTube Music d'un morceau."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tracks
                    SET ytm_streams = ?, ytm_streams_updated = ?
                    WHERE id = ?
                """,
                    (streams, datetime.now(), track_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur update_track_ytm_streams (track_id={track_id}): {e}")
            return False

    def update_track_youtube_url(self, track_id: int, url: str, source: str) -> bool:
        """Persiste le lien YouTube d'un morceau + sa provenance.

        Priorité des sources : 'manual' (choix utilisateur) ≥ 'genius_media' >
        'search_auto'. Un lien 'manual' ou 'genius_media' écrase n'importe quoi ;
        un 'search_auto' ne remplace JAMAIS un 'genius_media' ni un 'manual'.
        """
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tracks
                    SET youtube_url = ?, youtube_url_source = ?, updated_at = ?
                    WHERE id = ?
                      AND (? IN ('manual', 'genius_media')
                           OR youtube_url IS NULL
                           OR youtube_url = ''
                           OR COALESCE(youtube_url_source, '') NOT IN ('manual', 'genius_media'))
                """,
                    (url, source, datetime.now(), track_id, source),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur update_track_youtube_url (track_id={track_id}): {e}")
            return False

    def rename_track(self, track_id: int, new_title: str) -> bool:
        """Renomme un morceau en base (ex. « Matrix (Intro) » → « Matrix » pour
        aligner sur Kworb). Échoue si le titre existe déjà pour l'artiste
        (contrainte UNIQUE(title, artist_id))."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tracks SET title = ?, updated_at = ? WHERE id = ?
                """,
                    (new_title.strip(), datetime.now(), track_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur rename_track (track_id={track_id}): {e}")
            return False

    def clear_track_youtube_link(self, track_id: int) -> bool:
        """Efface le lien YouTube et sa provenance (repasse en recherche live)."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tracks
                    SET youtube_url = NULL, youtube_url_source = NULL, updated_at = ?
                    WHERE id = ?
                """,
                    (datetime.now(), track_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Erreur clear_track_youtube_link (track_id={track_id}): {e}")
            return False

    def update_album_ytm_streams(self, artist_id: int, title: str, streams: int) -> bool:
        """Met à jour les streams YouTube Music d'un album."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE albums SET ytm_streams = ?, ytm_streams_updated = ?
                    WHERE title = ? AND artist_id = ?
                """,
                    (streams, datetime.now(), title, artist_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(
                f"Erreur update_album_ytm_streams (artist_id={artist_id}, title={title!r}): {e}"
            )
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
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE artists
                    SET spotify_monthly_listeners = COALESCE(?, spotify_monthly_listeners),
                        ytm_monthly_listeners     = COALESCE(?, ytm_monthly_listeners),
                        updated_at = ?
                    WHERE id = ?
                """,
                    (spotify_listeners, ytm_listeners, datetime.now(), artist_id),
                )
                conn.execute(
                    """
                    INSERT INTO monthly_listeners_history
                        (artist_id, spotify_listeners, ytm_listeners, total_estimated, recorded_at)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (artist_id, spotify_listeners, ytm_listeners, total, datetime.now()),
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Erreur update_artist_monthly_listeners (id={artist_id}): {e}")
            return False

    def get_monthly_listeners_history(self, artist_id: int) -> list[dict[str, Any]]:
        """Retourne l'historique des auditeurs mensuels d'un artiste."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT spotify_listeners, ytm_listeners, total_estimated, recorded_at
                    FROM monthly_listeners_history
                    WHERE artist_id = ?
                    ORDER BY recorded_at DESC
                """,
                    (artist_id,),
                )
                return [
                    {
                        "spotify_listeners": r["spotify_listeners"],
                        "ytm_listeners": r["ytm_listeners"],
                        "total_estimated": r["total_estimated"],
                        "recorded_at": r["recorded_at"],
                    }
                    for r in cursor.fetchall()
                ]
        except Exception as e:
            logger.error(f"Erreur get_monthly_listeners_history (id={artist_id}): {e}")
            return []

    def update_artist_spotify_id(self, artist_id: int, spotify_id: str) -> bool:
        """Met à jour le spotify_id d'un artiste."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE artists SET spotify_id = ?, updated_at = ? WHERE id = ?
                """,
                    (spotify_id, datetime.now(), artist_id),
                )
                conn.commit()
                logger.info(f"spotify_id artiste #{artist_id} mis à jour: {spotify_id}")
                return True
        except Exception as e:
            logger.error(f"Erreur update_artist_spotify_id (artist_id={artist_id}): {e}")
            return False
