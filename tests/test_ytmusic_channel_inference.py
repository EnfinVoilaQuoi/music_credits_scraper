"""Filet de tests sur `_infer_channel_from_youtube_links` (src/utils/update_ytmusic).

Posé AVANT le durcissement de la désambiguïsation (étape 2 du plan) pour geler le
comportement de la collecte des votes : priorité aux liens déjà en base, recours
live seulement en complément (< 3 liens), filtrage `direct`/confiance ≥ 0.8,
plafonds `max_votes`/`max_attempts`, planchers (< 2 vidéo → pas d'appel API).

Stubs sans extraction réelle : l'import `youtube_integration` est LOCAL à la
fonction, on injecte un module bidon dans `sys.modules` (hermétique — évite
l'init réseau de YTMusic) et on stubbe l'`api`.
"""

import sys
import types
from types import SimpleNamespace

from src.utils.update_ytmusic import _infer_channel_from_youtube_links


def _track(idx, vid=None, album=None):
    """Track factice ; `vid` (11 car.) → youtube_url valide, sinon pas de lien."""
    url = f"https://www.youtube.com/watch?v={vid}" if vid else None
    return SimpleNamespace(id=idx, title=f"Track {idx}", youtube_url=url, album=album)


def _vid(n: int) -> str:
    """Un videoId valide de 11 caractères (regex `_extract_video_id`)."""
    return f"vid{n:08d}"[:11].ljust(11, "0")


class _FakeAPI:
    """Enregistre l'appel à `infer_channel_from_videos`."""

    def __init__(self, winner="UCwinner"):
        self.winner = winner
        self.called_with = None
        self.call_count = 0

    def infer_channel_from_videos(self, video_ids):
        self.call_count += 1
        self.called_with = list(video_ids)
        return self.winner


class _FakeYI:
    """Faux `youtube_integration`. `responses` = {title: dict} ; compte les appels."""

    def __init__(self, responses=None, raise_if_called=False):
        self.responses = responses or {}
        self.raise_if_called = raise_if_called
        self.call_count = 0
        self.titles_seen = []

    def get_youtube_link_for_track(self, artist_name, title, album=None):
        if self.raise_if_called:
            raise AssertionError("recherche live ne devait PAS être appelée")
        self.call_count += 1
        self.titles_seen.append(title)
        return self.responses.get(title, {"type": "search", "confidence": 0.0})


def _install_yi(monkeypatch, fake):
    """Injecte un faux module `src.utils.youtube_integration` (attr `youtube_integration`)."""
    stub = types.ModuleType("src.utils.youtube_integration")
    stub.youtube_integration = fake
    monkeypatch.setitem(sys.modules, "src.utils.youtube_integration", stub)


class _DM:
    def __init__(self, tracks=None, raise_exc=False):
        self._tracks = tracks or []
        self._raise = raise_exc

    def get_artist_tracks(self, artist_id):
        if self._raise:
            raise RuntimeError("boom")
        return self._tracks


_ARTIST = SimpleNamespace(id=1, name="Isha")


# ── 1. ≥ 3 liens en base → vote direct, recherche live jamais touchée ────────


def test_trois_liens_base_vote_sans_recherche_live(monkeypatch):
    yi = _FakeYI(raise_if_called=True)
    _install_yi(monkeypatch, yi)
    api = _FakeAPI()
    tracks = [_track(i, vid=_vid(i)) for i in range(3)]

    result = _infer_channel_from_youtube_links(api, _ARTIST, _DM(tracks))

    assert result == "UCwinner"
    assert api.call_count == 1
    assert len(api.called_with) == 3
    assert yi.call_count == 0


# ── 2. Plafond max_votes (12 liens → 8 ids) ──────────────────────────────────


def test_max_votes_respecte(monkeypatch):
    _install_yi(monkeypatch, _FakeYI(raise_if_called=True))
    api = _FakeAPI()
    tracks = [_track(i, vid=_vid(i)) for i in range(12)]

    _infer_channel_from_youtube_links(api, _ARTIST, _DM(tracks))

    assert len(api.called_with) == 8


