"""Bootstrap Alembic : bases fraîches au head, bases pré-Alembic stampées à e1.

Une base VIERGE est créée par `alembic upgrade head` (E3b) → révision head. Une
base pré-Alembic (legacy, sans `alembic_version`) est rattrapée puis stampée à
`LEGACY_HEAD_REVISION` (= e1_initial_schema, PAS le head) pour que
`upgrade_to_head` applique ensuite les révisions suivantes (e4 observations…).
Le bootstrap est PERMANENT : un backup pré-Alembic restauré est re-traité au
prochain démarrage.
"""

import sqlite3
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from src.persistence.bootstrap import (
    LEGACY_HEAD_REVISION,
    _head_revision,
    ensure_stamped,
    make_alembic_config,
)
from src.utils.db import Database

HEAD = _head_revision()  # révision head courante (e4_observations depuis E4)


def _versions(db_path: str):
    """Contenu de alembic_version, ou None si la table n'existe pas."""
    with sqlite3.connect(db_path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        if not exists:
            return None
        return [r[0] for r in conn.execute("SELECT version_num FROM alembic_version")]


def _current_revision(db_path: str) -> str | None:
    engine = create_engine(f"sqlite:///{Path(db_path).as_posix()}")
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def _upgrade_to(db_path: str, revision: str) -> None:
    """`alembic upgrade <revision>` sur `db_path` (via une connexion)."""
    from alembic import command

    engine = create_engine(f"sqlite:///{Path(db_path).as_posix()}")
    try:
        with engine.connect() as conn:
            command.upgrade(make_alembic_config(conn), revision)
            conn.commit()
    finally:
        engine.dispose()


def test_fresh_db_est_au_head(tmp_path):
    db = Database(str(tmp_path / "fresh.db"))
    assert _versions(db.db_path) == [HEAD]


def test_bootstrap_idempotent(tmp_path):
    path = str(tmp_path / "twice.db")
    Database(path)
    Database(path)  # 2e init : ne doit pas dupliquer ni échouer
    assert _versions(path) == [HEAD]


def test_backup_pre_alembic_est_stampe_a_e1_pas_au_head(tmp_path):
    """Un backup pré-Alembic (schéma e1, sans alembic_version) doit être stampé à
    e1 (LEGACY_HEAD_REVISION), PAS au head : sinon `upgrade_to_head` sauterait e4
    et la table observations ne serait jamais créée sur une base restaurée."""
    path = str(tmp_path / "restored.db")
    _upgrade_to(path, LEGACY_HEAD_REVISION)  # base au schéma e1 (pas d'observations)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE alembic_version")
        conn.execute("PRAGMA user_version = 46")
        conn.commit()
        has_obs = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'"
        ).fetchone()
    assert has_obs is None  # base e1 : la table observations n'existe pas encore
    assert _versions(path) is None

    ensure_stamped(path)  # re-démarrage
    assert _versions(path) == [LEGACY_HEAD_REVISION]  # stampée e1, pas le head


def test_backup_ancien_sous_46_est_rattrape_puis_stampe(tmp_path):
    """Un vieux backup pré-Alembic à user_version < 46 (colonnes manquantes) doit
    être RATTRAPÉ (colonnes ajoutées jusqu'à 46) PUIS stampé — pas juste stampé,
    sinon le stamp mentirait sur le schéma (E3b, base restaurée)."""
    path = str(tmp_path / "vieux.db")
    with sqlite3.connect(path) as conn:
        # Schéma « d'origine » (avant migrations user_version) : les colonnes de
        # base uniquement, user_version = 0, aucune alembic_version.
        conn.executescript("""
            CREATE TABLE artists (id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE, genius_id INTEGER, spotify_id TEXT,
                discogs_id INTEGER, spotify_monthly_listeners INTEGER,
                ytm_monthly_listeners INTEGER, created_at TIMESTAMP, updated_at TIMESTAMP);
            CREATE TABLE tracks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                artist_id INTEGER NOT NULL, album TEXT, track_number INTEGER,
                release_date TIMESTAMP, genius_id INTEGER, spotify_id TEXT, discogs_id INTEGER,
                bpm INTEGER, duration INTEGER, genre TEXT, genius_url TEXT, spotify_url TEXT,
                youtube_url TEXT, created_at TIMESTAMP, updated_at TIMESTAMP, last_scraped TIMESTAMP);
            CREATE TABLE albums (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                artist_id INTEGER NOT NULL, spotify_streams INTEGER, spotify_daily_streams INTEGER,
                spotify_streams_updated TIMESTAMP);
            """)
        conn.execute("PRAGMA user_version = 0")
        conn.commit()

    ensure_stamped(path)

    with sqlite3.connect(path) as conn:
        tcols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
        uv = conn.execute("PRAGMA user_version").fetchone()[0]
    # Échantillon de colonnes ajoutées par le rattrapage (parmi les 46 migrations).
    assert {"isrc", "bpm_source", "key", "mode", "spotify_streams", "album_override"} <= tcols
    assert uv == 46
    assert _versions(path) == [LEGACY_HEAD_REVISION]


