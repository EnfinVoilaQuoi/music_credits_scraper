"""e67 — `projets` : la migration réunit les éditions en place (M.A.N et son
Bonus), donne son propre projet à une apparition, et le retour en arrière retire
table et colonne."""

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


def _base_avant(tmp_path) -> Path:
    db = tmp_path / "avant_e67.db"
    _migrer(db, "e66_credits_album_genius")
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Josman')")
        for i in range(1, 20):
            conn.execute(
                "INSERT INTO tracks (id, title, artist_id) VALUES (?, ?, 1)", (i, f"Titre {i}")
            )
        parutions = [
            (10, "M.A.N (Black Roses & Lost Feelings)", "deezer:302453847", "own", "2022-03-18"),
            (
                11,
                "M.A.N (Black Roses & Lost Feelings) (Bonus)",
                "deezer:308500177",
                "own",
                "2022-04-29",
            ),
            (12, "Booska Pefra Vol.4", "deezer:1", "appearance", "2015-01-01"),
        ]
        for rid, titre, cle, scope, date in parutions:
            conn.execute(
                "INSERT INTO releases (id, artist_id, title, identity_key, scope, release_date, "
                "record_type) VALUES (?, 1, ?, ?, ?, ?, 'album')",
                (rid, titre, cle, scope, date),
            )
        for i in range(1, 20):
            conn.execute("INSERT INTO release_tracks (release_id, track_id) VALUES (10, ?)", (i,))
            conn.execute("INSERT INTO release_tracks (release_id, track_id) VALUES (11, ?)", (i,))
        conn.execute("INSERT INTO release_tracks (release_id, track_id) VALUES (12, 1)")
    return db


def test_les_editions_en_place_sont_reunies(tmp_path):
    db = _base_avant(tmp_path)
    _migrer(db, "e67_projets")
    with closing(sqlite3.connect(db)) as conn:
        projets = conn.execute(
            "SELECT id, artist_id, titre, edition_reference_id FROM projets"
        ).fetchall()
        assert len(projets) == 2  # M.A.N et la compilation, chacune le sien
        pid, artiste, titre, ref = projets[0]
        assert (artiste, titre, ref) == (1, "M.A.N (Black Roses & Lost Feelings)", 10)
        assert dict(conn.execute("SELECT id, projet_id FROM releases").fetchall()) == {
            10: pid,
            11: pid,
            12: projets[1][0],
        }


def test_le_retour_en_arriere_retire_table_et_colonne(tmp_path):
    db = _base_avant(tmp_path)
    _migrer(db, "e67_projets")
    _migrer(db, "e66_credits_album_genius", descendre=True)
    with closing(sqlite3.connect(db)) as conn:
        assert "projet_id" not in [r[1] for r in conn.execute("PRAGMA table_info(releases)")]
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'projets'"
        ).fetchone()
        assert conn.execute("SELECT COUNT(*) FROM releases").fetchone()[0] == 3
