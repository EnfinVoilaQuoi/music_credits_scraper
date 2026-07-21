"""Chantier « Media » : update_video_views (batch méta → kind + vues) — offline."""

import src.api.ytmusic_api as ytm_mod
from src.models import Artist, Track
from src.utils.update_video_views import update_video_views


class _FakeYTM:
    """YTMusicAPI factice : renvoie une méta figée par classe."""

    META: dict = {}

    def fetch_video_meta_batch(self, video_ids):
        return {vid: self.META[vid] for vid in video_ids if vid in self.META}


class _FakeDM:
    def __init__(self):
        self.calls = []

    def update_track_video_views(self, track_id, views, kind):
        self.calls.append((track_id, views, kind))
        return True


def _artist():
    return Artist(id=1, name="Jul")


def _track(tid, url):
    a = _artist()
    return Track(id=tid, title=f"T{tid}", artist=a, youtube_url=url)


def _patch_ytm(monkeypatch, meta):
    _FakeYTM.META = meta
    monkeypatch.setattr(ytm_mod, "YTMusicAPI", _FakeYTM)


def test_update_video_views_ecrit_kind_et_vues(monkeypatch):
    _patch_ytm(
        monkeypatch,
        {"dQw4w9WgXcQ": {"views": 1000, "title": "T - Clip Officiel", "channel": "Label"}},
    )
    dm = _FakeDM()
    track = _track(1, "https://youtu.be/dQw4w9WgXcQ")
    report = update_video_views(_artist(), [track], dm)

    assert report["updated"] == 1
    assert report["by_kind"] == {"clip": 1}
    assert track.media.youtube_video_kind == "clip"
    assert track.media.youtube_video_views == 1000
    assert dm.calls == [(1, 1000, "clip")]


def test_update_video_views_sans_lien(monkeypatch):
    _patch_ytm(monkeypatch, {})
    dm = _FakeDM()
    track = _track(1, None)
    report = update_video_views(_artist(), [track], dm)
    assert report["no_video_id"] == 1
    assert report["updated"] == 0
    assert dm.calls == []


def test_update_video_views_video_sans_meta(monkeypatch):
    _patch_ytm(monkeypatch, {})  # aucune méta renvoyée
    dm = _FakeDM()
    track = _track(1, "https://youtu.be/dQw4w9WgXcQ")
    report = update_video_views(_artist(), [track], dm)
    assert report["no_meta"] == 1
    assert report["updated"] == 0


def test_update_video_views_show(monkeypatch):
    _patch_ytm(
        monkeypatch,
        {"dQw4w9WgXcQ": {"views": 5, "title": "A COLORS SHOW", "channel": "COLORS"}},
    )
    dm = _FakeDM()
    track = _track(1, "https://youtu.be/dQw4w9WgXcQ")
    report = update_video_views(_artist(), [track], dm)
    assert track.media.youtube_video_kind == "show"
    assert report["by_kind"] == {"show": 1}
