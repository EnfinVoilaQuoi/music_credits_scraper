"""Pages Genius « Non-Music » : seule une SORTIE OFFICIELLE les garde
(décision utilisateur 2026-11-10). Une vidéo ne protège plus la page."""

from __future__ import annotations

import json

from sqlalchemy import text

from src.models import Artist, Track, TrackVideo
from src.services import revue
from src.utils.sortie_officielle import dans_la_liste, sorties_par_leur_disque


class TestDansLaListe:
    def test_titre_exact_a_la_graphie_pres(self):
        assert dans_la_liste("Interlude Fatima", ["Intro", "Interlude Fatima"])

    def test_l_annee_ajoutee_par_genius_ne_compte_pas(self):
        """« Paul (Skit) [2009] » : celui de *Relapse*, pas celui de 1999."""
        assert dans_la_liste("Paul (Skit) [2009]", ["3 a.m.", "Paul (Skit)"])

    def test_la_partie_d_un_titre_joint(self):
        assert dans_la_liste("Encore / Curtains Up", ["Curtains Up", "Evil Deeds"])

    def test_la_pochette_n_est_pas_le_morceau_titre(self):
        """« Bourré au son (Pochette) » de La Fouine : le titre de base
        tomberait sur le morceau-titre de l'album."""
        assert not dans_la_liste("Bourré au son (Pochette)", ["Bourré au son"])


def _page(dm, artiste, titre, genius_id, album=None):
    t = Track(title=titre, artist=artiste, genius_id=genius_id, album=album)
    t.genius_url = f"https://genius.com/x-{genius_id}-annotated"
    t.lyrics.text = "[Skit: Eminem]\nYo Paul, what's up, call me back"
    t.lyrics.present = True
    dm.save_track(t)
    return t


def _cas_annotee(dm, artiste):
    artiste.tracks = dm.get_artist_tracks(artiste.id)
    ctx = revue.charger_contexte(dm, artiste)
    return {c.morceau for c in revue.analyser(dm, artiste, ctx).actifs if c.detecteur == "annotee"}


def test_seule_la_sortie_officielle_garde_la_page(data_manager):
    from src.persistence import brut

    em = Artist(name="Eminem")
    em.id = data_manager.save_artist(em)
    _page(data_manager, em, "Paul (Skit) [2009]", 1, album="Relapse")
    snippet = _page(data_manager, em, "Ariel (Skit)", 2, album="The Slim Shady LP (Snippet Tape)")
    data_manager.record_track_videos(
        snippet.id, [TrackVideo(video_id="AAAAAAAAAAA", source="genius_media", views=10)]
    )
    sur_apple = _page(data_manager, em, "Curtains Up (Skit)", 3, album="The Eminem Show")
    with data_manager.engine.begin() as conn:
        conn.execute(
            text("UPDATE tracks SET apple_music_id = '42' WHERE id = :t"), {"t": sur_apple.id}
        )
        conn.execute(
            text(
                "INSERT INTO releases (artist_id, title, deezer_album_id, identity_key) "
                "VALUES (:a, 'Relapse', 350198, 'relapse')"
            ),
            {"a": em.id},
        )
    corps = {"data": [{"id": 1, "title": "Dr. West (Skit)"}, {"id": 2, "title": "Paul (Skit)"}]}
    brut.consigner(
        "deezer",
        "https://api.deezer.com/album/350198/tracks?limit=100",
        200,
        json.dumps(corps).encode(),
    )
    brut.fermer()

    # La vidéo d'un skit de cassette promo ne le protège plus ; l'ID Apple et
    # la liste Deezer de *Relapse* prouvent la sortie des deux autres.
    assert _cas_annotee(data_manager, em) == {"Ariel (Skit)"}


def test_sans_liste_ni_id_la_page_est_jugee(data_manager):
    em = Artist(name="Eminem")
    em.id = data_manager.save_artist(em)
    t = _page(data_manager, em, "Paul (Skit) [2009]", 1, album="Relapse")
    em.tracks = [t]
    with data_manager.engine.connect() as conn:
        assert sorties_par_leur_disque(conn, em.tracks) == set()
    assert _cas_annotee(data_manager, em) == {"Paul (Skit) [2009]"}
