"""Les VIDÉOS d'un enregistrement valent pour toutes ses lignes sœurs.

`track_videos` n'était unie que par `save_track`, sans le relevé ni les refus :
45 familles à jeux différents (« Mr No » chez Caballero, pas chez JeanJass), une
même vidéo à des vues différentes selon la ligne (2026-11-06). Le jugement des
vues et le partage dans la discographie de l'artiste restent par ligne.
"""

import pytest
from sqlalchemy import text

from src.models import Artist, Track, TrackVideo
from src.utils import corrections_fiches as cf

_GENIUS = 4242


def _ligne(dm, nom, titre="160 Rimes", genius_id=_GENIUS):
    artiste = dm.get_artist_by_name(nom)
    if artiste is None:
        artiste = Artist(name=nom)
        artiste.id = dm.save_artist(artiste)
    track = Track(title=titre, artist=artiste)
    track.genius_id = genius_id
    dm.save_track(track)
    return track


@pytest.fixture
def soeurs(data_manager):
    return _ligne(data_manager, "Caballero"), _ligne(data_manager, "JeanJass")


def _videos(dm, track_id):
    with dm.engine.connect() as conn:
        return {
            r["video_id"]: r
            for r in conn.execute(
                text(
                    "SELECT video_id, views, views_updated, source, title, duration, channel, "
                    "recherche FROM track_videos WHERE track_id = :t"
                ),
                {"t": track_id},
            )
            .mappings()
            .all()
        }


def _ytm(dm, track_id):
    with dm.engine.connect() as conn:
        return conn.execute(
            text("SELECT ytm_streams FROM tracks WHERE id = :t"), {"t": track_id}
        ).scalar()


def _dater(dm, track_id, video_id, quand, vues):
    with dm.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE track_videos SET views = :v, views_updated = :q "
                "WHERE track_id = :t AND video_id = :i"
            ),
            {"v": vues, "q": quand, "t": track_id, "i": video_id},
        )


class TestUnion:
    def test_une_video_posee_sur_une_ligne_arrive_chez_la_soeur(self, data_manager, soeurs):
        caballero, jeanjass = soeurs
        data_manager.record_track_videos(
            caballero.id,
            [
                TrackVideo(
                    video_id="AAAAAAAAAAA",
                    source="search_auto",
                    views=500,
                    title="Mr No",
                    duration=180,
                    channel="Caballero",
                )
            ],
        )
        recue = _videos(data_manager, jeanjass.id)["AAAAAAAAAAA"]
        assert (recue["views"], recue["source"], recue["title"]) == (500, "search_auto", "Mr No")
        assert (recue["duration"], recue["channel"]) == (180, "Caballero")
        assert _ytm(data_manager, jeanjass.id) == 500

    def test_le_releve_le_plus_frais_vaut_pour_toutes(self, data_manager, soeurs):
        caballero, jeanjass = soeurs
        video = TrackVideo(video_id="AAAAAAAAAAA", source="ytm_album", views=100, title="x")
        data_manager.record_track_videos(caballero.id, [video])
        _dater(data_manager, caballero.id, "AAAAAAAAAAA", "2026-01-01 00:00:00", 100)
        _dater(data_manager, jeanjass.id, "AAAAAAAAAAA", "2026-06-01 00:00:00", 900)
        data_manager.save_track(caballero)
        for t in soeurs:
            assert _videos(data_manager, t.id)["AAAAAAAAAAA"]["views"] == 900
            assert _ytm(data_manager, t.id) == 900

    def test_une_ligne_a_vues_vides_ne_vide_pas_la_soeur(self, data_manager, soeurs):
        caballero, jeanjass = soeurs
        data_manager.record_track_videos(
            caballero.id, [TrackVideo(video_id="AAAAAAAAAAA", source="ytm_album", views=100)]
        )
        data_manager.record_track_videos(
            jeanjass.id, [TrackVideo(video_id="AAAAAAAAAAA", source="ytm_album", title="relu")]
        )
        for t in soeurs:
            ligne = _videos(data_manager, t.id)["AAAAAAAAAAA"]
            assert (ligne["views"], ligne["title"]) == (100, "relu")

    def test_la_provenance_la_plus_forte_gagne(self, data_manager, soeurs):
        caballero, jeanjass = soeurs
        data_manager.record_track_videos(
            caballero.id, [TrackVideo(video_id="AAAAAAAAAAA", source="genius_media")]
        )
        data_manager.record_track_videos(
            jeanjass.id,
            [
                TrackVideo(
                    video_id="AAAAAAAAAAA",
                    source="search_auto",
                    recherche={"titre": "160 Rimes", "artistes": ["Caballero"]},
                )
            ],
        )
        for t in soeurs:
            ligne = _videos(data_manager, t.id)["AAAAAAAAAAA"]
            assert ligne["source"] == "genius_media"
            assert ligne["recherche"]

    def test_le_lien_prend_les_vues_du_releve_le_plus_frais(self, data_manager, soeurs):
        caballero, jeanjass = soeurs
        url = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
        with data_manager.engine.begin() as conn:
            for track_id, vues, quand in (
                (caballero.id, 100, "2026-01-01 00:00:00"),
                (jeanjass.id, 900, "2026-06-01 00:00:00"),
            ):
                conn.execute(
                    text(
                        "UPDATE tracks SET youtube_url = :u, youtube_url_source = 'genius_media', "
                        "youtube_video_views = :v, youtube_video_views_updated = :q WHERE id = :t"
                    ),
                    {"u": url, "v": vues, "q": quand, "t": track_id},
                )
        data_manager.save_track(caballero)
        for t in soeurs:
            with data_manager.engine.connect() as conn:
                assert (
                    conn.execute(
                        text("SELECT youtube_video_views FROM tracks WHERE id = :t"), {"t": t.id}
                    ).scalar()
                    == 900
                )


