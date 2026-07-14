"""E1 — garde-fou : le schéma SQLAlchemy Core reflète le schéma legacy À L'IDENTIQUE.

`src/persistence/schema.py` (MetaData/Table) doit décrire EXACTEMENT le schéma
produit aujourd'hui par `src/utils/db.py` (CREATE TABLE + migrations
`user_version`). Si une colonne manque, est en trop, ou change de type, ce test
rougit AVANT que la bascule Core (E2) ou la révision Alembic (E1c) ne s'appuient
sur un schéma faux.

On compare sur une base RÉELLE fraîchement créée par `db.py`, pas sur une copie
figée : chaque nouvelle migration `user_version` non reportée dans `schema.py`
sera donc immédiatement détectée.
"""

import sqlite3

from sqlalchemy.dialects import sqlite

from src.persistence import schema
from src.utils.db import Database

_SQLITE = sqlite.dialect()


def _db_tables(conn) -> set[str]:
    # alembic_version (créée par le bootstrap E1d) n'appartient pas au schéma
    # métier décrit par schema.py : on l'exclut de la comparaison.
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
    ).fetchall()
    return {r[0] for r in rows}


def _db_columns(conn, table: str) -> dict[str, str]:
    """{nom_colonne: type déclaré} d'après PRAGMA table_info."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1]: r[2].upper() for r in rows}


def _db_notnull_non_pk(conn, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows if r[3] and not r[5]}  # notnull=1 et pk=0


def _meta_columns(table) -> dict[str, str]:
    return {c.name: c.type.compile(dialect=_SQLITE).upper() for c in table.columns}


def _meta_notnull_non_pk(table) -> set[str]:
    return {c.name for c in table.columns if not c.nullable and not c.primary_key}


def _fresh_db(tmp_path) -> Database:
    return Database(str(tmp_path / "reflect.db"))


def test_meme_ensemble_de_tables(tmp_path):
    db = _fresh_db(tmp_path)
    with sqlite3.connect(db.db_path) as conn:
        # alembic_version n'existe pas encore (introduite en E1c) : on compare
        # les tables métier, qui doivent coïncider terme à terme.
        assert set(schema.metadata.tables) == _db_tables(conn)


def test_colonnes_et_types_identiques(tmp_path):
    db = _fresh_db(tmp_path)
    with sqlite3.connect(db.db_path) as conn:
        for name, table in schema.metadata.tables.items():
            assert _meta_columns(table) == _db_columns(
                conn, name
            ), f"Divergence colonnes/types sur '{name}'"


def test_contraintes_not_null_identiques(tmp_path):
    # Les NOT NULL métier (title, artist_id, name, role, track_id) doivent
    # coïncider. On exclut les clés primaires (SQLite rapporte notnull=0 sur un
    # INTEGER PRIMARY KEY même s'il est de fait non nul).
    db = _fresh_db(tmp_path)
    with sqlite3.connect(db.db_path) as conn:
        for name, table in schema.metadata.tables.items():
            assert _meta_notnull_non_pk(table) == _db_notnull_non_pk(
                conn, name
            ), f"Divergence NOT NULL sur '{name}'"


def test_metadata_couvre_les_46_migrations(tmp_path):
    # Filet supplémentaire : le nombre total de colonnes déclarées dans le
    # MetaData égale celui de la base legacy (aucune migration oubliée).
    db = _fresh_db(tmp_path)
    with sqlite3.connect(db.db_path) as conn:
        for name, table in schema.metadata.tables.items():
            assert len(table.columns) == len(
                _db_columns(conn, name)
            ), f"Nombre de colonnes différent sur '{name}'"
