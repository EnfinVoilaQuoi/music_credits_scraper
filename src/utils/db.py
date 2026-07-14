"""Connexion SQLite, schéma de base et migrations versionnées.

`Database` centralise l'accès physique à la base (`connect()`), la création du
schéma de départ (`init_schema()`) et l'application des migrations
(`run_migrations`). Les repositories (track/artist) et la façade `DataManager`
en dépendent — c'est le seul endroit qui connaît le fichier SQLite.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from src.persistence.legacy_migrations import run_migrations
from src.utils.logger import get_logger

logger = get_logger(__name__)


class Database:
    """Accès physique à la base SQLite : connexion, schéma, migrations."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        # Moteur SQLAlchemy Core (phase E2) : NullPool = une connexion par
        # opération, reproduit EXACTEMENT le comportement de `connect()`
        # (sqlite3) qu'il remplace progressivement, repository par repository.
        # Les deux visent le même fichier ; QueuePool = optimisation à évaluer
        # plus tard (au plus tôt en F).
        self.engine = create_engine(f"sqlite:///{Path(db_path).as_posix()}", poolclass=NullPool)
        self.init_schema()

    @contextmanager
    def connect(self):
        """Context manager pour les connexions à la base de données (sqlite3).

        Chemin legacy, en cours de remplacement par `self.engine` (Core, E2).
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def init_schema(self):
        """Crée le schéma de départ (idempotent) puis applique les migrations."""
        with self.connect() as conn:
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

            # Table des morceaux — DOIT être créée AVANT les migrations (bug
            # historique : les ALTER s'exécutaient avant le CREATE TABLE → base
            # neuve figée sur le vieux schéma, cf. AUDIT.md §3.1). Inclut les
            # colonnes historiques (lyrics, is_featuring...) qui n'étaient
            # couvertes ni par l'ancien CREATE TABLE ni par les migrations.
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

        # Bootstrap Alembic (phase E1d) : stampe une base pré-Alembic à la
        # révision de base (user_version 46 ≡ e1_initial_schema) pour qu'elle
        # soit reconnue par `alembic upgrade head` en E3. Hors du `with` : sa
        # propre connexion, aucune ne chevauche celle d'init. Idempotent.
        # Import paresseux : `bootstrap` importe `src.utils.logger`, or
        # `src.utils.__init__` tire `DataManager` → import de `db` — un import
        # au niveau module recréerait un cycle. Ici, `src.utils` est déjà chargé.
        from src.persistence.bootstrap import ensure_stamped

        ensure_stamped(self.db_path)
