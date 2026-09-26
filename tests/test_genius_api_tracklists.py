"""`GeniusAPI.album_du_morceau` / `tracklist_album` / `search_songs` sur un faux
transport (2026-09-24). Les tests des services passent par un faux client : la
pagination par `next_page` et le quota (429) n'étaient vérifiés nulle part."""

import pytest
import requests

from src.api.genius_api import GeniusAPI, QuotaGeniusAtteint
from src.observability import source_usage


class _Reponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code, self.text = payload, status, "quota"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._payload


def _genius(monkeypatch, repondre):
    appels = []

    def requests_get(source, url, params=None, headers=None, timeout=None):
        appels.append((url.rsplit("api.genius.com", 1)[-1], dict(params or {})))
        return repondre(url, params or {})

    monkeypatch.setattr(source_usage, "requests_get", requests_get)
    monkeypatch.setattr("src.api.genius_api.GENIUS_API_KEY", "cle")
    source_usage.reset()
    return GeniusAPI.__new__(GeniusAPI), appels


class TestAlbumDuMorceau:
    def test_album_et_ses_artistes(self, monkeypatch):
        song = {
            "album": {
                "id": 5,
                "name": "Bitume Caviar",
                "primary_artists": [{"id": 1}, {"id": 2}],
                "artist": {"id": 3},
                "release_date_for_display": "2021",
            }
        }
        g, _ = _genius(monkeypatch, lambda u, p: _Reponse({"response": {"song": song}}))
        assert g.album_du_morceau(9) == {
            "id": 5,
            "name": "Bitume Caviar",
            "primary_artist_ids": {1, 2, 3},
            "release_date_for_display": "2021",
        }

    def test_single_sans_album(self, monkeypatch):
        g, _ = _genius(monkeypatch, lambda u, p: _Reponse({"response": {"song": {"album": None}}}))
        assert g.album_du_morceau(9) == {}

    def test_illisible(self, monkeypatch):
        g, _ = _genius(monkeypatch, lambda u, p: _Reponse({}, status=500))
        assert g.album_du_morceau(9) is None

    def test_quota(self, monkeypatch):
        g, _ = _genius(monkeypatch, lambda u, p: _Reponse({}, status=429))
        with pytest.raises(QuotaGeniusAtteint):
            g.album_du_morceau(9)


class TestTracklistAlbum:
    def test_suit_next_page_meme_apres_une_page_courte(self, monkeypatch):
        """Genius sert des pages COURTES en plein milieu : seul `next_page` fait foi."""
        pages = {
            1: {"tracks": [{"number": 1}, {"number": 2}], "next_page": 2},
            2: {"tracks": [{"number": 3}], "next_page": 3},  # courte, mais pas la fin
            3: {"tracks": [{"number": 4}], "next_page": None},
        }
        g, appels = _genius(monkeypatch, lambda u, p: _Reponse({"response": pages[p["page"]]}))
        assert [t["number"] for t in g.tracklist_album(7)] == [1, 2, 3, 4]
        assert [p["page"] for _, p in appels] == [1, 2, 3]

    def test_page_vide_arrete(self, monkeypatch):
        """`next_page` reste parfois renseigné après la fin : une page vide arrête."""
        pages = {1: {"tracks": [{"number": 1}], "next_page": 2}, 2: {"tracks": [], "next_page": 3}}
        g, appels = _genius(monkeypatch, lambda u, p: _Reponse({"response": pages[p["page"]]}))
        assert len(g.tracklist_album(7)) == 1
        assert len(appels) == 2

    def test_echec_en_route_ne_rend_pas_une_tracklist_partielle(self, monkeypatch):
        def repondre(u, p):
            if p["page"] == 2:
                return _Reponse({}, status=502)
            return _Reponse({"response": {"tracks": [{"number": 1}], "next_page": 2}})

        g, _ = _genius(monkeypatch, repondre)
        assert g.tracklist_album(7) is None

    def test_quota(self, monkeypatch):
        g, _ = _genius(monkeypatch, lambda u, p: _Reponse({}, status=429))
        with pytest.raises(QuotaGeniusAtteint):
            g.tracklist_album(7)


class TestSearchSongs:
    def test_hits_bruts(self, monkeypatch):
        hits = [
            {"result": {"id": 1, "title": "A", "url": "u", "primary_artist": {"name": "X"}}},
            {"result": {"id": None, "title": "sans id"}},
        ]
        g, _ = _genius(monkeypatch, lambda u, p: _Reponse({"response": {"hits": hits}}))
        assert g.search_songs("X A") == [
            {"id": 1, "title": "A", "url": "u", "primary_artist": {"name": "X"}}
        ]

    def test_quota_leve_au_lieu_de_rendre_vide(self, monkeypatch):
        """Rendu vide, un 429 se lisait « aucune page Genius » (2026-09-24)."""
        g, _ = _genius(monkeypatch, lambda u, p: _Reponse({}, status=429))
        with pytest.raises(QuotaGeniusAtteint):
            g.search_songs("X A")

    def test_erreur_reseau_rend_vide(self, monkeypatch):
        g, _ = _genius(monkeypatch, lambda u, p: _Reponse({}, status=500))
        assert g.search_songs("X A") == []


def test_accrocher_genius_s_arrete_au_quota():
    from types import SimpleNamespace

    from src.services import ecarts_deezer as ed

    class _Api:
        appels = 0

        def search_songs(self, q):
            _Api.appels += 1
            raise QuotaGeniusAtteint("429")

    ecarts = [
        SimpleNamespace(
            nature="version", piste=SimpleNamespace(title_short=t), titre=t, genius=None
        )
        for t in ("A", "B", "C")
    ]
    ed.accrocher_genius(ecarts, SimpleNamespace(name="X"), _Api())
    assert _Api.appels == 1
    assert all(e.genius is None for e in ecarts)
