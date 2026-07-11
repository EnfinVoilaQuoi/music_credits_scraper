"""Tests de COMPORTEMENT de DataManager (API publique, base temporaire).

Deux rôles :
1. Non-régression du P0 AUDIT §3.1 : une installation NEUVE (base vierge)
   doit pouvoir sauvegarder puis relire un morceau avec les colonnes
   historiquement manquantes (is_featuring, lyrics, primary_artist_name…).
2. Filet de sécurité pour la migration SQLAlchemy 2.0 + Alembic : ces tests
   ne connaissent que save_*/get_* — ils doivent passer À L'IDENTIQUE après
   la réécriture de la couche DB.
"""

import sqlite3

from src.models import Artist, Track


def _artiste_sauve(data_manager, name="Artiste Test") -> Artist:
    artist = Artist(name=name)
    artist.id = data_manager.save_artist(artist)
    return artist


class TestBaseVierge:
    """P0 AUDIT §3.1 — crash historique : « no such column: is_featuring »."""

    def test_save_track_sur_base_neuve(self, data_manager):
        artist = _artiste_sauve(data_manager)
        track = Track(
            title="Morceau Test",
            artist=artist,
            is_featuring=True,
            primary_artist_name="Artiste Principal",
            featured_artists="Artiste Test",
            lyrics="Première ligne\nDeuxième ligne",
            has_lyrics=True,
        )
        assert data_manager.save_track(track)

    def test_relecture_des_champs_historiquement_manquants(self, data_manager):
        artist = _artiste_sauve(data_manager)
        track = Track(
            title="Morceau Test",
            artist=artist,
            is_featuring=True,
            primary_artist_name="Artiste Principal",
            featured_artists="Artiste Test",
            lyrics="Première ligne\nDeuxième ligne",
            has_lyrics=True,
            spotify_id="abc123",
            bpm=142,
        )
        data_manager.save_track(track)

        tracks = data_manager.get_artist_tracks(artist.id)
        assert len(tracks) == 1
        lu = tracks[0]
        assert lu.title == "Morceau Test"
        assert bool(lu.is_featuring) is True
        assert lu.primary_artist_name == "Artiste Principal"
        assert lu.featured_artists == "Artiste Test"
        assert lu.lyrics == "Première ligne\nDeuxième ligne"
        assert lu.spotify_id == "abc123"
        assert lu.bpm == 142


class TestRoundtrip:
    def test_artiste_sauve_et_retrouvable(self, data_manager):
        artist = _artiste_sauve(data_manager, name="Isha")
        assert isinstance(artist.id, int)

    def test_track_minimal(self, data_manager):
        artist = _artiste_sauve(data_manager)
        data_manager.save_track(Track(title="Minimal", artist=artist))
        (lu,) = data_manager.get_artist_tracks(artist.id)
        assert lu.title == "Minimal"
        assert lu.artist.id == artist.id


class TestConversionDuree:
    """Le mapper ligne → Track doit convertir une durée texte « 3:48 » en 228 s
    (données héritées : certaines lignes historiques stockent la durée en MM:SS)."""

    def _set_duration_sql(self, data_manager, value):
        with sqlite3.connect(data_manager.db_path) as conn:
            conn.execute("UPDATE tracks SET duration = ?", (value,))

    def test_duree_texte_mm_ss(self, data_manager):
        artist = _artiste_sauve(data_manager)
        data_manager.save_track(Track(title="Minimal", artist=artist))
        self._set_duration_sql(data_manager, "3:48")

        (lu,) = data_manager.get_artist_tracks(artist.id)
        assert lu.duration == 228

    def test_duree_entiere_inchangee(self, data_manager):
        artist = _artiste_sauve(data_manager)
        data_manager.save_track(Track(title="Minimal", artist=artist, duration=228))
        (lu,) = data_manager.get_artist_tracks(artist.id)
        assert lu.duration == 228
