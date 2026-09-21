"""e30 — `artists.deezer_id` : NULL tant que l'oracle n'a pas tranché ; lu partout où
l'artiste est relu (par nom, par id via les morceaux)."""

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine

from alembic import command
from src.models import Artist, Track
from src.persistence.bootstrap import make_alembic_config

_AVANT = "e29_variant_track_id"
_APRES = "e30_artist_deezer_id"


def _upgrade(db_path: Path, revision: str) -> None:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as conn:
            command.upgrade(make_alembic_config(conn), revision)
            conn.commit()
    finally:
        engine.dispose()


def test_la_colonne_arrive_vide(tmp_path):
    db = tmp_path / "avant_e30.db"
    _upgrade(db, _AVANT)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Isha')")
    _upgrade(db, _APRES)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT deezer_id FROM artists").fetchone() == (None,)


def test_ecrit_par_l_ecrivain_dedie_et_relu_partout(data_manager):
    artist = Artist(name="Isha")
    artist.id = data_manager.save_artist(artist)
    data_manager.save_track(Track(title="Durag", artist=artist))

    assert data_manager.update_artist_deezer_id(artist.id, 1236609)

    assert data_manager.get_artist_by_name("Isha").deezer_id == 1236609
    (track,) = data_manager.get_artist_tracks(artist.id)
    assert track.artist.deezer_id == 1236609
    assert data_manager.get_artist_details("Isha")["deezer_id"] == 1236609
