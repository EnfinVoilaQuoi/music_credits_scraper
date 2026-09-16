"""Statut des liens d'artiste (e26) et nature des disques (`albums.record_type`).

Ce que ces tests gardent : un lien PROPOSÉ ou REFUSÉ ne se LIT jamais comme un
confirmé (sinon un alias douteux ferait rattacher une certification), proposer
ne touche jamais l'existant (le refus est une mémoire), et une nature saisie à
la main n'est jamais écrasée par un run Deezer.
"""

import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import create_engine

from alembic import command
from src.models import Artist, ArtistRelation
from src.persistence.bootstrap import make_alembic_config


def _artiste(dm, nom):
    a = Artist(name=nom)
    a.id = dm.save_artist(a)
    return a


def _rel(nom, kind="alias", **kw):
    return ArtistRelation(related_name=nom, kind=kind, **kw)


# ── Migration ────────────────────────────────────────────────────────────────


def test_e26_existing_links_read_as_confirmed(tmp_path):
    db = tmp_path / "m.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        command.upgrade(make_alembic_config(conn), "e25_lignes_soeurs")
    with closing(sqlite3.connect(db)) as c, c:
        c.execute("INSERT INTO artists (name) VALUES ('Isha')")
        c.execute(
            "INSERT INTO artist_relations (artist_id, related_name, kind) VALUES (1, 'Psmaker', 'alias')"
        )
        c.execute("INSERT INTO albums (title, artist_id) VALUES ('Labrador bleu', 1)")
    with engine.begin() as conn:
        command.upgrade(make_alembic_config(conn), "e26_statut_album")
    with closing(sqlite3.connect(db)) as c:
        assert c.execute("SELECT status, detail FROM artist_relations").fetchone() == (
            "confirmed",
            None,
        )
        assert c.execute("SELECT record_type, record_type_source FROM albums").fetchone() == (
            None,
            None,
        )
    engine.dispose()


# ── Liens : proposer, arbitrer, lire ─────────────────────────────────────────


class TestProposer:
    def test_propose_inserts_only_when_absent_by_normalised_name(self, data_manager):
        a = _artiste(data_manager, "Swing")
        n = data_manager.propose_artist_relations(
            a.id,
            [
                _rel("L'Or du Commun", kind="member_of", source="musicbrainz"),
                _rel("L'Or Du Commun", kind="member_of", source="discogs"),
                _rel("Swing Jr", detail="Artist name"),
            ],
        )
        assert n == 2
        tous = data_manager.get_artist_relations(a.id, status=None)
        assert {(r.related_name, r.status) for r in tous} == {
            ("L'Or du Commun", "proposed"),
            ("Swing Jr", "proposed"),
        }
        assert next(r for r in tous if r.related_name == "Swing Jr").detail == "Artist name"
        # Second passage : rien de neuf.
        assert data_manager.propose_artist_relations(a.id, [_rel("Swing Jr")]) == 0

    def test_propose_never_touches_confirmed_or_refused(self, data_manager):
        a = _artiste(data_manager, "Isha")
        data_manager.record_artist_relations(a.id, [_rel("Psmaker", source="manual")])
        data_manager.propose_artist_relations(a.id, [_rel("Isha Bis")])
        assert data_manager.set_relation_status(a.id, "Isha Bis", "alias", "refused")
        # Le run repasse avec les deux : aucune ligne ne bouge.
        assert data_manager.propose_artist_relations(a.id, [_rel("Psmaker"), _rel("Isha Bis")]) == 0
        statuts = {r.related_name: r.status for r in data_manager.get_artist_relations(a.id, None)}
        assert statuts == {"Psmaker": "confirmed", "Isha Bis": "refused"}

    def test_info_status_and_vocabulary(self, data_manager):
        a = _artiste(data_manager, "Mac Miller")
        n = data_manager.propose_artist_relations(
            a.id, [_rel("Malcolm McCormick", detail="Legal name")], status="info"
        )
        assert n == 1
        assert data_manager.get_artist_relations(a.id, status="info")[0].detail == "Legal name"
        with pytest.raises(ValueError):
            data_manager.propose_artist_relations(a.id, [_rel("x")], status="confirmed")
        with pytest.raises(ValueError):
            data_manager.set_relation_status(a.id, "x", "alias", "peut-être")


