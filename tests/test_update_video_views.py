"""Chantier « Media » : update_video_views (batch méta → kind + vues) — offline.

Depuis e20, la passe mesure TOUTES les vidéos d'un morceau (le clip ET l'audio
du canal « - Topic »), pas seulement celle du lien `youtube_url`.
"""

import src.api.ytmusic_api as ytm_mod
from src.models import Artist, Track, TrackVideo
from src.utils.update_video_views import update_video_views, videos_a_mesurer


class _FakeYTM:
    """YTMusicAPI factice : renvoie une méta figée par classe."""

    META: dict = {}

    def __init__(self):
        self.demandes = []

    def fetch_video_meta_batch(self, video_ids):
        self.demandes.append(list(video_ids))
        return {vid: self.META[vid] for vid in video_ids if vid in self.META}


class _FakeDM:
    def __init__(self):
        self.calls = []
        self.video_writes = []

    def update_track_video_views(self, track_id, views, kind):
        self.calls.append((track_id, views, kind))
        return True

    def record_track_videos(self, track_id, videos):
        self.video_writes.append((track_id, [(v.video_id, v.kind, v.views) for v in videos]))
        return len(videos)


def _artist():
    return Artist(id=1, name="Jul")


def _track(tid, url, videos=None):
    a = _artist()
    track = Track(id=tid, title=f"T{tid}", artist=a, youtube_url=url)
    track.videos = list(videos or [])
    return track


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


# ── e20 : le morceau a DEUX vidéos ────────────────────────────────────────────


class TestVideosAMesurer:
    """L'ordre porte une décision : la première alimente `tracks.youtube_video_*`."""

    def test_le_lien_youtube_url_passe_en_tete(self):
        track = _track(
            1,
            "https://youtu.be/dQw4w9WgXcQ",
            videos=[TrackVideo(video_id="aaaaaaaaaaa"), TrackVideo(video_id="dQw4w9WgXcQ")],
        )
        assert videos_a_mesurer(track) == ["dQw4w9WgXcQ", "aaaaaaaaaaa"]

    def test_sans_lien_les_videos_connues_suffisent(self):
        track = _track(1, None, videos=[TrackVideo(video_id="aaaaaaaaaaa")])
        assert videos_a_mesurer(track) == ["aaaaaaaaaaa"]

    def test_aucune_video(self):
        assert videos_a_mesurer(_track(1, None)) == []


def test_les_deux_videos_dun_morceau_sont_mesurees(monkeypatch):
    """Le cas « Magot » / « Déluge » : le clip et l'audio du canal Topic. Avant
    e20 seule celle du lien était mesurée, les vues de l'autre étaient perdues."""
    _patch_ytm(
        monkeypatch,
        {
            "dQw4w9WgXcQ": {"views": 1000, "title": "Magot (Clip Officiel)", "channel": "Label"},
            "aaaaaaaaaaa": {"views": 400, "title": "Magot", "channel": "Artiste - Topic"},
        },
    )
    dm = _FakeDM()
    track = _track(1, "https://youtu.be/dQw4w9WgXcQ", videos=[TrackVideo(video_id="aaaaaaaaaaa")])

    report = update_video_views(_artist(), [track], dm)

    assert report["updated"] == 1  # un MORCEAU
    assert report["videos"] == 2  # deux VIDÉOS
    assert report["by_kind"] == {"clip": 1, "audio": 1}
    assert dm.video_writes == [(1, [("dQw4w9WgXcQ", "clip", 1000), ("aaaaaaaaaaa", "audio", 400)])]
    # Les colonnes du morceau décrivent la vidéo PRINCIPALE, celle du lien.
    assert dm.calls == [(1, 1000, "clip")]


def test_la_mutation_memoire_ne_perd_pas_url_ni_provenance(monkeypatch):
    """La passe des vues ne connaît ni l'URL ni la provenance : remplacer les
    objets en mémoire les effacerait jusqu'au prochain rechargement."""
    _patch_ytm(
        monkeypatch,
        {"aaaaaaaaaaa": {"views": 400, "title": "T", "channel": "Artiste - Topic"}},
    )
    dm = _FakeDM()
    connue = TrackVideo(video_id="aaaaaaaaaaa", url="https://y/aaa", source="genius_media")
    track = _track(1, None, videos=[connue])

    update_video_views(_artist(), [track], dm)

    (memoire,) = track.videos
    assert memoire is connue
    assert (memoire.url, memoire.source) == ("https://y/aaa", "genius_media")
    assert (memoire.kind, memoire.views) == ("audio", 400)


def test_limitation_aux_morceaux_coches(monkeypatch):
    """Le filtre porte sur les ÉCRITURES **et** sur le batch YouTube : demander
    des vues qu'on n'écrira pas coûterait du quota pour rien."""
    _patch_ytm(
        monkeypatch,
        {
            "dQw4w9WgXcQ": {"views": 1, "title": "A", "channel": "C"},
            "aaaaaaaaaaa": {"views": 2, "title": "B", "channel": "C"},
        },
    )
    dm = _FakeDM()
    api = _FakeYTM()
    coche = _track(1, "https://youtu.be/dQw4w9WgXcQ")
    ignore = _track(2, "https://youtu.be/aaaaaaaaaaa")

    report = update_video_views(_artist(), [coche, ignore], dm, api=api, track_ids={1})

    assert report["updated"] == 1
    assert [t for t, _, _ in dm.calls] == [1]
    assert api.demandes == [["dQw4w9WgXcQ"]]
