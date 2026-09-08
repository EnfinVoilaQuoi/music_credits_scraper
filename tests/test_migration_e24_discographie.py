"""e24 — les trois champs de discographie deviennent des observations `legacy`.

Un backfill silencieusement inopérant est le pire des deux mondes : la table
existe, elle est vide, et le moteur conclut « aucune source n'a mesuré ce champ »
pour 1 241 durées qui en avaient une. On le vérifie sur une base arrêtée à la
révision PRÉCÉDENTE, comme le sera une base réelle au moment de la migration.

Le piège propre à cette révision : la colonne `duration` est HÉTÉROGÈNE
(1 288 entiers, 19 chaînes « 2:30 »), SQLite acceptant n'importe quel type dans
une colonne INTEGER. Sans la coercition PARTAGÉE du mapper, ces 19 deviendraient
des observations illisibles.
"""

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine

from alembic import command
from src.persistence.bootstrap import make_alembic_config

_AVANT = "e23_track_spotify_ids"
_APRES = "e24_discographie_obs"


def _upgrade(db_path: Path, revision: str) -> None:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as conn:
            command.upgrade(make_alembic_config(conn), revision)
            conn.commit()
    finally:
        engine.dispose()


def _base(tmp_path: Path, lignes) -> Path:
    """`lignes` : (titre, duration, release_date, isrc)."""
    db = tmp_path / "avant_e24.db"
    _upgrade(db, _AVANT)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Flynt')")
        for i, (titre, duree, date, isrc) in enumerate(lignes, start=1):
            conn.execute(
                "INSERT INTO tracks (id, title, artist_id, duration, release_date, isrc) "
                "VALUES (?, ?, 1, ?, ?, ?)",
                (i, titre, duree, date, isrc),
            )
    return db


def _obs(db: Path) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT track_id, field, value, source FROM observations ORDER BY track_id, field"
        ).fetchall()


def test_les_trois_champs_deviennent_des_observations(tmp_path):
    db = _base(tmp_path, [("Un pour la plume", 249, "2006-01-01", "FRPJQ1501290")])

    _upgrade(db, _APRES)

    assert _obs(db) == [
        (1, "duration", "249", "legacy"),
        (1, "isrc", "FRPJQ1501290", "legacy"),
        (1, "release_date", "2006-01-01", "legacy"),
    ]


def test_aucune_valeur_de_colonne_ne_change(tmp_path):
    """Le backfill TRACE, il ne corrige pas — sauf les durées mal typées, qui
    sont l'objet du test suivant."""
    db = _base(tmp_path, [("Un pour la plume", 249, "2006-01-01", "FRPJQ1501290")])

    _upgrade(db, _APRES)

    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT duration, release_date, isrc FROM tracks WHERE id = 1"
        ).fetchone() == (249, "2006-01-01", "FRPJQ1501290")


def test_une_duree_ecrite_2h30_est_normalisee_des_DEUX_cotes(tmp_path):
    """« 2:30 » dans une colonne INTEGER : SQLite l'accepte, et tout lecteur en
    `text()` brut s'y casse (l'audit du 2026-09-08 l'a fait). La colonne est
    remise en secondes ET l'observation porte l'entier."""
    db = _base(tmp_path, [("Booska Labrador Bleu", "2:30", None, None)])

    _upgrade(db, _APRES)

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT duration FROM tracks WHERE id = 1").fetchone() == (150,)
        assert conn.execute("SELECT typeof(duration) FROM tracks WHERE id = 1").fetchone() == (
            "integer",
        )
    assert _obs(db) == [(1, "duration", "150", "legacy")]


def test_une_duree_illisible_n_est_pas_devinee(tmp_path):
    """On ne fabrique pas une valeur qu'on ne sait pas lire : ni colonne
    modifiée, ni observation créée sous une fausse forme."""
    db = _base(tmp_path, [("Bizarre", "à peu près trois minutes", None, None)])

    _upgrade(db, _APRES)

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT duration FROM tracks WHERE id = 1").fetchone() == (
            "à peu près trois minutes",
        )
    assert _obs(db) == []


def test_les_champs_vides_ne_fabriquent_rien(tmp_path):
    db = _base(tmp_path, [("Sans rien", None, None, None), ("Vides", None, "", "")])

    _upgrade(db, _APRES)

    assert _obs(db) == []
