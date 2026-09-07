"""e20 — le backfill verse dans `track_videos` la vidéo déjà connue de chaque morceau.

Un backfill silencieusement inopérant est le pire des deux mondes : la table
existe, elle est vide, et le code qui la lit conclut « ce morceau n'a pas de
vidéo » pour 1 505 morceaux qui en avaient une. On le vérifie donc sur une base
arrêtée à la révision PRÉCÉDENTE, comme le sera une base réelle au moment de la
migration.
"""

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine

from alembic import command
from src.persistence.bootstrap import make_alembic_config

_AVANT = "e19_deezer_identifiants"


def _upgrade(db_path: Path, revision: str) -> None:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as conn:
            command.upgrade(make_alembic_config(conn), revision)
            conn.commit()
    finally:
        engine.dispose()


def _base_a_la_revision_precedente(tmp_path: Path) -> Path:
    db = tmp_path / "avant_e20.db"
    _upgrade(db, _AVANT)
    return db


def _peupler(db: Path, lignes) -> None:
    """`lignes` : (titre, url, source, kind, views, views_updated)."""
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Artiste')")
        for i, (titre, url, source, kind, views, vu) in enumerate(lignes, start=1):
            conn.execute(
                "INSERT INTO tracks (id, title, artist_id, youtube_url, youtube_url_source, "
                "youtube_video_kind, youtube_video_views, youtube_video_views_updated) "
                "VALUES (?, ?, 1, ?, ?, ?, ?, ?)",
                (i, titre, url, source, kind, views, vu),
            )


def _videos(db: Path) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT track_id, video_id, url, kind, source, views, views_updated "
            "FROM track_videos ORDER BY track_id"
        ).fetchall()


def test_backfill_reprend_le_lien_et_ses_vues(tmp_path):
    db = _base_a_la_revision_precedente(tmp_path)
    _peupler(
        db,
        [
            (
                "Magot",
                "https://www.youtube.com/watch?v=as9MkTY7d-Q",
                "genius_media",
                "clip",
                23482630,
                "2026-09-01 10:00:00",
            )
        ],
    )

    _upgrade(db, "head")

    assert _videos(db) == [
        (
            1,
            "as9MkTY7d-Q",
            "https://www.youtube.com/watch?v=as9MkTY7d-Q",
            "clip",
            "genius_media",
            23482630,
            "2026-09-01 10:00:00",
        )
    ]


def test_la_provenance_est_recopiee_verbatim(tmp_path):
    """Pas de défaut inventé : un lien `manual` reste `manual`. La priorité des
    provenances se joue à l'écriture, pas à la migration."""
    db = _base_a_la_revision_precedente(tmp_path)
    _peupler(
        db,
        [
            ("A", "https://youtu.be/aaaaaaaaaaa", "manual", None, None, None),
            ("B", "https://www.youtube.com/watch?v=bbbbbbbbbbb", "search_auto", None, None, None),
        ],
    )

    _upgrade(db, "head")

    assert [(r[1], r[4]) for r in _videos(db)] == [
        ("aaaaaaaaaaa", "manual"),
        ("bbbbbbbbbbb", "search_auto"),
    ]


def test_un_morceau_sans_lien_ne_produit_aucune_ligne(tmp_path):
    db = _base_a_la_revision_precedente(tmp_path)
    _peupler(
        db, [("Sans lien", None, None, None, None, None), ("Vide", "", None, None, None, None)]
    )

    _upgrade(db, "head")

    assert _videos(db) == []


def test_un_lien_dont_lidentifiant_ne_sextrait_pas_est_saute(tmp_path):
    """La colonne le garde ; la table, elle, est indexée par video id — y mettre
    une ligne sans identifiant lui ôterait sa seule clé."""
    db = _base_a_la_revision_precedente(tmp_path)
    _peupler(db, [("Bancal", "https://www.youtube.com/results?q=x", "manual", None, None, None)])

    _upgrade(db, "head")

    assert _videos(db) == []
