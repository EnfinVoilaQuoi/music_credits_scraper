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

from src.enrichment.observation import Observation
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
        )
        track.lyrics.text = "Première ligne\nDeuxième ligne"
        track.lyrics.present = True
        assert data_manager.save_track(track)

    def test_relecture_des_champs_historiquement_manquants(self, data_manager):
        artist = _artiste_sauve(data_manager)
        track = Track(
            title="Morceau Test",
            artist=artist,
            is_featuring=True,
            primary_artist_name="Artiste Principal",
            featured_artists="Artiste Test",
            spotify_id="abc123",
        )
        track.lyrics.text = "Première ligne\nDeuxième ligne"
        track.lyrics.present = True
        track.audio.bpm = 142  # Phase 5 : audio/lyrics hors constructeur (sous-objets)
        # E7-D1 : le BPM ne fait plus l'aller-retour par la colonne mais par les
        # observations → on l'émet explicitement (comme le flux d'enrichissement).
        track.observations = [Observation("bpm", 142, "songbpm")]
        data_manager.save_track(track)

        tracks = data_manager.get_artist_tracks(artist.id)
        assert len(tracks) == 1
        lu = tracks[0]
        assert lu.title == "Morceau Test"
        assert bool(lu.is_featuring) is True
        assert lu.primary_artist_name == "Artiste Principal"
        assert lu.featured_artists == "Artiste Test"
        assert lu.lyrics.text == "Première ligne\nDeuxième ligne"
        assert lu.spotify_id == "abc123"
        assert lu.audio.bpm == 142  # reconstruit depuis l'observation


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


class TestUpdateNonDestructif:
    """save_track en paramètres nommés : la sémantique COALESCE / CASE / écrasement
    doit rester identique après la refonte 1.4 (positionnel → nommé)."""

    def test_coalesce_preserve_les_champs_enrichis(self, data_manager):
        # Un re-save « vide » (re-fetch discographie API, champs None) ne doit
        # PAS écraser les données enrichies. E7-D1 : le BPM est préservé via son
        # observation (le re-save vide ne porte pas d'observation → aucun upsert) ;
        # lyrics reste préservé par le COALESCE de la colonne.
        artist = _artiste_sauve(data_manager)
        t = Track(title="X", artist=artist)
        t.lyrics.text = "paroles"
        t.audio.bpm = 142
        t.observations = [Observation("bpm", 142, "songbpm")]
        data_manager.save_track(t)
        data_manager.save_track(Track(title="X", artist=artist))
        (lu,) = data_manager.get_artist_tracks(artist.id)
        assert lu.audio.bpm == 142
        assert lu.lyrics.text == "paroles"

    def test_is_featuring_ecrase_sans_coalesce(self, data_manager):
        # Décision documentée : is_featuring est le seul champ écrasé sans COALESCE.
        artist = _artiste_sauve(data_manager)
        data_manager.save_track(Track(title="X", artist=artist, is_featuring=True))
        data_manager.save_track(Track(title="X", artist=artist, is_featuring=False))
        (lu,) = data_manager.get_artist_tracks(artist.id)
        assert bool(lu.is_featuring) is False

    def test_certifications_vides_ne_wipent_pas(self, data_manager):
        # CASE WHEN :certifications_json = '[]' → conserve l'existant.
        artist = _artiste_sauve(data_manager)
        t = Track(title="X", artist=artist)
        t.certifications = [{"certification": "Or", "certification_date": "2020-01-01"}]
        data_manager.save_track(t)
        data_manager.save_track(Track(title="X", artist=artist))
        (lu,) = data_manager.get_artist_tracks(artist.id)
        assert lu.certifications
        assert lu.certifications[0]["certification"] == "Or"


class TestClearAudio:
    """E7-D1/D2 : un save avec `clear_audio_observations` efface DÉFINITIVEMENT
    l'audio (observations supprimées ; colonnes déjà droppées en D2) → aucune
    résurrection à la relecture."""

    def test_clear_supprime_obs_et_colonne(self, data_manager):
        artist = _artiste_sauve(data_manager)
        t = Track(title="X", artist=artist)
        t.audio.bpm = 142
        t.observations = [
            Observation("bpm", 142, "songbpm"),
            Observation("key", 5, "songbpm"),
            Observation("mode", 1, "songbpm"),
        ]
        data_manager.save_track(t)
        (lu,) = data_manager.get_artist_tracks(artist.id)
        assert lu.audio.bpm == 142  # présent via l'observation

        wipe = Track(title="X", artist=artist)
        wipe.clear_audio_observations = True
        data_manager.save_track(wipe)

        (apres,) = data_manager.get_artist_tracks(artist.id)
        assert apres.audio.bpm is None
        assert apres.audio.key is None
        assert apres.audio.mode is None
        # Les observations audio ont bien été supprimées en base.
        obs = {o.field for o in data_manager.get_observations(t.id)}
        assert obs.isdisjoint({"bpm", "key", "mode", "bpm_alt", "time_signature"})


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
