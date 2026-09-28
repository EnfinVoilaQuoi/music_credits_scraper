"""e40 — la vidéo principale insérée sans provenance récupère celle que la fiche
porte (`tracks.youtube_url_source`) ; aucune autre ligne n'est touchée."""

import sqlite3
from contextlib import closing
from pathlib import Path

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


def test_seule_la_video_du_lien_recoit_la_provenance(tmp_path):
    db = tmp_path / "avant_e40.db"
    _upgrade(db, "e39_revue_corrections")
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Isha')")
        conn.execute(
            "INSERT INTO tracks (id, title, artist_id, youtube_url, youtube_url_source) "
            "VALUES (1, 'Durag', 1, 'https://www.youtube.com/watch?v=AAAAAAAAAAA', 'genius_media')"
        )
        conn.executemany(
            "INSERT INTO track_videos (track_id, video_id, source) VALUES (1, ?, ?)",
            [("AAAAAAAAAAA", None), ("BBBBBBBBBBB", None), ("CCCCCCCCCCC", "ytm_album")],
        )
    _upgrade(db, "e40_provenance_videos")
    with closing(sqlite3.connect(db)) as conn:
        assert dict(conn.execute("SELECT video_id, source FROM track_videos").fetchall()) == {
            "AAAAAAAAAAA": "genius_media",
            "BBBBBBBBBBB": None,  # pas la vidéo du lien : rien d'inventé
            "CCCCCCCCCCC": "ytm_album",
        }
