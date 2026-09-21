"""e28 — `track_spotify_ids.kind` : l'existant est une ÉDITION, rien d'autre ne change.

Vérifié sur une base arrêtée à la révision précédente, comme le sera une base
réelle au moment de la migration : chaque ligne existante reçoit `kind='edition'`
et des colonnes de compteur VIDES — une rendition ne s'invente pas rétroactivement.
"""

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine

from alembic import command
from src.persistence.bootstrap import make_alembic_config

_AVANT = "e27_instrumental"
_APRES = "e28_renditions"


def _upgrade(db_path: Path, revision: str) -> None:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as conn:
            command.upgrade(make_alembic_config(conn), revision)
            conn.commit()
    finally:
        engine.dispose()


def _base_peuplee(tmp_path: Path) -> Path:
    db = tmp_path / "avant_e28.db"
    _upgrade(db, _AVANT)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Artiste')")
        conn.execute(
            "INSERT INTO tracks (id, title, artist_id, spotify_id) "
            "VALUES (1, 'DKR', 1, '3VXzVGAWFSrH47dBtTOPws')"
        )
        conn.execute(
            "INSERT INTO track_spotify_ids (track_id, spotify_id, source, is_primary) "
            "VALUES (1, '3VXzVGAWFSrH47dBtTOPws', 'legacy', 1)"
        )
    return db


def test_lexistant_devient_une_edition_sans_compteur(tmp_path):
    db = _base_peuplee(tmp_path)

    _upgrade(db, _APRES)

    with sqlite3.connect(db) as conn:
        ligne = conn.execute(
            "SELECT kind, label, streams, daily_streams, streams_at, source, is_primary "
            "FROM track_spotify_ids"
        ).fetchone()
    assert ligne == ("edition", None, None, None, None, "legacy", 1)


def test_une_rendition_peut_etre_ecrite_apres(tmp_path):
    db = _base_peuplee(tmp_path)
    _upgrade(db, _APRES)

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO track_spotify_ids (track_id, spotify_id, source, kind, label, streams) "
            "VALUES (1, '5go793BaOjzfop2JwctQ0r', 'kworb', 'rendition', 'DKR - Bonus Track', 108)"
        )
        kinds = conn.execute("SELECT kind FROM track_spotify_ids ORDER BY kind").fetchall()
    assert kinds == [("edition",), ("rendition",)]
