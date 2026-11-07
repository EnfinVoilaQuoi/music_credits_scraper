"""Un générique, ou un côté de « A : B », se prouve par le disque : chaîne Topic
d'un interprète ou page d'album YTM, ET durée indépendante à 2 s (2026-11-06)."""

from types import SimpleNamespace

from src.services.revue import video_nomme


def _fiche(titre, artiste, durees):
    return SimpleNamespace(
        title=titre,
        artist=SimpleNamespace(name=artiste),
        primary_artist_name=None,
        secondary_role=None,
        featured_artists_list=[],
        durations_observees=durees,
    )


def _video(titre, source, duree=None, chaine=None):
    return SimpleNamespace(title=titre, source=source, duration=duree, channel=chaine)


def test_generique_de_la_chaine_topic_a_la_bonne_duree():
    fiche = _fiche("Intro (LAX)", "The Game", {"deezer": 80, "youtube": 81})
    assert video_nomme(
        fiche, _video("Intro (Album Version)", "search_auto", 81, "The Game - Topic")
    )


def test_generique_sans_duree_independante_refuse():
    """La durée lue par la vidéo elle-même ne la corrobore pas."""
    fiche = _fiche("Intro (LAX)", "The Game", {"youtube": 81})
    assert not video_nomme(fiche, _video("Intro", "search_auto", 81, "The Game - Topic"))


def test_generique_d_une_chaine_etrangere_refuse():
    fiche = _fiche("Intro (LAX)", "The Game", {"deezer": 80})
    assert not video_nomme(fiche, _video("Intro", "search_auto", 80, "Autre Rappeur - Topic"))


def test_partie_d_un_titre_a_deux_points_par_l_album_ytm():
    fiche = _fiche("Entre les lignes : prière", "Keny Arkana", {"deezer": 258})
    assert video_nomme(fiche, _video("Keny Arkana - Prière", "ytm_album", 259, "Keny Arkana"))
    assert not video_nomme(fiche, _video("Keny Arkana - Prière", "search_auto", 259, "Keny Arkana"))


def test_heartless_de_dermot_kennedy_ne_passe_pas():
    fiche = _fiche("Heartless - Recorded At RAK Studios", "Kanye West", {"deezer": 210})
    video = _video("Dermot Kennedy - Power Over Me", "ytm_album", 210, "Dermot Kennedy")
    assert not video_nomme(fiche, video)


def test_le_dernier_cote_de_deux_mots_suffit():
    fiche = _fiche("Entre les lignes : Une goutte de plus", "Keny Arkana", {})
    video = _video('clip arkana "une goutte de plus" sauvons la planete', "genius_media")
    assert video_nomme(fiche, video)


def test_le_cote_de_la_serie_ne_nomme_pas_le_morceau():
    """Mesuré sur la copie : un concert entier et des scènes de film comptaient."""
    fiche = _fiche("DAYS BEFORE RODEO: THE PRAYER (Live)", "Travis Scott", {"deezer": 200})
    video = _video(
        "TRAVIS SCOTT DAYS BEFORE RODEO LIVE 10 YEAR ANNIVERSARY IN ATLANTA", "genius_media", 3267
    )
    assert not video_nomme(fiche, video)
    fiche = _fiche("8 Mile: B-Rabbit vs Papa Doc", "Eminem", {})
    video = _video(
        "8 Mile (2002) - Rabbit Battles Papa Doc Scene (10/10) | Movieclips", "genius_media"
    )
    assert not video_nomme(fiche, video)


def test_un_juge_change_recalcule_les_totaux_des_videos_genius(data_manager):
    """`video_nomme` change, le code n'écrit rien : le portier suit `revue`, et son
    rejeu recalcule aussi les fiches à vidéo Genius (4 totaux décrochés sur la
    copie du 2026-11-06, « 8 Mile: Lotto vs B-Rabbit »)."""
    from sqlalchemy import text

    from src.models import Artist, Track, TrackVideo
    from src.services import regles

    assert "src.services.revue" in regles.PORTIERS["recherche_youtube"]
    artiste = Artist(name="Eminem")
    artiste.id = data_manager.save_artist(artiste)
    fiche = Track(title="8 Mile: Lotto vs B-Rabbit", artist=artiste, genius_id=7)
    data_manager.save_track(fiche)
    data_manager.record_track_videos(
        fiche.id,
        [
            TrackVideo(
                video_id="AAAAAAAAAAA", source="genius_media", views=9, title="B Rabbit vs Lotto"
            )
        ],
    )
    with data_manager.engine.begin() as conn:
        conn.execute(text("UPDATE tracks SET ytm_streams = NULL WHERE id = :t"), {"t": fiche.id})
    artiste.tracks = data_manager.get_artist_tracks(artiste.id)
    assert regles.rejouer_recherche_youtube(data_manager, artiste).startswith("1 total")
    with data_manager.engine.connect() as conn:
        total = conn.execute(
            text("SELECT ytm_streams FROM tracks WHERE id = :t"), {"t": fiche.id}
        ).scalar()
    assert total == 9
