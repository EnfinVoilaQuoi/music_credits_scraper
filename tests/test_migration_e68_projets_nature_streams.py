"""e68 — nature et streams d'album passent d'`albums` (clé : le titre) au
projet : par l'ID Deezer, les ID d'album Spotify ou le titre d'une édition ;
une ligne qui ne désigne aucun projet n'est pas reportée ; retour en arrière."""

import sqlite3
from contextlib import closing

from tests.test_migration_e67_projets import _base_avant, _migrer


def _avec_albums(tmp_path):
    db = _base_avant(tmp_path)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(
            "UPDATE releases SET record_type = NULL, deezer_album_id = 302453847 WHERE id = 10"
        )
        conn.execute("UPDATE releases SET record_type = NULL WHERE id = 11")
        conn.execute(
            "INSERT INTO albums (title, artist_id, record_type, record_type_source, deezer_album_id) "
            "VALUES ('M.A.N (Black Roses & Lost Feelings)', 1, 'album', 'deezer', 302453847)"
        )
        conn.execute(
            "INSERT INTO albums (title, artist_id, spotify_streams, spotify_streams_source, "
            "spotify_streams_updated, ytm_streams) VALUES "
            "('M.A.N (Black Roses & Lost Feelings) (Bonus)', 1, 400, 'kworb', '2026-11-01', 61)"
        )
        conn.execute(
            "INSERT INTO albums (title, artist_id, spotify_streams, spotify_streams_source, "
            "spotify_streams_updated) VALUES ('Disque inconnu', 1, 5, 'kworb', '2026-11-01')"
        )
    return db


def test_le_report_va_au_projet(tmp_path):
    db = _avec_albums(tmp_path)
    _migrer(db, "e68_projets_nature_streams")
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT record_type FROM releases WHERE id = 10").fetchone() == (
            "album",
        )
        assert conn.execute(
            "SELECT nature, nature_source, spotify_streams, spotify_streams_source, ytm_streams "
            "FROM projets WHERE id = (SELECT projet_id FROM releases WHERE id = 10)"
        ).fetchall() == [("album", "deezer", 400, "kworb", 61)]
        # `albums` reste intacte (ses lecteurs passent au lot suivant).
        assert conn.execute("SELECT COUNT(*) FROM albums").fetchone() == (3,)


def test_le_retour_en_arriere_retire_les_colonnes(tmp_path):
    db = _avec_albums(tmp_path)
    _migrer(db, "e68_projets_nature_streams")
    _migrer(db, "e67_projets", descendre=True)
    with closing(sqlite3.connect(db)) as conn:
        assert "nature" not in [r[1] for r in conn.execute("PRAGMA table_info(projets)")]
        assert "spotify_streams" not in [r[1] for r in conn.execute("PRAGMA table_info(releases)")]
