"""e62 — `track_videos.duration` : la colonne naît vide, les lignes en place
restent intactes, et le retour en arrière la retire."""

import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import create_engine

from alembic import command
from src.persistence.bootstrap import make_alembic_config


def _migrer(db_path: Path, revision: str, *, descendre: bool = False) -> None:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as conn:
            config = make_alembic_config(conn)
            if descendre:
                command.downgrade(config, revision)
            else:
                command.upgrade(config, revision)
            conn.commit()
    finally:
        engine.dispose()


def _colonnes(db_path: Path) -> list[str]:
    with closing(sqlite3.connect(db_path)) as conn:
        return [r[1] for r in conn.execute("PRAGMA table_info(track_videos)")]


def test_la_colonne_nait_vide_sans_toucher_aux_lignes(tmp_path):
    db = tmp_path / "avant_e62.db"
    _migrer(db, "e61_lectures_compteurs")
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Isha')")
        conn.execute("INSERT INTO tracks (id, title, artist_id) VALUES (1, 'Magot', 1)")
        conn.execute(
            "INSERT INTO track_videos (track_id, video_id, views, title) "
            "VALUES (1, 'AAAAAAAAAAA', 12, 'Isha - Magot')"
        )
    _migrer(db, "e62_videos_duree")
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute(
            "SELECT video_id, views, title, duration FROM track_videos"
        ).fetchall() == [("AAAAAAAAAAA", 12, "Isha - Magot", None)]


def test_le_retour_en_arriere_retire_la_colonne(tmp_path):
    db = tmp_path / "e62.db"
    _migrer(db, "e62_videos_duree")
    assert "duration" in _colonnes(db)
    _migrer(db, "e61_lectures_compteurs", descendre=True)
    assert "duration" not in _colonnes(db)
