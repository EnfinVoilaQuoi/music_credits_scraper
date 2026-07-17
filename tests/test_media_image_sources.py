"""Chantier « Media » : extraction des URLs d'images (Deezer + Genius) — offline.

Aucun réseau : Deezer via `_make_request` monkeypatché / dict figé,
Genius via un faux client `self.genius` (instance construite sans __init__).
"""

from src.api.deezer_api import DeezerAPI
from src.api.genius_api import GeniusAPI


# ── Deezer ───────────────────────────────────────────────────────────────────
def test_extract_enrichment_data_expose_covers_xl_et_ids():
    track_data = {
        "id": 111,
        "isrc": "FRX9820001",
        "duration": 195,
        "album": {"id": 42, "cover_medium": "cov_med.jpg", "cover_xl": "cov_xl.jpg"},
        "artist": {"id": 7, "picture_medium": "pic_med.jpg", "picture_xl": "pic_xl.jpg"},
    }
    data = DeezerAPI().extract_enrichment_data(track_data)
    assert data["deezer_album_id"] == 42
    assert data["deezer_artist_id"] == 7
    assert data["deezer_cover_xl"] == "cov_xl.jpg"
    assert data["deezer_picture_xl"] == "pic_xl.jpg"
    # Compat historique : deezer_picture (medium album) intact
    assert data["deezer_picture"] == "cov_med.jpg"


def test_extract_enrichment_data_sans_album_ni_artiste():
    data = DeezerAPI().extract_enrichment_data({"id": 1})
    assert data["deezer_cover_xl"] is None
    assert data["deezer_picture_xl"] is None
    assert data["deezer_album_id"] is None
    assert data["deezer_picture"] is None


def test_search_artist_renvoie_premier_resultat(monkeypatch):
    deezer = DeezerAPI()
    monkeypatch.setattr(
        deezer,
        "_make_request",
        lambda *a, **k: {"data": [{"id": 7, "name": "Jul", "picture_xl": "jul_xl.jpg"}]},
    )
    result = deezer.search_artist("Jul")
    assert result["id"] == 7
    assert result["picture_xl"] == "jul_xl.jpg"


def test_search_artist_aucun_resultat(monkeypatch):
    deezer = DeezerAPI()
    monkeypatch.setattr(deezer, "_make_request", lambda *a, **k: {"data": []})
    assert deezer.search_artist("Inconnu XYZ") is None


def test_get_album_delegue_a_make_request(monkeypatch):
    deezer = DeezerAPI()
    seen = {}

    def fake(endpoint, params=None):
        seen["endpoint"] = endpoint
        return {"id": 42, "cover_xl": "album_xl.jpg"}

    monkeypatch.setattr(deezer, "_make_request", fake)
    result = deezer.get_album(42)
    assert seen["endpoint"] == "album/42"
    assert result["cover_xl"] == "album_xl.jpg"


# ── Genius ───────────────────────────────────────────────────────────────────
def test_extract_song_art_morceau_et_album():
    song = {
        "song_art_image_url": "song_art.jpg",
        "album": {"cover_art_url": "album_cover.jpg"},
    }
    art, cover = GeniusAPI._extract_song_art(song)
    assert art == "song_art.jpg"
    assert cover == "album_cover.jpg"


def test_extract_song_art_fallback_thumbnail_et_sans_album():
    song = {"song_art_image_thumbnail_url": "thumb.jpg", "album": None}
    art, cover = GeniusAPI._extract_song_art(song)
    assert art == "thumb.jpg"
    assert cover is None


def test_extract_song_art_entree_invalide():
    assert GeniusAPI._extract_song_art(None) == (None, None)


class _FakeGeniusClient:
    def __init__(self, image_url=None, raise_exc=None):
        self._image_url = image_url
        self._raise_exc = raise_exc

    def artist(self, genius_id):
        if self._raise_exc:
            raise self._raise_exc
        return {"artist": {"image_url": self._image_url}}


def _genius_with(client) -> GeniusAPI:
    """Instance GeniusAPI sans __init__ (évite la clé API + le réseau)."""
    api = object.__new__(GeniusAPI)
    api.genius = client
    return api


def test_get_artist_image_ok():
    api = _genius_with(_FakeGeniusClient(image_url="https://images.genius.com/jul.jpg"))
    assert api.get_artist_image(123) == "https://images.genius.com/jul.jpg"


def test_get_artist_image_avatar_par_defaut_ignore():
    api = _genius_with(_FakeGeniusClient(image_url="https://x/default_avatar_300.png"))
    assert api.get_artist_image(123) is None


def test_get_artist_image_sans_id():
    api = _genius_with(_FakeGeniusClient(image_url="x.jpg"))
    assert api.get_artist_image(None) is None


def test_get_artist_image_erreur_client():
    api = _genius_with(_FakeGeniusClient(raise_exc=RuntimeError("boom")))
    assert api.get_artist_image(123) is None
