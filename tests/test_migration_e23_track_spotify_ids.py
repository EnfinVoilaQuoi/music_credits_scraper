"""e23 — le backfill verse dans `track_spotify_ids` l'ID déjà connu de chaque morceau.

Un backfill silencieusement inopérant est le pire des deux mondes : la table
existe, elle est vide, et le code qui la lit conclut « ce morceau n'a pas d'ID »
pour les 1 071 morceaux qui en avaient un. On le vérifie sur une base arrêtée à
la révision PRÉCÉDENTE, comme le sera une base réelle au moment de la migration.

La provenance versée est `legacy` et pas mieux : elle n'était nulle part avant
e23, et elle ne s'invente pas (même convention que le backfill audio d'e10).
"""

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine

from alembic import command
from src.persistence.bootstrap import make_alembic_config

_AVANT = "e22_artist_relations"
_APRES = "e23_track_spotify_ids"


def _upgrade(db_path: Path, revision: str) -> None:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as conn:
            command.upgrade(make_alembic_config(conn), revision)
            conn.commit()
    finally:
        engine.dispose()


def _base_peuplee(tmp_path: Path, lignes) -> Path:
    """`lignes` : (titre, spotify_id)."""
    db = tmp_path / "avant_e23.db"
    _upgrade(db, _AVANT)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Artiste')")
        for i, (titre, sid) in enumerate(lignes, start=1):
            conn.execute(
                "INSERT INTO tracks (id, title, artist_id, spotify_id) VALUES (?, ?, 1, ?)",
                (i, titre, sid),
            )
    return db


def _entrees(db: Path) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT track_id, spotify_id, source, is_primary FROM track_spotify_ids "
            "ORDER BY track_id"
        ).fetchall()


def test_le_backfill_reprend_lid_connu_de_chaque_morceau(tmp_path):
    db = _base_peuplee(tmp_path, [("Magot", "3VXzVGAWFSrH47dBtTOPws")])

    _upgrade(db, _APRES)

    assert _entrees(db) == [(1, "3VXzVGAWFSrH47dBtTOPws", "legacy", 1)]


def test_un_morceau_sans_id_nen_fabrique_pas(tmp_path):
    """NULL et chaîne vide disent la même chose — « aucun ID » — et ni l'un ni
    l'autre ne doit produire une ligne."""
    db = _base_peuplee(tmp_path, [("Sans ID", None), ("Vide", "")])

    _upgrade(db, _APRES)

    assert _entrees(db) == []


def test_la_colonne_nest_pas_touchee(tmp_path):
    """La table COMPLÈTE `tracks.spotify_id`, elle ne la remplace pas : l'ID
    principal reste où la GUI, les exports et le scrape le lisent."""
    db = _base_peuplee(tmp_path, [("Magot", "3VXzVGAWFSrH47dBtTOPws")])

    _upgrade(db, _APRES)

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT spotify_id FROM tracks WHERE id = 1").fetchone() == (
            "3VXzVGAWFSrH47dBtTOPws",
        )


def test_genius_id_est_indexe(tmp_path):
    """`genius_id` identifie l'ENREGISTREMENT là où `tracks.id` identifie la
    ligne d'un artiste : c'est par lui que se retrouvent les lignes sœurs, et ce
    parcours sera fait à chaque écriture de donnée d'enregistrement (lot C)."""
    db = _base_peuplee(tmp_path, [("Magot", None)])

    _upgrade(db, _APRES)

    with sqlite3.connect(db) as conn:
        index = {r[1] for r in conn.execute("PRAGMA index_list(tracks)")}
    assert "ix_tracks_genius_id" in index
