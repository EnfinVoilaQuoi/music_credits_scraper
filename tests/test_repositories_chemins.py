"""Repositories et façade — chemins d'erreur et lectures secondaires.

`DataManager` : `record_pending` sans id / avec IDs Spotify en attente,
`export_to_json` (objet chargé, nom, artiste inconnu, chemin par défaut),
`get_statistics` sous panne. `TrackRepository` : rôle de crédit inconnu (DIT,
plus avalé), lecture des crédits qui casse, verdict de streams non numérique,
`get_stream_observation_dates`, et les quatre méthodes `track_videos` en
nominal comme sous panne.
"""

import json

from sqlalchemy import text

from src.models import Artist, Track
from src.models.track import CreditRole, TrackSpotifyId, TrackVideo
from src.utils import data_manager as dm_mod
from tests import test_track_repository as _ttr

# Fixtures du module voisin, réexposées sous leur nom (ruff verrait une
# redéfinition avec un import direct, black déplace le `noqa`).
artiste = _ttr.artiste
morceau = _ttr.morceau
moteur_casse = _ttr.moteur_casse


class TestRecordPending:
    def test_sans_id_rien_n_est_ecrit(self, data_manager, caplog):
        t = Track(title="Neuf", artist=Artist(name="A"))
        t.certs.needs_write = True
        with caplog.at_level("WARNING"):
            data_manager.record_pending(t)
        assert t.certs.needs_write and any("sans id" in r.message for r in caplog.records)

    def test_ids_spotify_en_attente_sont_verses(self, data_manager, morceau):
        morceau._spotify_ids_pending = [
            TrackSpotifyId(spotify_id="abc123", source="kworb"),
            TrackSpotifyId(spotify_id="def456", source="scraper"),
        ]
        data_manager.record_pending(morceau)
        assert morceau._spotify_ids_pending == []
        with data_manager.engine.connect() as conn:
            ids = {
                r[0]
                for r in conn.execute(
                    text("SELECT spotify_id FROM track_spotify_ids WHERE track_id = :t"),
                    {"t": morceau.id},
                )
            }
        assert ids == {"abc123", "def456"}


class TestExportJson:
    def test_objet_charge_est_exporte_tel_quel(self, data_manager, artiste, tmp_path):
        """La GUI passe un artiste FILTRÉ des désactivés : c'est LUI qui part,
        pas une relecture par le nom."""
        a = Artist(name=artiste.name, genius_id=1)
        a.tracks = [Track(title="Gardé", artist=a)]
        out = data_manager.export_to_json(a, tmp_path / "x.json")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert [t["title"] for t in data["tracks"]] == ["Gardé"] and data["total_tracks"] == 1

    def test_par_nom_recharge_depuis_la_base(self, data_manager, morceau, tmp_path):
        out = data_manager.export_to_json("ISHA", tmp_path / "isha.json")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["artist"]["name"] == "ISHA" and data["total_tracks"] == 1

    def test_artiste_inconnu_rend_none(self, data_manager, tmp_path):
        assert data_manager.export_to_json("Personne", tmp_path / "x.json") is None

    def test_chemin_par_defaut(self, data_manager, artiste, monkeypatch, tmp_path):
        monkeypatch.setattr(dm_mod, "ARTISTS_DIR", tmp_path)
        out = data_manager.export_to_json("ISHA")
        assert out == tmp_path / "isha_credits.json" and out.exists()


class TestStatistiques:
    def test_panne_rend_des_zeros(self, data_manager, moteur_casse):
        moteur_casse(data_manager)
        stats = data_manager.get_statistics()
        assert stats["total_artists"] == 0 and stats["total_tracks"] == 0


class TestCredits:
    def test_role_inconnu_en_base_est_dit_et_classe_other(self, data_manager, morceau, caplog):
        with data_manager.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO credits (track_id, name, role, source) "
                    "VALUES (:t, 'X', 'Chef de rang', 'genius')"
                ),
                {"t": morceau.id},
            )
        with caplog.at_level("WARNING"):
            (t,) = data_manager.get_artist_tracks(morceau.artist.id)
        assert [c.role for c in t.credits] == [CreditRole.OTHER]
        assert any("Rôle de crédit inconnu" in r.message for r in caplog.records)

    def test_lecture_qui_casse_est_loggee_en_warning(self, data_manager, morceau, caplog):
        """Une connexion fermée : la lecture rend [] et le DIT (warning, plus debug)."""
        conn = data_manager.engine.connect()
        conn.close()
        with caplog.at_level("WARNING"):
            assert data_manager._get_track_credits(conn, morceau.id) == []
        assert any("Erreur _get_track_credits" in r.message for r in caplog.records)


class TestStreams:
    def test_verdict_non_numerique_laisse_la_colonne(self, data_manager, morceau, caplog):
        with data_manager.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO observations (track_id, field, value, source, seen_at) "
                    "VALUES (:t, 'spotify_streams', 'beaucoup', 'kworb', '2026-01-01')"
                ),
                {"t": morceau.id},
            )
            with caplog.at_level("WARNING"):
                assert data_manager._arbitrer_streams(conn, morceau.id) == {}
        assert any("Streams non numériques" in r.message for r in caplog.records)

    def test_dates_d_observation_par_source(self, data_manager, morceau, moteur_casse):
        data_manager.record_spotify_streams(morceau.id, 1000, "kworb", updated_at="2026-01-02")
        assert data_manager.get_stream_observation_dates("kworb") == {morceau.id: "2026-01-02"}
        assert data_manager.get_stream_observation_dates("spotify_web") == {}
        moteur_casse(data_manager)
        assert data_manager.get_stream_observation_dates("kworb") == {}


class TestVideos:
    def test_enregistrer_lire_oublier(self, data_manager, morceau, artiste):
        v1 = TrackVideo(video_id="abcdefghijk", url="https://youtu.be/abcdefghijk", views=10)
        v2 = TrackVideo(video_id="lmnopqrstuv", kind="audio", views=500)
        assert data_manager.record_track_videos(morceau.id, [v1, v2]) == 2
        vus = data_manager.get_track_videos(morceau.id)
        assert [v.video_id for v in vus] == ["lmnopqrstuv", "abcdefghijk"]  # plus vues d'abord
        par_artiste = data_manager.get_artist_track_videos(artiste.id)
        assert set(par_artiste) == {morceau.id} and len(par_artiste[morceau.id]) == 2
        assert data_manager.forget_track_video(morceau.id, "abcdefghijk") is True
        assert data_manager.forget_track_video(morceau.id, "abcdefghijk") is False
        assert [v.video_id for v in data_manager.get_track_videos(morceau.id)] == ["lmnopqrstuv"]

    def test_sous_panne(self, data_manager, morceau, artiste, moteur_casse):
        moteur_casse(data_manager)
        v = TrackVideo(video_id="abcdefghijk")
        assert data_manager.record_track_videos(morceau.id, [v]) == 0
        assert data_manager.forget_track_video(morceau.id, "abcdefghijk") is False
        assert data_manager.get_track_videos(morceau.id) == []
        assert data_manager.get_artist_track_videos(artiste.id) == {}
