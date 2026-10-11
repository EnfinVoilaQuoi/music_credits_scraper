"""e47 — la clé d'un crédit tient face aux NULL ; `tracks.musical_key` part.

Deux crédits identiques à `role_detail` NULL passaient l'UNIQUE de la table
(deux NULL sont distincts) : 12 doublons en base. La migration les retire en
gardant le crédit Genius, sinon le plus ancien, puis l'index l'interdit."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from alembic import command
from src.persistence.bootstrap import make_alembic_config


def _upgrade(db_path: Path, revision: str) -> None:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as conn:
            command.upgrade(make_alembic_config(conn), revision)
            conn.commit()
    finally:
        engine.dispose()


@pytest.fixture
def base(tmp_path):
    db = tmp_path / "avant_e47.db"
    _upgrade(db, "e46_source_track_absences")
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Booba')")
        conn.execute("INSERT INTO tracks (id, title, artist_id) VALUES (1, 'DKR', 1)")
        conn.execute("ALTER TABLE tracks ADD COLUMN musical_key TEXT")  # la colonne oubliée
        conn.executemany(
            "INSERT INTO credits (id, track_id, name, role, role_detail, source) "
            "VALUES (?, 1, ?, ?, ?, ?)",
            [
                (1, "Pinkman", "Producer", None, "youtube_clip"),
                (2, "Pinkman", "Producer", None, "genius"),
                (3, "Akalex", "Featured Artist", None, "deezer"),
                (4, "Akalex", "Featured Artist", None, "deezer"),
                (5, "Akalex", "Featured Artist", "", "deezer"),
                (6, "Tysko", "Producer", "Co-", "genius"),
            ],
        )
    return db


def test_doublons_retires_genius_garde(base):
    _upgrade(base, "e47_credits_cle_null_sure")
    with closing(sqlite3.connect(base)) as conn:
        assert [r[0] for r in conn.execute("SELECT id FROM credits ORDER BY id")] == [2, 3, 6]


def test_l_index_refuse_un_nouveau_doublon(base):
    _upgrade(base, "e47_credits_cle_null_sure")
    with closing(sqlite3.connect(base)) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO credits (track_id, name, role, role_detail, source) "
            "VALUES (1, 'Akalex', 'Featured Artist', '', 'genius')"
        )


def test_musical_key_retiree(base):
    _upgrade(base, "e47_credits_cle_null_sure")
    with closing(sqlite3.connect(base)) as conn:
        colonnes = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
        assert "musical_key" not in colonnes
        assert conn.execute("SELECT title FROM tracks WHERE id = 1").fetchone() == ("DKR",)
