"""Import d'un album Genius par URL — la route INDIRECTE par l'API authentifiée.

Cassé du 2026-08-24 (Cloudflare sur `genius.com/api`) au 2026-09-15. L'API
authentifiée n'a pas de recherche d'album ; on passe par `/search` (des
MORCEAUX), filtrés GRATUITEMENT sur `primary_artist`, puis `/songs/{id}`
jusqu'à `song.album.url` == l'URL, puis `/albums/{id}` + `/tracks`.
Mesuré sur Josman « M.A.N » : 9 requêtes, 9,7 s, 19 morceaux.
"""

import requests

from src.api.genius_api import GeniusAPI
from src.observability import source_usage
from src.observability.issues import IssueKind

URL = "https://genius.com/albums/Josman/M-a-n-black-roses-lost-feelings"


class _Reponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


def _hit(sid, artiste, titre="T"):
    return {"result": {"id": sid, "title": titre, "primary_artist": {"name": artiste}}}


def _song(album_url, album_id=876733):
    return {"response": {"song": {"album": {"id": album_id, "url": album_url}}}}


class _Api:
    """Sert les routes de l'API authentifiée d'après un plan ; journalise."""

    def __init__(self, recherches, songs, album=None, tracks=None):
        self.recherches, self.songs = recherches, songs
        self.album = album or {
            "response": {
                "album": {
                    "id": 876733,
                    "name": "M.A.N",
                    "artist": {"name": "Josman"},
                    "release_date": "2022-03-18",
                    "cover_art_url": "cover",
                }
            }
        }
        self.tracks = (
            tracks
            if tracks is not None
            else {
                "response": {
                    "tracks": [
                        {
                            "number": 1,
                            "song": {"id": 1, "title": "Intro", "lyrics_state": "complete"},
                        },
                        {
                            "number": 2,
                            "song": {"id": 2, "title": "POP", "lyrics_state": "incomplete"},
                        },
                        {"number": 3, "song": {}},  # sans id : ignoré
                    ]
                }
            }
        )
        self.appels = []

    def __call__(self, source, url, params=None, headers=None, timeout=None):
        self.appels.append((url.rsplit("api.genius.com", 1)[-1], (params or {}).get("q")))
        if url.endswith("/search"):
            r = self.recherches.get(params["q"], [])
            if isinstance(r, Exception):
                raise r
            return _Reponse({"response": {"hits": r}})
        if "/songs/" in url:
            r = self.songs[int(url.rsplit("/", 1)[-1])]
            if isinstance(r, Exception):
                raise r
            return _Reponse(r)
        if url.endswith("/tracks"):
            return (
                _Reponse(self.tracks)
                if not isinstance(self.tracks, Exception)
                else _lever(self.tracks)
            )
        return _Reponse(self.album)


def _lever(e):
    raise e


def _brancher(monkeypatch, api):
    monkeypatch.setattr(source_usage, "requests_get", api)
    monkeypatch.setattr("src.api.genius_api.GENIUS_API_KEY", "cle")
    source_usage.reset()
    return GeniusAPI.__new__(GeniusAPI)


class TestUrl:
    def test_decoupage(self):
        assert GeniusAPI._album_url_parts(URL + "/?x=1") == (
            URL,
            "Josman",
            "M-a-n-black-roses-lost-feelings",
        )
        for mauvaise in (
            "",
            "https://genius.com/Josman-intro-lyrics",
            "https://genius.com/albums/Josman",
        ):
            assert GeniusAPI._album_url_parts(mauvaise) is None

    def test_requetes_de_plus_en_plus_courtes(self):
        assert GeniusAPI._album_queries("Josman", "M-a-n-black-roses-lost-feelings") == [
            "Josman M a n black roses lost feelings",
            "Josman M a n",
            "Josman M a",
            "Josman",
        ]
        assert GeniusAPI._album_queries("Sch", "Jvlivs") == ["Sch Jvlivs", "Sch"]

    def test_filtre_artiste_par_mots_entiers(self):
        assert GeniusAPI._meme_artiste(_hit(1, "Josman"), "Josman")
        assert GeniusAPI._meme_artiste(_hit(1, "L'Or du Commun"), "Lor-du-commun") is False
        assert GeniusAPI._meme_artiste(_hit(1, "Genius France"), "Josman") is False
        assert GeniusAPI._meme_artiste({"result": {}}, "Josman") is False


