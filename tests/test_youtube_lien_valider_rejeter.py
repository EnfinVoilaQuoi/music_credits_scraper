"""Valider / rejeter le lien YouTube proposé automatiquement (lot 2.3).

La bande 0,85-0,90 était un cul-de-sac : un lien affiché « auto • 87 % »
n'atteignait pas le seuil de persistance, n'était donc jamais enregistré, et ses
vues n'étaient jamais comptées. Ces fonctions sont ce qui permet d'en sortir à
la main — dans un sens (valider) comme dans l'autre (rejeter).

Aucun réseau : le chercheur est un faux, et l'import du module ne construit plus
le vrai (singleton paresseux).
"""

from types import SimpleNamespace

import pytest

from src.models import TrackVideo
from src.utils.youtube_integration import (
    clear_youtube_link,
    lien_youtube_valide,
    reject_youtube_link,
    set_youtube_link,
)
from src.utils.youtube_utils import artiste_de_recherche

_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
_VID = "dQw4w9WgXcQ"


class _FauxDM:
    def __init__(self):
        self.urls = []
        self.videos = []
        self.oubliees = []
        self.cleared = []

    def update_track_youtube_url(self, track_id, url, source):
        self.urls.append((track_id, url, source))
        return True

    def record_track_videos(self, track_id, videos):
        self.videos.append((track_id, [(v.video_id, v.source) for v in videos]))
        return len(videos)

    def forget_track_video(self, track_id, video_id):
        self.oubliees.append((track_id, video_id))
        return True

    def clear_track_youtube_link(self, track_id):
        self.cleared.append(track_id)
        return True


class _FauxChercheur:
    def __init__(self):
        self.purges = []

    def forget(self, artist, title):
        self.purges.append((artist, title))
        return True


def _track(url=None, source=None, videos=None, is_featuring=False, primary=None):
    return SimpleNamespace(
        id=7,
        title="Magot",
        youtube_url=url,
        youtube_url_source=source,
        videos=list(videos or []),
        is_featuring=is_featuring,
        primary_artist_name=primary,
    )


class TestLienValide:
    @pytest.mark.parametrize(
        "url",
        ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ"],
    )
    def test_une_url_de_video(self, url):
        assert lien_youtube_valide(url) == _VID

    def test_une_url_de_RECHERCHE_est_refusee(self):
        """Faux ami : elle s'ouvre dans le navigateur mais ne désigne aucune
        vidéo — l'enregistrer poserait un lien dont aucune vue ne sera comptée."""
        assert lien_youtube_valide("https://www.youtube.com/results?search_query=magot") is None

    def test_rien(self):
        assert lien_youtube_valide(None) is None
        assert lien_youtube_valide("") is None


class TestSetYoutubeLink:
    def test_ecrit_les_deux_magasins(self):
        """La colonne porte la vidéo principale, `track_videos` alimente la somme
        des vues : n'écrire que la première laisserait le total ignorer le lien
        que l'utilisateur vient de valider."""
        dm, track = _FauxDM(), _track()

        assert set_youtube_link(dm, track, _URL) is True

        assert dm.urls == [(7, _URL, "manual")]
        assert dm.videos == [(7, [(_VID, "manual")])]
        assert (track.youtube_url, track.youtube_url_source) == (_URL, "manual")
        assert [v.video_id for v in track.videos] == [_VID]

    def test_une_url_invalide_necrit_rien(self):
        dm, track = _FauxDM(), _track()
        assert set_youtube_link(dm, track, "https://example.com/x") is False
        assert (dm.urls, dm.videos) == ([], [])

    def test_ne_duplique_pas_une_video_deja_portee(self):
        dm = _FauxDM()
        track = _track(videos=[TrackVideo(video_id=_VID, source="genius_media")])
        set_youtube_link(dm, track, _URL)
        assert len(track.videos) == 1


class TestClearYoutubeLink:
    def test_oublie_aussi_la_video(self):
        """Depuis que `ytm_streams` somme toutes les vidéos connues, laisser la
        ligne en base ferait continuer de compter les vues d'un lien qu'on vient
        de juger faux."""
        dm = _FauxDM()
        track = _track(_URL, "search_auto", videos=[TrackVideo(video_id=_VID)])

        clear_youtube_link(dm, track)

        assert dm.cleared == [7]
        assert dm.oubliees == [(7, _VID)]
        assert track.videos == []
        assert (track.youtube_url, track.youtube_url_source) == (None, None)


class TestRejectYoutubeLink:
    def test_les_trois_gestes(self):
        dm, chercheur = _FauxDM(), _FauxChercheur()
        track = _track(_URL, "search_auto", videos=[TrackVideo(video_id=_VID)])

        reject_youtube_link(dm, track, _URL, "Isha", searcher=chercheur)

        assert dm.oubliees == [(7, _VID)]  # la vidéo ne compte plus
        assert dm.cleared == [7]  # la colonne portait bien ce lien
        assert chercheur.purges == [("Isha", "Magot")]  # la recherche est purgée
        assert track.videos == []

    def test_ne_touche_pas_une_colonne_qui_porte_un_AUTRE_lien(self):
        """Rejeter une proposition ne doit pas effacer le lien Genius en place."""
        dm, chercheur = _FauxDM(), _FauxChercheur()
        track = _track("https://youtu.be/aaaaaaaaaaa", "genius_media")

        reject_youtube_link(dm, track, _URL, "Isha", searcher=chercheur)

        assert dm.cleared == []
        assert track.youtube_url == "https://youtu.be/aaaaaaaaaaa"
        assert dm.oubliees == [(7, _VID)]

    def test_la_purge_vise_lartiste_PRINCIPAL_dun_featuring(self):
        """La recherche a été faite sous ce nom : purger sous celui de l'invité
        viderait une entrée de cache que personne n'a écrite."""
        dm, chercheur = _FauxDM(), _FauxChercheur()
        track = _track(is_featuring=True, primary="Caballero & JeanJass")

        reject_youtube_link(dm, track, _URL, "Isha", searcher=chercheur)

        assert chercheur.purges == [("Caballero & JeanJass", "Magot")]


class TestArtisteDeRecherche:
    def test_featuring_cherche_sous_le_principal(self):
        track = _track(is_featuring=True, primary="Hatik")
        assert artiste_de_recherche(track, "Isha") == "Hatik"

    def test_morceau_principal_cherche_sous_lartiste_courant(self):
        assert artiste_de_recherche(_track(), "Isha") == "Isha"

    def test_featuring_sans_principal_connu_retombe_sur_le_courant(self):
        track = _track(is_featuring=True, primary=None)
        assert artiste_de_recherche(track, "Isha") == "Isha"
