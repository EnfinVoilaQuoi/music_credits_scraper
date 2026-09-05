"""Accesseurs `TrackRepository` : chemin nominal, décisions, chemin d'erreur.

Même contrat que `test_artist_repository.py` — en cas d'échec DB, ces méthodes
logguent et rendent une valeur de repli plutôt que de laisser l'exception
remonter à la GUI. Les `except` correspondants étaient les lignes non couvertes
de `src/utils/track_repository.py`, et les couvrir est le pré-requis pour les
resserrer en `except SQLAlchemyError`.

S'y ajoute une VRAIE décision, elle aussi non testée : la priorité des sources
de lien YouTube (`update_track_youtube_url`).
"""

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.models import Artist, Track


@pytest.fixture
def artiste(data_manager):
    a = Artist(name="ISHA")
    a.id = data_manager.save_artist(a)
    return a


@pytest.fixture
def morceau(data_manager, artiste):
    t = Track(title="Morceau", artist=artiste, album="Album A")
    t.id = data_manager.save_track(t)
    return t


@pytest.fixture
def moteur_casse(monkeypatch):
    """Pose une panne DB : `connect()` (lectures) et `begin()` (écritures)."""

    class _MoteurCasse:
        def connect(self):
            raise SQLAlchemyError("base indisponible")

        def begin(self):
            raise SQLAlchemyError("base indisponible")

    def _installer(dm):
        # `DataManager.engine` est une propriété en lecture seule déléguant à
        # `Database` : la panne se pose sur l'objet sous-jacent.
        monkeypatch.setattr(dm._db, "engine", _MoteurCasse())

    return _installer


def _colonne(dm, track_id, *colonnes):
    with dm.engine.connect() as conn:
        return conn.execute(
            text(f"SELECT {', '.join(colonnes)} FROM tracks WHERE id = :i"), {"i": track_id}
        ).first()


class TestLienYouTube:
    """Priorité des sources : 'manual' ≥ 'genius_media' > 'search_auto'.

    Un lien trouvé par recherche automatique ne doit JAMAIS écraser un lien
    issu du catalogue Genius ni un choix de l'utilisateur — sinon un run de
    recherche annulerait silencieusement une correction manuelle.
    """

    def test_pose_sur_un_morceau_sans_lien(self, data_manager, morceau):
        assert data_manager.update_track_youtube_url(morceau.id, "u1", "search_auto") is True
        assert _colonne(data_manager, morceau.id, "youtube_url") == ("u1",)

    def test_search_auto_n_ecrase_pas_genius_media(self, data_manager, morceau):
        data_manager.update_track_youtube_url(morceau.id, "genius", "genius_media")
        data_manager.update_track_youtube_url(morceau.id, "auto", "search_auto")

        assert _colonne(data_manager, morceau.id, "youtube_url") == ("genius",)

    def test_search_auto_n_ecrase_pas_manual(self, data_manager, morceau):
        data_manager.update_track_youtube_url(morceau.id, "choisi", "manual")
        data_manager.update_track_youtube_url(morceau.id, "auto", "search_auto")

        assert _colonne(data_manager, morceau.id, "youtube_url") == ("choisi",)

    def test_manual_ecrase_genius_media(self, data_manager, morceau):
        data_manager.update_track_youtube_url(morceau.id, "genius", "genius_media")
        data_manager.update_track_youtube_url(morceau.id, "choisi", "manual")

        assert _colonne(data_manager, morceau.id, "youtube_url") == ("choisi",)

    def test_search_auto_remplace_un_autre_search_auto(self, data_manager, morceau):
        data_manager.update_track_youtube_url(morceau.id, "vieux", "search_auto")
        data_manager.update_track_youtube_url(morceau.id, "neuf", "search_auto")

        assert _colonne(data_manager, morceau.id, "youtube_url") == ("neuf",)

    def test_effacement_repasse_en_recherche_live(self, data_manager, morceau):
        data_manager.update_track_youtube_url(morceau.id, "u1", "manual")

        assert data_manager.clear_track_youtube_link(morceau.id) is True
        assert _colonne(data_manager, morceau.id, "youtube_url", "youtube_url_source") == (
            None,
            None,
        )

    def test_replis_si_la_base_est_indisponible(self, data_manager, morceau, moteur_casse):
        moteur_casse(data_manager)

        assert data_manager.update_track_youtube_url(morceau.id, "u", "manual") is False
        assert data_manager.clear_track_youtube_link(morceau.id) is False


class TestSpotifyId:
    def test_backfill_sur_un_morceau_sans_id(self, data_manager, morceau):
        assert data_manager.update_track_spotify_id(morceau.id, "sp1") is True
        assert _colonne(data_manager, morceau.id, "spotify_id") == ("sp1",)

    def test_ne_remplace_jamais_un_id_existant(self, data_manager, morceau):
        """Le backfill Kworb ne doit pas écraser l'ID scrapé, plus sûr."""
        data_manager.update_track_spotify_id(morceau.id, "sp1")
        data_manager.update_track_spotify_id(morceau.id, "sp2")

        assert _colonne(data_manager, morceau.id, "spotify_id") == ("sp1",)

    def test_repli_si_la_base_est_indisponible(self, data_manager, morceau, moteur_casse):
        moteur_casse(data_manager)
        assert data_manager.update_track_spotify_id(morceau.id, "x") is False


