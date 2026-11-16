"""Refus d'un PROFIL Spotify d'artiste (2026-11-16, The Classmates).

Le duo de Travis Scott n'est pas sur Spotify : sans ID Spotify de fiche, le vote
de l'étape identité retombe sur une recherche par NOM et pose le profil d'un
homonyme (5156YRN7…, reprises rock). Refusé, le profil ne revient ni par les
voix ni par la recherche, et le profil mémorisé part.
"""

from src.models import Artist, Track
from src.utils import corrections_fiches as cf
from src.utils import update_kworb as uk

HOMONYME = "5156YRN7cvcszG3asLPcTF"


def _artiste(dm, nom="The Classmates", spotify_id=None):
    a = Artist(name=nom)
    a.spotify_id = spotify_id
    a.id = dm.save_artist(a)
    return a


class _Scraper:
    """Faux `SpotifyIDScraper` : chaque piste vote pour `voix[sid]`, la
    recherche par nom rend `par_nom`."""

    def __init__(self, voix, par_nom):
        self.voix, self.par_nom = voix, par_nom

    def __call__(self, headless=True):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_artist_id_from_track(self, sid, expected_name=None):
        return self.voix.get(sid)

    def get_artist_spotify_id(self, nom):
        return self.par_nom


def _brancher(monkeypatch, voix, par_nom):
    import src.scrapers.spotify_id_scraper_v2 as module

    monkeypatch.setattr(module, "SpotifyIDScraper", _Scraper(voix, par_nom))


class TestRefusDeProfilSpotify:
    def test_refuser_puis_oublier(self, data_manager):
        assert cf.profils_spotify_refuses("The Classmates") == set()
        assert cf.memoriser_profil_spotify_refuse("The Classmates", HOMONYME, "The Classmates")
        assert not cf.memoriser_profil_spotify_refuse("The Classmates", HOMONYME, "The Classmates")
        assert cf.profils_spotify_refuses("The Classmates") == {HOMONYME}
        assert cf.profils_spotify_refuses("Travis Scott") == set()
        assert cf.oublier_profil_spotify_refuse("The Classmates", HOMONYME)
        assert cf.profils_spotify_refuses("The Classmates") == set()

    def test_le_profil_retire_quitte_l_artiste_et_ses_profils(self, data_manager):
        a = _artiste(data_manager, spotify_id=HOMONYME)
        data_manager.record_artist_spotify_ids(a.id, [HOMONYME, "AUTRE"], "musicbrainz")
        assert data_manager.retirer_profil_spotify(a.id, HOMONYME)
        assert data_manager.get_artist_by_name("The Classmates").spotify_id is None
        assert data_manager.ids_spotify_connus(a.id) == {"AUTRE"}
        assert not data_manager.retirer_profil_spotify(a.id, HOMONYME)


class TestVote:
    def test_la_recherche_par_nom_ne_rend_pas_un_profil_refuse(self, data_manager, monkeypatch):
        a = _artiste(data_manager)
        cf.memoriser_profil_spotify_refuse(a.name, HOMONYME, a.name)
        _brancher(monkeypatch, {}, HOMONYME)
        assert uk._vote_artist_spotify_id(a, data_manager) is None

    def test_un_profil_refuse_ne_vote_pas(self, data_manager, monkeypatch):
        a = _artiste(data_manager)
        for i in range(3):
            data_manager.save_track(Track(title=f"T{i}", artist=a, spotify_id=f"S{i}"))
        cf.memoriser_profil_spotify_refuse(a.name, HOMONYME, a.name)
        _brancher(monkeypatch, {"S0": HOMONYME, "S1": HOMONYME, "S2": "BON"}, None)
        assert uk._vote_artist_spotify_id(a, data_manager) == "BON"

    def test_sans_refus_le_vote_est_inchange(self, data_manager, monkeypatch):
        a = _artiste(data_manager)
        _brancher(monkeypatch, {}, HOMONYME)
        assert uk._vote_artist_spotify_id(a, data_manager) == HOMONYME