class TestResolution:
    def test_trouve_au_second_detail_sans_payer_les_hits_etrangers(self, monkeypatch):
        api = _Api(
            recherches={
                "Josman M a n black roses lost feelings": [_hit(10, "Genius France")],
                "Josman M a n": [_hit(11, "Genius France"), _hit(12, "Josman"), _hit(13, "Josman")],
            },
            songs={12: _song("https://genius.com/albums/Josman/Autre"), 13: _song(URL + "/")},
        )
        g = _brancher(monkeypatch, api)
        data = g.get_album_tracks_from_url(URL)
        assert data["album"] == {
            "id": 876733,
            "name": "M.A.N",
            "artist": "Josman",
            "release_date": "2022-03-18",
            "url": URL,
            "cover_art_url": "cover",
        }
        assert [(t["track_number"], t["title"]) for t in data["tracks"]] == [
            (1, "Intro"),
            (2, "POP"),
        ]
        # 2 recherches, 2 détails (10 et 11 sont d'un autre artiste : jamais demandés), album, tracks
        assert [u for u, _ in api.appels] == [
            "/search",
            "/search",
            "/songs/12",
            "/songs/13",
            "/albums/876733",
            "/albums/876733/tracks",
        ]
        # `indeterminate` : le faux `requests_get` court-circuite le capteur de
        # transport (en réel : 9 tentatives, verdict OK — mesuré). Un trou de
        # capteur se signale lui-même, on ne le maquille pas.
        assert [v.issue for v in source_usage.flush()] == [IssueKind.INDETERMINATE]

    def test_aucun_hit_ne_mene_a_l_album_est_absent(self, monkeypatch):
        api = _Api(
            recherches={"Josman": [_hit(1, "Josman")]},
            songs={1: _song("https://genius.com/albums/Josman/Autre")},
        )
        g = _brancher(monkeypatch, api)
        assert g.get_album_tracks_from_url(URL) is None
        assert (
            api.appels.count(("/songs/1", None)) == 1
        )  # un même morceau n'est détaillé qu'une fois
        assert [v.issue for v in source_usage.flush()] == [IssueKind.ABSENT]

    def test_plafond_de_details_par_recherche(self, monkeypatch):
        hits = [_hit(i, "Josman") for i in range(1, 15)]
        api = _Api(
            recherches={"Josman": hits},
            songs={i: _song("https://genius.com/albums/Josman/Autre") for i in range(1, 15)},
        )
        g = _brancher(monkeypatch, api)
        assert g.get_album_tracks_from_url(URL) is None
        assert (
            sum(1 for u, _ in api.appels if u.startswith("/songs/")) == GeniusAPI._ALBUM_MAX_DETAILS
        )

    def test_recherche_en_panne_rend_none(self, monkeypatch):
        api = _Api(
            recherches={"Josman M a n black roses lost feelings": requests.ConnectionError("x")},
            songs={},
        )
        g = _brancher(monkeypatch, api)
        assert g.get_album_tracks_from_url(URL) is None

    def test_detail_en_echec_est_saute(self, monkeypatch):
        api = _Api(
            recherches={
                "Josman M a n black roses lost feelings": [_hit(1, "Josman"), _hit(2, "Josman")]
            },
            songs={1: requests.HTTPError("500"), 2: _song(URL)},
        )
        g = _brancher(monkeypatch, api)
        assert g.get_album_tracks_from_url(URL)["album"]["id"] == 876733

    def test_tracklist_en_panne_rend_none(self, monkeypatch):
        api = _Api(
            recherches={"Josman M a n black roses lost feelings": [_hit(1, "Josman")]},
            songs={1: _song(URL)},
            tracks=requests.ConnectionError("x"),
        )
        g = _brancher(monkeypatch, api)
        assert g.get_album_tracks_from_url(URL) is None

    def test_url_invalide(self, monkeypatch):
        g = _brancher(monkeypatch, _Api({}, {}))
        assert g.get_album_tracks_from_url("https://genius.com/Josman-intro-lyrics") is None
