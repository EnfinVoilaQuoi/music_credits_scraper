"""Rattrapage de `youtube_video_views` posé avant e5266ec : sans méta pour la
vidéo du lien, `update_video_views` écrivait les vues (et la nature) de la
première AUTRE vidéo mesurée. La colonne reprend le compteur de la vidéo du
lien quand une ligne de `track_videos` l'a mesurée, sinon elle se vide."""

from sqlalchemy import text

from src.models import Artist, Track, TrackVideo

_LIEN = "GZSrUBGqRGI"
_AUTRE = "uLFNAYJn5LA"


def _fiche(dm, nom="A2H", titre="Allume la lumière", genius_id=None) -> Track:
    a = Artist(name=nom)
    a.id = dm.save_artist(a)
    t = Track(title=titre, artist=a)
    t.genius_id = genius_id
    t.youtube_url = f"https://www.youtube.com/watch?v={_LIEN}"
    t.youtube_url_source = "genius_media"
    dm.save_track(t)
    return t


def _colonnes(dm, tid):
    with dm.engine.connect() as conn:
        return tuple(
            conn.execute(
                text(
                    "SELECT youtube_video_views, youtube_video_kind, youtube_video_views_updated "
                    "FROM tracks WHERE id = :t"
                ),
                {"t": tid},
            ).one()
        )


def _poser_vues_de_l_autre(dm, tid, vues=35495, nature="audio"):
    """L'état laissé par l'ancien `update_video_views`."""
    with dm.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE tracks SET youtube_video_views = :v, youtube_video_kind = :k, "
                "youtube_video_views_updated = '2026-10-16 00:43:19' WHERE id = :t"
            ),
            {"v": vues, "k": nature, "t": tid},
        )


def test_lien_jamais_releve_la_colonne_se_vide(data_manager):
    t = _fiche(data_manager)
    data_manager.record_track_videos(
        t.id,
        [
            TrackVideo(video_id=_LIEN, source="genius_media"),
            TrackVideo(video_id=_AUTRE, source="search_auto", kind="audio", views=35495),
        ],
    )
    _poser_vues_de_l_autre(data_manager, t.id)

    (cas,) = data_manager.vues_d_une_autre_video()
    assert (cas["track_id"], cas["autre_video"], cas["juste"]) == (t.id, _AUTRE, None)
    assert _colonnes(data_manager, t.id)[0] == 35495  # à blanc : rien d'écrit

    assert data_manager.rattraper_vues_du_lien([cas]) == 1
    assert _colonnes(data_manager, t.id) == (None, None, None)
    assert not data_manager.vues_d_une_autre_video()


def test_lien_mesure_la_colonne_reprend_ses_vues(data_manager):
    t = _fiche(data_manager)
    data_manager.record_track_videos(
        t.id,
        [
            TrackVideo(video_id=_LIEN, source="genius_media", kind="clip", views=488576),
            TrackVideo(video_id=_AUTRE, source="search_auto", kind="audio", views=6891143),
        ],
    )
    _poser_vues_de_l_autre(data_manager, t.id, vues=6891143)

    (cas,) = data_manager.vues_d_une_autre_video()
    assert cas["juste"][0] == 488576 and cas["nature_apres"] == "clip"
    data_manager.rattraper_vues_du_lien([cas])
    vues, nature, releve = _colonnes(data_manager, t.id)
    assert (vues, nature) == (488576, "clip") and releve is not None


def test_une_nature_qui_n_est_pas_celle_de_l_autre_video_reste(data_manager):
    t = _fiche(data_manager)
    data_manager.record_track_videos(
        t.id, [TrackVideo(video_id=_AUTRE, source="search_auto", kind="audio", views=35495)]
    )
    _poser_vues_de_l_autre(data_manager, t.id, nature="clip")

    (cas,) = data_manager.vues_d_une_autre_video()
    data_manager.rattraper_vues_du_lien([cas])
    assert _colonnes(data_manager, t.id) == (None, "clip", None)


def test_vues_justes_ou_sans_autre_video_rien_a_faire(data_manager):
    t = _fiche(data_manager)
    data_manager.record_track_videos(
        t.id, [TrackVideo(video_id=_LIEN, source="genius_media", kind="clip", views=10)]
    )
    _poser_vues_de_l_autre(data_manager, t.id, vues=10)
    assert not data_manager.vues_d_une_autre_video()
    # Un compteur qu'aucune vidéo de la fiche n'explique : rien ne le montre faux.
    _poser_vues_de_l_autre(data_manager, t.id, vues=999)
    assert not data_manager.vues_d_une_autre_video()


def test_les_soeurs_videes_ensemble_ne_se_rendent_rien(data_manager):
    t = _fiche(data_manager, genius_id=77)
    s = _fiche(data_manager, nom="Flynt", genius_id=77)
    for f in (t, s):
        data_manager.record_track_videos(
            f.id, [TrackVideo(video_id=_AUTRE, source="search_auto", kind="audio", views=35495)]
        )
        _poser_vues_de_l_autre(data_manager, f.id)

    cas = data_manager.vues_d_une_autre_video()
    assert {c["track_id"] for c in cas} == {t.id, s.id}
    data_manager.rattraper_vues_du_lien(cas)
    t.title = "Allume la lumière"
    data_manager.save_track(t)  # synchronisation des sœurs : rien ne revient
    assert _colonnes(data_manager, t.id) == _colonnes(data_manager, s.id) == (None, None, None)