class TestStreamsEtVues:
    def test_streams_spotify(self, data_manager, morceau):
        assert (
            data_manager.record_spotify_streams(morceau.id, 1_000, "kworb", daily_streams=50)
            is True
        )
        assert _colonne(data_manager, morceau.id, "spotify_streams") == (1_000,)

    def test_streams_ytm(self, data_manager, morceau):
        assert data_manager.update_track_ytm_streams(morceau.id, 2_000) is True
        assert _colonne(data_manager, morceau.id, "ytm_streams") == (2_000,)

    def test_vues_de_clip(self, data_manager, morceau):
        assert data_manager.update_track_video_views(morceau.id, 500, kind="clip") is True
        assert _colonne(data_manager, morceau.id, "youtube_video_views") == (500,)

    def test_replis_si_la_base_est_indisponible(self, data_manager, morceau, moteur_casse):
        moteur_casse(data_manager)

        assert data_manager.record_spotify_streams(morceau.id, 1, "kworb", daily_streams=0) is False
        assert data_manager.update_track_ytm_streams(morceau.id, 1) is False
        assert data_manager.update_track_video_views(morceau.id, 1) is False


class TestAlbums:
    def test_upsert_puis_lecture(self, data_manager, artiste):
        assert data_manager.upsert_album(artiste.id, "Album A", streams=10, daily_streams=1) is True

        albums = data_manager.get_albums_for_artist(artiste.id)
        assert [(a["title"], a["spotify_streams"]) for a in albums] == [("Album A", 10)]

    def test_upsert_met_a_jour_sans_dupliquer(self, data_manager, artiste):
        data_manager.upsert_album(artiste.id, "Album A", streams=10, daily_streams=1)
        data_manager.upsert_album(artiste.id, "Album A", streams=20, daily_streams=2)

        albums = data_manager.get_albums_for_artist(artiste.id)
        assert len(albums) == 1 and albums[0]["spotify_streams"] == 20

    def test_tri_par_streams_decroissants(self, data_manager, artiste):
        data_manager.upsert_album(artiste.id, "Petit", streams=1, daily_streams=1)
        data_manager.upsert_album(artiste.id, "Gros", streams=99, daily_streams=9)

        assert [a["title"] for a in data_manager.get_albums_for_artist(artiste.id)] == [
            "Gros",
            "Petit",
        ]

    def test_streams_ytm_dalbum(self, data_manager, artiste):
        data_manager.upsert_album(artiste.id, "Album A", streams=10, daily_streams=1)
        assert data_manager.update_album_ytm_streams(artiste.id, "Album A", 42) is True

        assert data_manager.get_albums_for_artist(artiste.id)[0]["ytm_streams"] == 42

    def test_artiste_sans_album(self, data_manager, artiste):
        assert data_manager.get_albums_for_artist(artiste.id) == []

    def test_detachement_manuel_dun_morceau(self, data_manager, morceau):
        """`album_override=1` empêche le prefill API de re-remplir le champ."""
        assert data_manager.clear_track_album(morceau.id) is True
        assert _colonne(data_manager, morceau.id, "album", "album_override") == (None, 1)

    def test_replis_si_la_base_est_indisponible(self, data_manager, artiste, morceau, moteur_casse):
        moteur_casse(data_manager)

        assert data_manager.upsert_album(artiste.id, "A", streams=1, daily_streams=1) is False
        assert data_manager.get_albums_for_artist(artiste.id) == []
        assert data_manager.update_album_ytm_streams(artiste.id, "A", 1) is False
        assert data_manager.clear_track_album(morceau.id) is False


class TestRenommageEtSuppression:
    def test_renommage(self, data_manager, morceau):
        """Aligner un titre sur Kworb (« Matrix (Intro) » → « Matrix »)."""
        assert data_manager.rename_track(morceau.id, "  Matrix  ") is True
        assert _colonne(data_manager, morceau.id, "title") == ("Matrix",)

    def test_renommage_vers_un_titre_deja_pris(self, data_manager, artiste, morceau):
        """UNIQUE(title, artist_id) : le repli est False, pas une exception."""
        autre = Track(title="Déjà pris", artist=artiste)
        data_manager.save_track(autre)

        assert data_manager.rename_track(morceau.id, "Déjà pris") is False
        assert _colonne(data_manager, morceau.id, "title") == ("Morceau",)

    def test_suppression(self, data_manager, morceau):
        assert data_manager.delete_track(morceau.id) is True
        assert _colonne(data_manager, morceau.id, "title") is None

    def test_replis_si_la_base_est_indisponible(self, data_manager, morceau, moteur_casse):
        moteur_casse(data_manager)

        assert data_manager.rename_track(morceau.id, "X") is False
        assert data_manager.delete_track(morceau.id) is False


class TestLectureDesMorceaux:
    def test_artiste_sans_morceau(self, data_manager, artiste):
        assert data_manager.get_artist_tracks(artiste.id) == []

    def test_repli_si_la_base_est_indisponible(self, data_manager, artiste, moteur_casse):
        moteur_casse(data_manager)
        assert data_manager.get_artist_tracks(artiste.id) == []


class TestDatesLibres:
    """Piège TIMESTAMP double-face : `date_bind` accepte string ET datetime en
    écriture, et la lecture rend la valeur VERBATIM (chemin `text()` non typé)."""

    @pytest.mark.parametrize("valeur", ["2026-01-02 03:04:05", datetime(2026, 1, 2, 3, 4, 5)])
    def test_streams_updated_accepte_les_deux_formes(self, data_manager, morceau, valeur):
        assert (
            data_manager.record_spotify_streams(
                morceau.id, 10, "kworb", updated_at=valeur, daily_streams=1
            )
            is True
        )
