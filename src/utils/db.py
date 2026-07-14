"""Connexion SQLite, schéma de base et migrations versionnées.

`Database` centralise l'accès physique à la base (`connect()`), la création du
schéma de départ (`init_schema()`) et l'application des migrations
(`run_migrations`). Les repositories (track/artist) et la façade `DataManager`
en dépendent — c'est le seul endroit qui connaît le fichier SQLite.
"""

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from src.utils.logger import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Migrations de schéma versionnées (PRAGMA user_version)
# ──────────────────────────────────────────────────────────────────────────
#
# Séquence FIGÉE : une entrée par évolution, dans l'ordre, JAMAIS modifiée
# après coup (on ajoute uniquement à la fin). Les CREATE TABLE de
# init_schema posent le schéma de départ ; toute colonne ajoutée depuis
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
