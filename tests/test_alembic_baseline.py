"""E1 — la révision Alembic de base ne peut pas diverger de `schema.py`.

Trois chemins doivent produire LE MÊME schéma sur une base vide :
  1. `alembic upgrade head` (révision e1_initial_schema),
  2. `schema.metadata.create_all()` (SQLAlchemy Core),
  3. `db.py` (CREATE TABLE + migrations user_version — le legacy).

Si l'un diverge (colonne, type, NOT NULL, défaut, PK, UNIQUE), ce test rougit.
C'est le garde-fou qui autorise E3 à remplacer `_init_database` par
`alembic upgrade head` sans changer le schéma réel des bases existantes.
"""

import sqlite3
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine

from alembic import command
from src.persistence import schema
from src.utils.db import Database

_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config(db_path: str) -> Config:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{Path(db_path).as_posix()}")
    return cfg


def _snapshot(db_path: str) -> dict:
    """Empreinte du schéma : colonnes (table_info) + UNIQUE, hors alembic_version."""
    with sqlite3.connect(db_path) as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
            )
        ]
        snap = {}
        for t in sorted(tables):
            cols = []
            for r in conn.execute(f"PRAGMA table_info({t})"):
                name, typ, notnull, dflt, pk = r[1], r[2].upper(), r[3], r[4], r[5]
                # `INTEGER PRIMARY KEY` (legacy) vs `INTEGER NOT NULL PRIMARY KEY`
                # (SQLAlchemy) : notnull=0/1 selon la présence littérale de NOT
                # NULL, mais sémantiquement identiques (rowid jamais nul). On
                # neutralise cet écart cosmétique sur les PK.
                if pk:
                    notnull = 0
                cols.append((name, typ, notnull, dflt, pk))
            uniques = set()
            for idx in conn.execute(f"PRAGMA index_list({t})"):
                if idx[2]:  # unique
                    cols_idx = tuple(ic[2] for ic in conn.execute(f"PRAGMA index_info({idx[1]})"))
                    uniques.add(cols_idx)
            snap[t] = {"columns": cols, "uniques": uniques}
        return snap


def test_upgrade_head_equivaut_a_create_all(tmp_path):
    alembic_db = tmp_path / "alembic.db"
    createall_db = tmp_path / "createall.db"

    command.upgrade(_alembic_config(str(alembic_db)), "head")

    engine = create_engine(f"sqlite:///{createall_db.as_posix()}")
    schema.metadata.create_all(engine)
    engine.dispose()

    assert _snapshot(str(alembic_db)) == _snapshot(str(createall_db))


def test_upgrade_head_equivaut_au_schema_legacy(tmp_path):
    # Transitivité : la baseline Alembic reproduit aussi le schéma de db.py.
    alembic_db = tmp_path / "alembic.db"
    legacy_db = tmp_path / "legacy.db"

    command.upgrade(_alembic_config(str(alembic_db)), "head")
    Database(str(legacy_db))  # CREATE TABLE + migrations user_version

    assert _snapshot(str(alembic_db)) == _snapshot(str(legacy_db))
