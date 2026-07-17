"""Chantier « Media » : tests de media_enricher.apply_images — sans réseau.

download_image est remplacé par un faux qui ÉCRIT réellement un fichier (pour
que l'idempotence par existence de fichier soit exercée), les clients Deezer/
Genius sont des fakes, et time.sleep est neutralisé.
"""

from pathlib import Path

import pytest

import src.utils.image_downloader as idl
import src.utils.media_enricher as me
from src.models import Artist, Track


# ── Fakes ────────────────────────────────────────────────────────────────────
class FakeDeezer:
    def __init__(self, artist_pic="http://cdn/artist.jpg", track_cover="http://cdn/cover.jpg"):
        self.artist_pic = artist_pic
        self.track_cover = track_cover
        self.search_artist_calls = []
        self.search_track_calls = []

    def search_artist(self, name):
        self.search_artist_calls.append(name)
        return {"id": 1, "picture_xl": self.artist_pic} if self.artist_pic else None

    def search_track(self, artist, title, strict=False):
        self.search_track_calls.append((artist, title))
        return {"album": {"cover_xl": self.track_cover}} if self.track_cover else None

    def extract_enrichment_data(self, track_data):
        album = track_data.get("album") or {}
        return {"deezer_cover_xl": album.get("cover_xl"), "deezer_picture": None}


class FakeGenius:
    def __init__(self, artist_image="http://genius/artist.jpg"):
        self.artist_image = artist_image
        self.calls = []

    def get_artist_image(self, genius_id):
        self.calls.append(genius_id)
        return self.artist_image


def _fake_download(url, dest, *, timeout=15):
    """Écrit un fichier .jpg réel à `dest` et le renvoie ; None si url falsy."""
    if not url:
        return None
    final = Path(dest).with_suffix(".jpg")
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"img")
    return final


@pytest.fixture
def media_env(tmp_path, monkeypatch):
    """Redirige IMAGES_DIR + sous-dossiers vers tmp, neutralise sleep + download."""
    images = tmp_path / "images"
    monkeypatch.setattr(me, "IMAGES_DIR", images)
    monkeypatch.setattr(idl, "ARTIST_IMAGES_DIR", images / "artistes")
    monkeypatch.setattr(idl, "COVER_IMAGES_DIR", images / "covers")
    monkeypatch.setattr(idl, "VIGNETTE_IMAGES_DIR", images / "vignettes")
    monkeypatch.setattr(me, "download_image", _fake_download)
    monkeypatch.setattr(me.time, "sleep", lambda *a, **k: None)
    return images


def _artist(**kw):
    return Artist(id=1, name=kw.pop("name", "Jul"), **kw)


def _track(title, artist, **kw):
    t = Track(id=kw.pop("id", None), title=title, artist=artist)
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _apply(artist, tracks, deezer=None, genius=None, **kw):
    return me.apply_images(artist, tracks, deezer=deezer, genius=genius, **kw)


# ── 1. Photo de l'artiste ────────────────────────────────────────────────────
def test_artist_photo_telechargee(media_env):
    artist = _artist()
    report = _apply(artist, [], deezer=FakeDeezer())
    assert artist.image_path == "artistes/Jul.jpg"
    assert (media_env / "artistes" / "Jul.jpg").exists()
    assert report.downloaded["artist"] == 1


def test_artist_photo_idempotente(media_env):
    artist = _artist()
    _apply(artist, [], deezer=FakeDeezer())
    r2 = _apply(artist, [], deezer=FakeDeezer())
    assert r2.skipped["artist"] == 1
    assert r2.downloaded["artist"] == 0


def test_artist_photo_force_retelecharge(media_env):
    artist = _artist()
    _apply(artist, [], deezer=FakeDeezer())
    r2 = _apply(artist, [], deezer=FakeDeezer(), force=True)
    assert r2.downloaded["artist"] == 1
    assert r2.skipped["artist"] == 0


def test_artist_photo_fallback_genius(media_env):
    artist = _artist(genius_id=999)
    deezer = FakeDeezer(artist_pic=None)  # Deezer ne trouve pas de photo
    genius = FakeGenius(artist_image="http://genius/jul.jpg")
    report = _apply(artist, [], deezer=deezer, genius=genius)
    assert genius.calls == [999]
    assert artist.image_path == "artistes/Jul.jpg"
    assert report.downloaded["artist"] == 1


def test_artist_photo_echec_si_aucune_source(media_env):
    artist = _artist(genius_id=None)
    report = _apply(artist, [], deezer=FakeDeezer(artist_pic=None), genius=None)
    assert artist.image_path is None
    assert report.failed["artist"] == 1


