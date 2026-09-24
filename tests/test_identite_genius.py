"""e36 — l'identité d'une fiche Genius est son `genius_id`, plus son titre.

Mesuré le 2026-09-24 : `UNIQUE(title, artist_id)` + un `save_track` qui
retrouvait la fiche par titre fusionnaient des morceaux Genius DIFFÉRENTS au
même titre (132 collisions loggées, 117 fiches). « goosebumps » portait la page
de la cover de Skylar Grey et les streams du morceau de Travis Scott.
"""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from src.models import Artist, Track
from src.persistence.bootstrap import make_alembic_config
from src.utils.track_repository import FicheAmbigue


@pytest.fixture
def artiste(data_manager):
    a = Artist(name="Travis Scott")
    a.id = data_manager.save_artist(a)
    return a


def _t(artist, titre, genius_id=None, **kw):
    t = Track(title=titre, artist=artist, **kw)
    t.genius_id = genius_id
    return t


def _fiches(dm, artist):
    with dm.engine.connect() as conn:
        return conn.execute(
            text("SELECT id, title, genius_id FROM tracks WHERE artist_id = :a ORDER BY id"),
            {"a": artist.id},
        ).fetchall()


class TestRattachement:
    def test_deux_morceaux_genius_au_meme_titre_font_deux_fiches(self, data_manager, artiste):
        """La cover de Skylar Grey et le morceau de Travis ne fusionnent plus."""
        a = data_manager.save_track(_t(artiste, "goosebumps", 2849767))
        b = data_manager.save_track(_t(artiste, "goosebumps", 5616211))
        assert a != b
        assert [g for _, _, g in _fiches(data_manager, artiste)] == [2849767, 5616211]

    def test_meme_genius_id_retrouve_la_fiche_meme_si_le_titre_change(self, data_manager, artiste):
        a = data_manager.save_track(_t(artiste, "goosebumps", 2849767))
        b = data_manager.save_track(_t(artiste, "Goosebumps", 2849767))
        assert a == b

    def test_une_fiche_deezer_est_adoptee_par_genius(self, data_manager, artiste):
        """Créée depuis Deezer (sans genius_id), puis publiée par Genius : la
        fiche est ADOPTÉE, pas doublée."""
        deezer = data_manager.save_track(_t(artiste, "Durag"))
        genius = data_manager.save_track(_t(artiste, "Durag", 111))
        assert deezer == genius
        assert _fiches(data_manager, artiste)[0][2] == 111

    def test_la_fiche_deezer_complete_l_unique_homonyme(self, data_manager, artiste):
        a = data_manager.save_track(_t(artiste, "Durag", 111))
        assert data_manager.save_track(_t(artiste, "Durag")) == a

    def test_sans_genius_id_face_a_deux_homonymes_refus(self, data_manager, artiste):
        """Jamais de choix au hasard entre deux fiches homonymes."""
        data_manager.save_track(_t(artiste, "Heartless", 1))
        data_manager.save_track(_t(artiste, "Heartless", 2))
        with pytest.raises(FicheAmbigue):
            data_manager.save_track(_t(artiste, "Heartless"))

    def test_l_id_de_la_fiche_prime(self, data_manager, artiste):
        """Un objet relu de la base retrouve SA fiche, même parmi des homonymes."""
        data_manager.save_track(_t(artiste, "Heartless", 1))
        b = data_manager.save_track(_t(artiste, "Heartless", 2))
        relu = _t(artiste, "Heartless")
        relu.id = b
        assert data_manager.save_track(relu) == b

    def test_un_renommage_ne_fabrique_pas_d_homonyme(self, data_manager, artiste):
        data_manager.save_track(_t(artiste, "Matrix", 1))
        b = data_manager.save_track(_t(artiste, "Matrix (Intro)", 2))
        assert data_manager.rename_track(b, "Matrix") is False
        assert data_manager.rename_track(b, "Matrix Intro") is True


# ── Migration e36 ────────────────────────────────────────────────────────────

_AVANT = "e35_relations_suffixe_nature"
_APRES = "e36_tracks_identite_genius"


def _upgrade(db_path: Path, revision: str) -> None:
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with engine.connect() as conn:
            command.upgrade(make_alembic_config(conn), revision)
            conn.commit()
    finally:
        engine.dispose()


def test_migration_leve_les_doublons_puis_autorise_les_homonymes(tmp_path):
    db = tmp_path / "avant_e36.db"
    _upgrade(db, _AVANT)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Josman'), (2, 'Django')")
        # Vrai doublon (même page) : l'ID reste sur la plus ancienne.
        conn.execute(
            "INSERT INTO tracks (id, title, artist_id, genius_id, genius_url) VALUES "
            "(1, 'BOSS', 1, 638651, 'u/boss'), (2, 'Boss', 1, 638651, 'u/boss'), "
            # ID d'une AUTRE page : impossible de trancher, retiré des deux.
            "(3, 'Brouillard (extrait)', 2, 14175368, 'u/brouillard'), "
            "(4, 'Locke', 2, 14175368, 'u/locke')"
        )
    _upgrade(db, _APRES)
    with sqlite3.connect(db) as conn:
        ids = dict(conn.execute("SELECT id, genius_id FROM tracks").fetchall())
        assert ids == {1: 638651, 2: None, 3: None, 4: None}
        # Deux genius_id différents au même titre : permis.
        conn.execute(
            "INSERT INTO tracks (title, artist_id, genius_id) VALUES "
            "('Heartless', 1, 10), ('Heartless', 1, 11)"
        )
        # Deux fiches SANS genius_id au même titre : toujours refusé.
        conn.execute("INSERT INTO tracks (title, artist_id) VALUES ('Durag', 1)")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO tracks (title, artist_id) VALUES ('Durag', 1)")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO tracks (title, artist_id, genius_id) VALUES ('X', 1, 10)")