# ── 3. Complément live : seuls direct + conf ≥ 0.8 votent ────────────────────


def test_complement_live_filtre_direct_et_confiance(monkeypatch):
    # 0 lien en base → 4 titres sans lien. Live : 2 qualifiants, 2 rejetés.
    responses = {
        "Track 0": {"type": "direct", "confidence": 0.9, "url": f"https://youtu.be/{_vid(90)}"},
        "Track 1": {"type": "direct", "confidence": 0.95, "url": f"https://youtu.be/{_vid(91)}"},
        "Track 2": {"type": "direct", "confidence": 0.5, "url": f"https://youtu.be/{_vid(92)}"},
        "Track 3": {"type": "search", "confidence": 1.0, "url": f"https://youtu.be/{_vid(93)}"},
    }
    _install_yi(monkeypatch, _FakeYI(responses))
    api = _FakeAPI()
    tracks = [_track(i) for i in range(4)]  # aucun lien en base

    _infer_channel_from_youtube_links(api, _ARTIST, _DM(tracks))

    # Seuls les 2 direct/≥0.8 ont voté.
    assert len(api.called_with) == 2


def test_max_attempts_respecte(monkeypatch):
    # 1 lien base + 5 sans lien, tous résolubles en live ; max_attempts=2 borne.
    responses = {
        f"Track {i}": {"type": "direct", "confidence": 0.9, "url": f"https://youtu.be/{_vid(i)}"}
        for i in range(1, 6)
    }
    yi = _FakeYI(responses)
    _install_yi(monkeypatch, yi)
    api = _FakeAPI()
    tracks = [_track(0, vid=_vid(0))] + [_track(i) for i in range(1, 6)]

    _infer_channel_from_youtube_links(api, _ARTIST, _DM(tracks), max_attempts=2)

    assert yi.call_count == 2  # borne max_attempts
    assert len(api.called_with) == 3  # 1 base + 2 live


# ── 4. < 2 vidéos au total → None, aucun appel API ───────────────────────────


def test_moins_de_deux_videos_retourne_none(monkeypatch):
    _install_yi(monkeypatch, _FakeYI())  # live ne résout rien (type=search)
    api = _FakeAPI()
    tracks = [_track(0, vid=_vid(0))]  # un seul lien fiable

    result = _infer_channel_from_youtube_links(api, _ARTIST, _DM(tracks))

    assert result is None
    assert api.call_count == 0


# ── 5. Tracks vides / exception → None sans appel API ────────────────────────


def test_tracks_vides_retourne_none(monkeypatch):
    _install_yi(monkeypatch, _FakeYI(raise_if_called=True))
    api = _FakeAPI()

    result = _infer_channel_from_youtube_links(api, _ARTIST, _DM([]))

    assert result is None
    assert api.call_count == 0


def test_exception_get_tracks_retourne_none(monkeypatch):
    _install_yi(monkeypatch, _FakeYI(raise_if_called=True))
    api = _FakeAPI()

    result = _infer_channel_from_youtube_links(api, _ARTIST, _DM(raise_exc=True))

    assert result is None
    assert api.call_count == 0


# ── 6. URLs invalides ignorées (_extract_video_id → None) ────────────────────


def test_urls_invalides_ignorees(monkeypatch):
    # 2 liens valides + 1 URL invalide ; pas de recours live (youtube_integration
    # None) → seuls les 2 videoId valides votent, l'URL invalide est écartée.
    _install_yi(monkeypatch, None)
    api = _FakeAPI()
    tracks = [
        _track(0, vid=_vid(0)),
        _track(1, vid=_vid(1)),
        SimpleNamespace(
            id=2, title="Track 2", youtube_url="https://example.com/no-video", album=None
        ),
    ]

    _infer_channel_from_youtube_links(api, _ARTIST, _DM(tracks))

    assert len(api.called_with) == 2