class TestRefusEtRetrait:
    def test_un_refus_de_page_bloque_l_union(self, data_manager, soeurs):
        """Une vidéo restée chez une sœur malgré le refus ne se recopie pas."""
        caballero, jeanjass = soeurs
        data_manager.record_track_videos(
            caballero.id, [TrackVideo(video_id="AAAAAAAAAAA", source="ytm_album", views=100)]
        )
        with data_manager.engine.begin() as conn:
            conn.execute(text("DELETE FROM track_videos WHERE track_id = :t"), {"t": jeanjass.id})
        cf.memoriser_video_refusee(jeanjass, "AAAAAAAAAAA")
        data_manager.save_track(caballero)
        assert "AAAAAAAAAAA" not in _videos(data_manager, jeanjass.id)

    def test_un_retrait_tient_apres_une_ecriture_de_la_soeur(self, data_manager, soeurs):
        caballero, jeanjass = soeurs
        data_manager.record_track_videos(
            caballero.id, [TrackVideo(video_id="AAAAAAAAAAA", source="ytm_album", views=100)]
        )
        assert data_manager.forget_track_video(jeanjass.id, "AAAAAAAAAAA")
        data_manager.record_track_videos(
            jeanjass.id, [TrackVideo(video_id="BBBBBBBBBBB", source="ytm_album", views=7)]
        )
        for t in soeurs:
            assert set(_videos(data_manager, t.id)) == {"BBBBBBBBBBB"}


class TestParLigne:
    def test_le_partage_reste_propre_a_l_artiste(self, data_manager, soeurs):
        """« Bethléem / Goût du beurre » : la vidéo arrive chez les deux sœurs,
        elle ne compte pas chez l'artiste qui la porte aussi sur une autre fiche."""
        caballero, jeanjass = soeurs
        autre = _ligne(data_manager, "Caballero", titre="Goût du beurre", genius_id=9999)
        video = TrackVideo(video_id="AAAAAAAAAAA", source="ytm_album", views=500)
        data_manager.record_track_videos(autre.id, [video])
        data_manager.record_track_videos(jeanjass.id, [video])

        assert set(_videos(data_manager, caballero.id)) == {"AAAAAAAAAAA"}
        assert not _ytm(data_manager, caballero.id)
        assert not _ytm(data_manager, autre.id)
        assert _ytm(data_manager, jeanjass.id) == 500

    def test_idempotent(self, data_manager, soeurs):
        from src.utils.track_soeurs import unir_videos

        caballero, jeanjass = soeurs
        data_manager.record_track_videos(
            caballero.id, [TrackVideo(video_id="AAAAAAAAAAA", source="ytm_album", views=100)]
        )
        with data_manager.engine.begin() as conn:
            assert unir_videos(conn, [caballero.id, jeanjass.id]) == 0


def test_le_rattrapage_a_blanc_n_ecrit_rien_et_apply_aligne(data_manager, soeurs):
    caballero, jeanjass = soeurs
    with data_manager.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO track_videos (track_id, video_id, source, views) "
                "VALUES (:t, 'AAAAAAAAAAA', 'search_auto', 500)"
            ),
            {"t": caballero.id},
        )
    assert data_manager.realigner_soeurs() == {_GENIUS: 1}
    assert not _videos(data_manager, jeanjass.id)

    data_manager.realigner_soeurs(appliquer=True)
    assert set(_videos(data_manager, jeanjass.id)) == {"AAAAAAAAAAA"}
    assert data_manager.realigner_soeurs() == {}
