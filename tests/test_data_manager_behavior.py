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

    def test_save_track_n_ecrit_plus_les_certifications(self, data_manager):
        """Ces colonnes ont un écrivain DÉDIÉ depuis le 2026-09-06.

        `save_track` les protégeait par `CASE WHEN … = '[]'` parce que `[]` disait
        aussi bien « recalculé, aucune » que « cet objet ne porte pas l'info » — et
        cette protection rendait tout RETRAIT impossible (21 certifications fautives
        mesurées en base). Le producteur est désormais seul à écrire.
        """
        artist = _artiste_sauve(data_manager)
        t = Track(title="X", artist=artist)
        t.certs.entries = [{"certification": "Or", "certification_date": "2020-01-01"}]
        data_manager.save_track(t)
        (lu,) = data_manager.get_artist_tracks(artist.id)
        assert lu.certs.entries == []

    def test_un_save_etranger_n_efface_pas_les_certifications(self, data_manager):
        """Ce que la clause protégeait est toujours vrai, et plus solidement :
        les dix flux qui ignorent les certifs ne peuvent plus y toucher du tout."""
        artist = _artiste_sauve(data_manager)
        t = Track(title="X", artist=artist)
        t.id = data_manager.save_track(t)
        data_manager.record_certifications(
            t.id, [{"certification": "Or", "certification_date": "2020-01-01"}], []
        )

        data_manager.save_track(Track(title="X", artist=artist))  # flux sans certifs

        (lu,) = data_manager.get_artist_tracks(artist.id)
        assert lu.certs.entries[0]["certification"] == "Or"

    def test_le_vidage_est_desormais_possible(self, data_manager):
        """LE cas que l'ancien code rendait impossible, et qui a imposé un script
        écrivant en UPDATE direct : une certification devenue caduque doit pouvoir
        DISPARAÎTRE."""
        artist = _artiste_sauve(data_manager)
        t = Track(title="X", artist=artist)
        t.id = data_manager.save_track(t)
        data_manager.record_certifications(
            t.id, [{"certification": "Or"}], [{"certification": "Or"}]
        )

        data_manager.record_certifications(t.id, [], [])

        (lu,) = data_manager.get_artist_tracks(artist.id)
        assert lu.certs.entries == []
        assert lu.certs.album_entries == []

    def test_relationships_meme_traitement(self, data_manager):
        artist = _artiste_sauve(data_manager)
        t = Track(title="X", artist=artist)
        t.id = data_manager.save_track(t)
        data_manager.record_relationships(t.id, [{"type": "sample", "title": "Y"}])
        (lu,) = data_manager.get_artist_tracks(artist.id)
        assert lu.relationships[0]["title"] == "Y"

        data_manager.record_relationships(t.id, [])
        (lu,) = data_manager.get_artist_tracks(artist.id)
        assert lu.relationships == []


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


class TestEcrivainsDedies:
    """Garde-fou STRUCTUREL : `save_track` ne doit plus jamais mentionner les
    trois colonnes JSON à écrivain dédié.

    Un futur refactor qui les y remettrait rétablirait l'ambiguïté de `[]` sans
    rien casser visiblement — et donc l'impossibilité de retirer une
    certification. Même esprit que `tests/test_no_naked_substring_match.py` :
    interdire le motif plutôt que constater ses effets."""

    COLONNES = ("certifications", "album_certifications", "relationships")

    def _source_save_track(self) -> str:
        import inspect

        from src.utils.track_repository import TrackRepository

        return inspect.getsource(TrackRepository.save_track)

    def test_save_track_ne_mentionne_plus_ces_colonnes_en_ecriture(self):
        source = self._source_save_track()
        # Le commentaire d'explication les nomme : on ne regarde que le SQL.
        sql = "\n".join(ligne for ligne in source.splitlines() if not ligne.strip().startswith("#"))
        for colonne in self.COLONNES:
            assert f"{colonne} =" not in sql, (
                f"`{colonne}` est réécrite par save_track — elle a un écrivain dédié "
                f"(`record_certifications` / `record_relationships`). Remettre la "
                f"clause `CASE WHEN … = '[]'` rendrait tout RETRAIT impossible."
            )
            assert f":{colonne}_json" not in sql

    def test_save_track_nectrit_pas_la_table_des_videos(self):
        """e20 : `track_videos` rejoint la règle. Une façade appelée par treize
        flux ne peut pas écrire une donnée que douze d'entre eux ignorent."""
        assert "track_videos" not in self._source_save_track()

    def test_les_ecrivains_dedies_existent(self):
        from src.utils.track_repository import TrackRepository

        assert callable(TrackRepository.record_certifications)
        assert callable(TrackRepository.record_relationships)
        assert callable(TrackRepository.record_track_videos)
