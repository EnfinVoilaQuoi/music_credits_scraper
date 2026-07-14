"""E1d — bootstrap Alembic : les bases pré-Alembic sont stampées, en permanence.

Une base créée par `db.py` (legacy, sans `alembic_version`) doit être stampée à
la révision de base pour qu'`alembic upgrade head` (E3) la reconnaisse au lieu
de vouloir recréer le schéma. Le bootstrap est PERMANENT : un backup pré-Alembic
restauré (donc sans `alembic_version`) est re-stampé au prochain démarrage.
"""

import sqlite3
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from src.persistence.bootstrap import (
    LEGACY_HEAD_REVISION,
    ensure_stamped,
    make_alembic_config,
)
from src.utils.db import Database


def _versions(db_path: str):
    """Contenu de alembic_version, ou None si la table n'existe pas."""
    with sqlite3.connect(db_path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        if not exists:
            return None
        return [r[0] for r in conn.execute("SELECT version_num FROM alembic_version")]


def _current_revision(db_path: str) -> str | None:
    engine = create_engine(f"sqlite:///{Path(db_path).as_posix()}")
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def test_fresh_db_est_stampe(tmp_path):
    db = Database(str(tmp_path / "fresh.db"))
    assert _versions(db.db_path) == [LEGACY_HEAD_REVISION]


def test_bootstrap_idempotent(tmp_path):
    path = str(tmp_path / "twice.db")
    Database(path)
    Database(path)  # 2e init : ne doit pas dupliquer ni échouer
    assert _versions(path) == [LEGACY_HEAD_REVISION]


def test_backup_sans_version_est_restampe(tmp_path):
    # Simule un backup pré-Alembic : base legacy dont on retire alembic_version.
    path = str(tmp_path / "restored.db")
    Database(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE alembic_version")
        conn.commit()
    assert _versions(path) is None

    ensure_stamped(path)  # re-démarrage
    assert _versions(path) == [LEGACY_HEAD_REVISION]


def test_backup_ancien_sous_46_est_rattrape_puis_stampe(tmp_path):
    """Un vieux backup pré-Alembic à user_version < 46 (colonnes manquantes) doit
    être RATTRAPÉ (colonnes ajoutées jusqu'à 46) PUIS stampé — pas juste stampé,
    sinon le stamp mentirait sur le schéma (E3b, base restaurée)."""
    path = str(tmp_path / "vieux.db")
    with sqlite3.connect(path) as conn:
        # Schéma « d'origine » (avant migrations user_version) : les colonnes de
        # base uniquement, user_version = 0, aucune alembic_version.
        conn.executescript("""
            CREATE TABLE artists (id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE, genius_id INTEGER, spotify_id TEXT,
                discogs_id INTEGER, spotify_monthly_listeners INTEGER,
                ytm_monthly_listeners INTEGER, created_at TIMESTAMP, updated_at TIMESTAMP);
            CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                artist_id INTEGER NOT NULL, album TEXT, track_number INTEGER,
                release_date TIMESTAMP, genius_id INTEGER, spotify_id TEXT, discogs_id INTEGER,
                bpm INTEGER, duration INTEGER, genre TEXT, genius_url TEXT, spotify_url TEXT,
                youtube_url TEXT, created_at TIMESTAMP, updated_at TIMESTAMP, last_scraped TIMESTAMP);
            CREATE TABLE albums (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                artist_id INTEGER NOT NULL, spotify_streams INTEGER, spotify_daily_streams INTEGER,
                spotify_streams_updated TIMESTAMP);
            """)
        conn.execute("PRAGMA user_version = 0")
        conn.commit()

    ensure_stamped(path)

    with sqlite3.connect(path) as conn:
        tcols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
        uv = conn.execute("PRAGMA user_version").fetchone()[0]
    # Échantillon de colonnes ajoutées par le rattrapage (parmi les 46 migrations).
    assert {"isrc", "bpm_source", "key", "mode", "spotify_streams", "album_override"} <= tcols
    assert uv == 46
    assert _versions(path) == [LEGACY_HEAD_REVISION]


def test_base_stampee_est_au_head_pour_alembic(tmp_path):
    # Propriété critique pour E3 : Alembic considère la base à jour (head), donc
    # `upgrade head` sera un no-op au lieu de recréer les tables.
    db = Database(str(tmp_path / "head.db"))
    assert _current_revision(db.db_path) == LEGACY_HEAD_REVISION

    # upgrade head : aucune erreur, aucune table recréée/perdue.
    with sqlite3.connect(db.db_path) as conn:
        avant = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    engine = create_engine(f"sqlite:///{Path(db.db_path).as_posix()}")
    try:
        from alembic import command

        with engine.connect() as conn:
            command.upgrade(make_alembic_config(conn), "head")
            conn.commit()
    finally:
        engine.dispose()
    with sqlite3.connect(db.db_path) as conn:
        apres = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert avant == apres
