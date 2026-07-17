"""Tests du downloader d'images — sans réseau (requests.get monkeypatché)."""

import requests

import src.utils.image_downloader as dl
from src.utils.image_downloader import (
    artist_image_path,
    cover_image_path,
    download_image,
    slugify_filename,
    vignette_image_path,
)


# ── slugify ──────────────────────────────────────────────────────────────────
def test_slugify_retire_caracteres_interdits():
    assert slugify_filename('AC/DC: <Live>?*"') == "ACDC Live"


def test_slugify_ecrase_espaces_multiples():
    assert slugify_filename("Kery   James") == "Kery James"


def test_slugify_tronque_et_reste_deterministe():
    long_name = "a" * 200
    out1 = slugify_filename(long_name)
    out2 = slugify_filename(long_name)
    assert out1 == out2
    assert len(out1) <= 120


def test_slugify_vide_renvoie_placeholder():
    assert slugify_filename("") == "_"
    assert slugify_filename("   ") == "_"


def test_slugify_pas_de_point_ou_espace_final():
    assert slugify_filename("Intro. ") == "Intro"


# ── conventions de nommage ───────────────────────────────────────────────────
def test_artist_image_path():
    p = artist_image_path("Jul")
    assert p.parent.name == "artistes"
    assert p.name == "Jul.jpg"


def test_cover_image_path():
    p = cover_image_path("Nekfeu", "Feu")
    assert p.parent.name == "covers"
    assert p.name == "Nekfeu - Feu.jpg"


def test_vignette_image_path():
    p = vignette_image_path("dQw4w9WgXcQ")
    assert p.parent.name == "vignettes"
    assert p.name == "dQw4w9WgXcQ.jpg"


# ── download_image (monkeypatch requests.get) ────────────────────────────────
class _FakeResponse:
    def __init__(self, *, content=b"", content_type="image/jpeg", raise_exc=None):
        self._content = content
        self.headers = {"Content-Type": content_type}
        self._raise_exc = raise_exc

    def __enter__(self):
        if self._raise_exc:
            raise self._raise_exc
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=8192):
        yield self._content


def _patch_get(monkeypatch, response):
    monkeypatch.setattr(dl.requests, "get", lambda *a, **k: response)


def test_download_image_succes_ecrit_le_fichier(tmp_path, monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(content=b"JPEGDATA"))
    dest = tmp_path / "photo.jpg"
    result = download_image("http://x/img", dest)
    assert result == dest
    assert dest.read_bytes() == b"JPEGDATA"


def test_download_image_extension_deduite_du_content_type(tmp_path, monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(content=b"PNG", content_type="image/png"))
    # dest en .jpg mais le serveur renvoie du png → fichier final en .png
    result = download_image("http://x/img", tmp_path / "photo.jpg")
    assert result == tmp_path / "photo.png"
    assert result.exists()
    assert not (tmp_path / "photo.jpg").exists()


def test_download_image_reponse_non_image_renvoie_none(tmp_path, monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(content=b"<html>", content_type="text/html"))
    dest = tmp_path / "photo.jpg"
    assert download_image("http://x/img", dest) is None
    assert not dest.exists()


def test_download_image_erreur_reseau_renvoie_none(tmp_path, monkeypatch):
    _patch_get(
        monkeypatch,
        _FakeResponse(raise_exc=requests.ConnectionError("boom")),
    )
    dest = tmp_path / "photo.jpg"
    assert download_image("http://x/img", dest) is None
    assert not dest.exists()


def test_download_image_url_vide_renvoie_none(tmp_path):
    assert download_image("", tmp_path / "photo.jpg") is None
    assert download_image(None, tmp_path / "photo.jpg") is None


def test_download_image_pas_de_fichier_part_residuel(tmp_path, monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(content=b"JPEGDATA"))
    dest = tmp_path / "photo.jpg"
    download_image("http://x/img", dest)
    # L'écriture atomique ne doit pas laisser de .part traîner
    assert not (tmp_path / "photo.jpg.part").exists()
