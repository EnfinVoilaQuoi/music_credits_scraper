"""Tests des migrations de schéma versionnées (PRAGMA user_version).

Deux scénarios critiques :
- Base NEUVE : le schéma de départ (CREATE TABLE minimal) est amené au dernier
  user_version, toutes les colonnes étendues ajoutées.
- Bootstrap d'une base type-PROD : colonnes déjà présentes (ancien mécanisme
  ALTER) mais user_version encore 0 → run_migrations ne doit PAS planter en
  re-ajoutant une colonne existante, et pose le bon user_version.
"""

import sqlite3

import pytest

from src.utils.data_manager import _MIGRATIONS, run_migrations

LATEST = _MIGRATIONS[-1][0]


def _base_schema(conn):
    """Schéma de départ minimal (colonnes v0), comme les CREATE TABLE de
    _init_database avant l'ère des migrations."""
    conn.execute("CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute(
        "CREATE TABLE tracks (id INTEGER PRIMARY KEY, title TEXT, "
        "artist_id INTEGER, youtube_url TEXT)"  # youtube_url déjà en base
    )
    conn.execute("CREATE TABLE albums (id INTEGER PRIMARY KEY, title TEXT, artist_id INTEGER)")


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    _base_schema(c)
    yield c
    c.close()


class TestBaseNeuve:
    def test_user_version_atteint_le_dernier(self, conn):
        run_migrations(conn.cursor())
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST

    def test_colonnes_etendues_ajoutees(self, conn):
        run_migrations(conn.cursor())
        tracks = _columns(conn, "tracks")
        assert {"isrc", "bpm_source", "key", "mode", "album_override"} <= tracks
        assert "kworb_updated" in _columns(conn, "artists")
        assert "spotify_album_ids" in _columns(conn, "albums")

    def test_youtube_url_en_base_non_dupliquee(self, conn):
        # youtube_url est déjà dans le schéma de départ : la migration 40
        # doit être sautée (sinon ALTER en double → erreur).
        run_migrations(conn.cursor())
        cols = [r[1] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()]
        assert cols.count("youtube_url") == 1


class TestBootstrapProd:
    def test_reprise_sur_base_deja_peuplee(self, conn):
        # 1er passage : construit le schéma complet (comme une base réelle)
        run_migrations(conn.cursor())
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST

        # Simule une base PROD passée par l'ancien mécanisme : colonnes
        # présentes mais user_version jamais posé.
        conn.execute("PRAGMA user_version = 0")

        # 2e passage : NE DOIT PAS planter (chaque ADD COLUMN sauté si présent)
        run_migrations(conn.cursor())
        assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST

    def test_backfill_youtube_source(self, conn):
        run_migrations(conn.cursor())
        conn.execute(
            "INSERT INTO tracks (title, youtube_url, youtube_url_source) "
            "VALUES ('X', 'http://y', NULL)"
        )
        conn.execute("PRAGMA user_version = 45")  # avant la migration backfill (46)
        run_migrations(conn.cursor())
        row = conn.execute("SELECT youtube_url_source FROM tracks WHERE title='X'").fetchone()
        assert row[0] == "genius_media"


class TestIdempotence:
    def test_second_passage_noop(self, conn):
        run_migrations(conn.cursor())
        v1 = conn.execute("PRAGMA user_version").fetchone()[0]
        run_migrations(conn.cursor())  # rien à faire
        v2 = conn.execute("PRAGMA user_version").fetchone()[0]
        assert v1 == v2 == LATEST


def test_datamanager_pose_le_dernier_user_version(data_manager):
    """Une base créée par DataManager (fresh) est au dernier user_version."""
    with sqlite3.connect(data_manager.db_path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == LATEST