def test_base_neuve_est_au_head_pour_alembic(tmp_path):
    # Propriété critique : une base neuve est au head, donc `upgrade head` est un
    # no-op au lieu de recréer/perdre des tables.
    db = Database(str(tmp_path / "head.db"))
    assert _current_revision(db.db_path) == HEAD

    # upgrade head : aucune erreur, aucune table recréée/perdue.
    with sqlite3.connect(db.db_path) as conn:
        avant = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    engine = create_engine(f"sqlite:///{Path(db.db_path).as_posix()}")
    try:
        from alembic import command

        with engine.connect() as conn:
            command.upgrade(make_alembic_config(conn), "head")
            conn.commit()
    finally:
        engine.dispose()
    with sqlite3.connect(db.db_path) as conn:
        apres = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert avant == apres


def test_e4_backfill_bpm_key_mode(tmp_path):
    """La révision e4 crée `observations` et backfill le trio audio depuis les
    colonnes `*_source` : 1 obs par `bpm_source` non nul, key/mode = 2 obs (même
    source `key_mode_source`) ; aucune obs si pas de source."""
    path = str(tmp_path / "backfill.db")
    _upgrade_to(path, LEGACY_HEAD_REVISION)  # schéma e1, pas encore d'observations
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'A')")
        # T1 : bpm (avec confiance) + key + mode
        conn.execute(
            "INSERT INTO tracks (id, title, artist_id, bpm, bpm_source, bpm_confidence, "
            '"key", "mode", key_mode_source) '
            "VALUES (1, 'T1', 1, 140, 'reccobeats', 2, '2', '1', 'reccobeats')"
        )
        # T2 : bpm seul, sans confiance ni key/mode
        conn.execute(
            "INSERT INTO tracks (id, title, artist_id, bpm, bpm_source) "
            "VALUES (2, 'T2', 1, 90, 'songbpm')"
        )
        # T3 : aucune source -> aucune observation
        conn.execute("INSERT INTO tracks (id, title, artist_id, bpm) VALUES (3, 'T3', 1, 120)")
        conn.commit()

    # Épinglé à e4 (pas HEAD) : on isole le backfill e4 du backfill legacy e10.
    _upgrade_to(path, "e4_observations")

    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT track_id, field, value, source, confidence "
            "FROM observations ORDER BY track_id, field"
        ).fetchall()
    assert rows == [
        (1, "bpm", "140", "reccobeats", 2.0),
        (1, "key", "2", "reccobeats", None),
        (1, "mode", "1", "reccobeats", None),
        (2, "bpm", "90", "songbpm", None),
    ]