class TestArbitrer:
    def test_confirming_a_proposal_sets_status_and_date(self, data_manager):
        a = _artiste(data_manager, "Isha")
        data_manager.propose_artist_relations(a.id, [_rel("Psmaker", source="musicbrainz")])
        assert data_manager.get_artist_relations(a.id) == []
        # Confirmer par l'écrivain historique : la ligne PASSE en confirmé.
        data_manager.record_artist_relations(a.id, [_rel("Psmaker", source="musicbrainz")])
        [r] = data_manager.get_artist_relations(a.id)
        assert r.status == "confirmed" and r.related_name == "Psmaker"
        # Puis refusée : disparaît des lecteurs, reste en mémoire.
        assert data_manager.set_relation_status(a.id, "Psmaker", "alias", "refused")
        assert data_manager.get_artist_relations(a.id) == []
        assert data_manager.get_artist_relations(a.id, "refused")[0].related_name == "Psmaker"
        assert not data_manager.set_relation_status(a.id, "Inconnu", "alias", "refused")

    def test_readers_ignore_non_confirmed(self, data_manager):
        a = _artiste(data_manager, "Isha")
        data_manager.propose_artist_relations(a.id, [_rel("Psmaker")])
        data_manager.propose_artist_relations(
            a.id, [_rel("Malcolm", detail="Legal name")], status="info"
        )
        data_manager.record_artist_relations(a.id, [_rel("ISHA Officiel")])
        assert data_manager.noms_de_lartiste(a.id, "Isha") == {"Isha", "ISHA Officiel"}

    def test_nature_connue_ignores_non_confirmed(self, data_manager):
        a = _artiste(data_manager, "Swing")
        data_manager.propose_artist_relations(
            a.id, [_rel("L'Animalerie", kind="member_of", formation="collectif")]
        )
        assert data_manager.nature_connue_pour("L'Animalerie") is None
        data_manager.set_relation_status(a.id, "L'Animalerie", "member_of", "confirmed")
        assert data_manager.nature_connue_pour("L'Animalerie") == "collectif"


# ── Albums : nature du disque ────────────────────────────────────────────────


class TestRecordType:
    def test_creates_row_without_streams(self, data_manager):
        a = _artiste(data_manager, "Isha")
        assert data_manager.set_album_record_type(a.id, "Drôle d'oiseau", "ep", deezer_album_id=42)
        [alb] = data_manager.get_albums_for_artist(a.id)
        assert alb["record_type"] == "ep" and alb["record_type_source"] == "deezer"
        assert alb["deezer_album_id"] == 42 and alb["record_type_updated"]
        assert alb["spotify_streams"] is None

    def test_manual_beats_deezer(self, data_manager):
        a = _artiste(data_manager, "Isha")
        data_manager.upsert_album(a.id, "Labrador bleu", 1000, 10)
        assert data_manager.set_album_record_type(a.id, "Labrador bleu", "album", source="manual")
        assert not data_manager.set_album_record_type(
            a.id, "Labrador bleu", "ep", deezer_album_id=7
        )
        [alb] = data_manager.get_albums_for_artist(a.id)
        assert (alb["record_type"], alb["record_type_source"]) == ("album", "manual")
        assert alb["deezer_album_id"] == 7  # la fiche est retenue quand même
        assert alb["spotify_streams"] == 1000  # les streams n'ont pas bougé
        # Effacer la saisie manuelle → Deezer reprend la main.
        assert data_manager.set_album_record_type(a.id, "Labrador bleu", None, source="manual")
        assert data_manager.set_album_record_type(a.id, "Labrador bleu", "ep")
        assert data_manager.get_albums_for_artist(a.id)[0]["record_type"] == "ep"

    def test_vocabulary_guard(self, data_manager):
        a = _artiste(data_manager, "Isha")
        with pytest.raises(ValueError):
            data_manager.set_album_record_type(a.id, "X", "mixtape")
        with pytest.raises(ValueError):
            data_manager.set_album_record_type(a.id, "X", "ep", source="genius")
        assert not data_manager.set_album_record_type(a.id, "  ", "ep")