# ── 2. Photos des feats ──────────────────────────────────────────────────────
def test_feat_photos_dedupliquees(media_env):
    artist = _artist()
    tracks = [
        _track("A", artist, featured_artists="Alpha, Beta"),
        _track("B", artist, featured_artists="Beta, Gamma"),
    ]
    report = _apply(artist, tracks, deezer=FakeDeezer())
    # Alpha, Beta, Gamma → 3 fichiers (Beta dédupliqué)
    assert report.downloaded["feat"] == 3
    for name in ("Alpha", "Beta", "Gamma"):
        assert (media_env / "artistes" / f"{name}.jpg").exists()


# ── 3. Covers d'albums (groupées) ────────────────────────────────────────────
def test_cover_album_posee_sur_tous_les_morceaux(media_env):
    artist = _artist()
    tracks = [
        _track("T1", artist, album="Feu"),
        _track("T2", artist, album="Feu"),
    ]
    report = _apply(artist, tracks, deezer=FakeDeezer())
    assert report.downloaded["cover"] == 1  # UN seul download pour l'album
    assert tracks[0].cover_path == "covers/Jul - Feu.jpg"
    assert tracks[1].cover_path == "covers/Jul - Feu.jpg"


def test_cover_album_fallback_genius_transitoire(media_env):
    artist = _artist()
    t = _track("T1", artist, album="Feu")
    t.album_cover_url = "http://genius/album.jpg"  # transitoire posé par genius_api
    deezer = FakeDeezer(track_cover=None)  # Deezer ne trouve pas
    report = _apply(artist, [t], deezer=deezer)
    assert report.downloaded["cover"] == 1
    assert t.cover_path == "covers/Jul - Feu.jpg"


# ── 4. Covers de singles ─────────────────────────────────────────────────────
def test_cover_single_prefere_artwork_url(media_env):
    artist = _artist()
    t = _track("Solo", artist, artwork_url="http://genius/solo.jpg")
    deezer = FakeDeezer()
    report = _apply(artist, [t], deezer=deezer)
    assert t.cover_path == "covers/Jul - Solo.jpg"
    assert report.downloaded["cover"] == 1
    # artwork_url suffisant → pas de recherche Deezer pour ce single
    assert ("Jul", "Solo") not in deezer.search_track_calls


# ── 5. Covers des samples ────────────────────────────────────────────────────
def test_sample_cover_ecrite_dans_la_relation(media_env):
    artist = _artist()
    t = _track("Morceau", artist)
    t.relationships = [
        {"type": "samples", "artist": "James Brown", "title": "Funky Drummer", "url": "u"},
        {"type": "sampled_in", "artist": "X", "title": "Y"},  # aval : ignoré
    ]
    report = _apply(artist, [t], deezer=FakeDeezer())
    assert report.downloaded["sample"] == 1
    assert t.relationships[0]["cover_path"] == "covers/James Brown - Funky Drummer.jpg"
    assert "cover_path" not in t.relationships[1]


# ── 6. Vignettes YouTube ─────────────────────────────────────────────────────
def test_vignette_pour_show_exotic(media_env):
    artist = _artist()
    t = _track("Grünt #52", artist, youtube_url="https://youtu.be/dQw4w9WgXcQ")
    report = _apply(artist, [t], deezer=FakeDeezer())
    assert t.yt_thumbnail_path == "vignettes/dQw4w9WgXcQ.jpg"
    assert report.downloaded["vignette"] == 1


def test_pas_de_vignette_pour_morceau_album(media_env):
    artist = _artist()
    t = _track("Titre normal", artist, album="Album", youtube_url="https://youtu.be/dQw4w9WgXcQ")
    report = _apply(artist, [t], deezer=FakeDeezer())
    assert t.yt_thumbnail_path is None
    assert report.downloaded["vignette"] == 0


def test_vignette_maxres_404_bascule_hqdefault(media_env, monkeypatch):
    # maxresdefault échoue (None) → hqdefault réussit.
    def only_hq(url, dest, *, timeout=15):
        if "maxresdefault" in url:
            return None
        return _fake_download(url, dest)

    monkeypatch.setattr(me, "download_image", only_hq)
    artist = _artist()
    t = _track("Freestyle", artist, youtube_url="https://youtu.be/dQw4w9WgXcQ")
    report = _apply(artist, [t], deezer=FakeDeezer())
    assert report.downloaded["vignette"] == 1
    assert t.yt_thumbnail_path == "vignettes/dQw4w9WgXcQ.jpg"


# ── should_stop ──────────────────────────────────────────────────────────────
def test_should_stop_interrompt_tout(media_env):
    artist = _artist()
    tracks = [_track("A", artist, featured_artists="Alpha")]
    report = _apply(artist, tracks, deezer=FakeDeezer(), should_stop=lambda: True)
    assert artist.image_path is None
    assert report.total_downloaded() == 0


def test_summary_ne_leve_pas(media_env):
    report = _apply(_artist(), [], deezer=FakeDeezer())
    assert isinstance(report.summary(), str)