def test_e10_backfill_legacy_observations(tmp_path):
    """La révision e10 (E7-D0) backfill une observation `source='legacy'` là où une
    colonne audio est renseignée SANS observation du champ : bpm/key/mode/
    time_signature, et bpm_alt seulement quand aucune VRAIE source bpm n'existe."""
    path = str(tmp_path / "legacy.db")
    _upgrade_to(path, "e9_media_images")  # schéma pré-e10 (observations existe déjà)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'A')")
        # T1 : bpm avec une VRAIE observation déjà présente -> pas de legacy.
        conn.execute("INSERT INTO tracks (id, title, artist_id, bpm) VALUES (1, 'T1', 1, 140)")
        conn.execute(
            "INSERT INTO observations (track_id, field, value, source, confidence, seen_at) "
            "VALUES (1, 'bpm', '140', 'songbpm', 2.0, '2026-01-01')"
        )
        # T2 : bpm + bpm_confidence, aucune obs -> legacy bpm (confiance castée REAL).
        conn.execute(
            "INSERT INTO tracks (id, title, artist_id, bpm, bpm_confidence) "
            "VALUES (2, 'T2', 1, 120, 3)"
        )
        # T3 : key/mode sans obs -> legacy key + mode.
        conn.execute(
            'INSERT INTO tracks (id, title, artist_id, "key", "mode") '
            "VALUES (3, 'T3', 1, '5', '0')"
        )
        # T4 : bpm_alt sans vraie source bpm -> legacy bpm_alt.
        conn.execute("INSERT INTO tracks (id, title, artist_id, bpm_alt) VALUES (4, 'T4', 1, 70)")
        # T5 : bpm_alt AVEC vraie source bpm -> PAS de legacy bpm_alt (re-dérivé).
        conn.execute(
            "INSERT INTO tracks (id, title, artist_id, bpm, bpm_alt) VALUES (5, 'T5', 1, 160, 80)"
        )
        conn.execute(
            "INSERT INTO observations (track_id, field, value, source, confidence, seen_at) "
            "VALUES (5, 'bpm', '160', 'reccobeats', 1.0, '2026-01-01')"
        )
        # T6 : time_signature sans obs -> legacy time_signature.
        conn.execute(
            "INSERT INTO tracks (id, title, artist_id, time_signature) VALUES (6, 'T6', 1, '4/4')"
        )
        conn.commit()

    _upgrade_to(path, HEAD)  # applique e10

    with sqlite3.connect(path) as conn:
        legacy = conn.execute(
            "SELECT track_id, field, value, confidence FROM observations "
            "WHERE source = 'legacy' ORDER BY track_id, field"
        ).fetchall()
    assert legacy == [
        (2, "bpm", "120", 3.0),
        (3, "key", "5", None),
        (3, "mode", "0", None),
        (4, "bpm_alt", "70", None),
        (6, "time_signature", "4/4", None),
    ]


def test_e11_backfill_musical_key_orphans(tmp_path):
    """La révision e11 rétro-dérive key/mode des orphelins `musical_key` (colonne
    sans paire key/mode en observations), pour les préserver au drop E7-D2."""
    path = str(tmp_path / "orphans.db")
    _upgrade_to(path, "e10_backfill_legacy_obs")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'A')")
        # T1 : orphelin (musical_key, aucune obs key/mode) -> rétro-dérivé 11/0.
        conn.execute(
            "INSERT INTO tracks (id, title, artist_id, musical_key) VALUES (1, 'T1', 1, 'Si mineur')"
        )
        # T2 : musical_key MAIS déjà une paire key/mode (source réelle) -> intouché.
        conn.execute(
            "INSERT INTO tracks (id, title, artist_id, musical_key) VALUES (2, 'T2', 1, 'Sol majeur')"
        )
        conn.execute(
            "INSERT INTO observations (track_id, field, value, source, seen_at) "
            "VALUES (2, 'key', '7', 'songbpm', '2026-01-01'), "
            "(2, 'mode', '1', 'songbpm', '2026-01-01')"
        )
        # T3 : musical_key non interprétable -> ignoré (reste orphelin).
        conn.execute(
            "INSERT INTO tracks (id, title, artist_id, musical_key) VALUES (3, 'T3', 1, 'grosse blague')"
        )
        conn.commit()

    _upgrade_to(path, HEAD)  # applique e11

    with sqlite3.connect(path) as conn:
        legacy = conn.execute(
            "SELECT track_id, field, value FROM observations "
            "WHERE source = 'legacy' ORDER BY track_id, field"
        ).fetchall()
    assert legacy == [(1, "key", "11"), (1, "mode", "0")]
