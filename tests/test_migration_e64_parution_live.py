"""e64 — `releases.mb_live` : la colonne naît vide, les parutions en place
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
        return [r[1] for r in conn.execute("PRAGMA table_info(releases)")]


def test_la_colonne_nait_vide_sans_toucher_aux_parutions(tmp_path):
    db = tmp_path / "avant_e64.db"
    _migrer(db, "e63_videos_chaine")
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'MC Solaar')")
        conn.execute(
            "INSERT INTO releases (id, artist_id, title, identity_key, upc) "
            "VALUES (1, 1, 'Le tour de la question', 'deezer:43852', '3700368423851')"
        )
    _migrer(db, "e64_parution_live")
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT title, upc, mb_live FROM releases").fetchall() == [
            ("Le tour de la question", "3700368423851", None)
        ]


def test_le_retour_en_arriere_retire_la_colonne(tmp_path):
    db = tmp_path / "e64.db"
    _migrer(db, "e64_parution_live")
    assert "mb_live" in _colonnes(db)
    _migrer(db, "e63_videos_chaine", descendre=True)
    assert "mb_live" not in _colonnes(db)
