"""Tests du resolver synchro/paroles (src/utils/synced_lyrics_resolver) — sans réseau.

Verrouille le comportement extrait VERBATIM du worker GUI (scraping.py, F5-step1) :
sources LRCLIB/YTM/Musixmatch, cross-check/départage via compare_synced, émission
des observations PAR SOURCE, fallback texte YTM.
"""

from src.models.artist import Artist
from src.models.track import Track
from src.utils.synced_lyrics_resolver import resolve_track_synced_lyrics

# LRC valides (format [mm:ss.cc]) — ≥3 lignes pour le cross-check à conf 2.
_LRC = "[00:10.00] alpha\n[00:20.00] beta\n[00:30.00] gamma\n[00:40.00] delta"
_LRC_OTHER = "[00:11.00] one\n[00:22.00] two\n[00:33.00] three\n[00:44.00] four"


class _FakeLRCLIB:
    def __init__(self, lrc=None):
        self._lrc = lrc
        self.last_duration = "unset"

    def get_synced(self, title, artist, album_name=None, duration=None):
        self.last_duration = duration
        return {"lyrics_synced": self._lrc} if self._lrc else None


class _FakeYTM:
    def __init__(self, lrc=None, text=None, duration=None, source=None):
        self._res = {
            "lyrics_synced": lrc,
            "lyrics": text,
            "duration": duration,
            "source": source,
        }

    def get_lyrics(self, artist, title):
        return dict(self._res)


class _FakeMxm:
    def __init__(self, lrc=None, note="mxm"):
        self._lrc = lrc
        self._note = note

    def get_synced_as_source3(self, title, artist, duration=None):
        if not self._lrc:
            return None
        return {"lrc": self._lrc, "source": "Musixmatch", "confidence": 1, "note": self._note}


def _track(title="Solo", duration=None, has_lyrics=False, lyrics=None, album="Album"):
    t = Track(title=title, artist=Artist(name="X"))
    t.duration = duration
    t.lyrics.present = has_lyrics
    t.lyrics.text = lyrics
    t.album = album
    return t


def test_lrclib_seul_source_unique():
    out = resolve_track_synced_lyrics(
        _track(),
        "X",
        lrclib=_FakeLRCLIB(_LRC),
        ytm=None,
        mxm=None,
        need_sync=True,
        need_text=False,
        sync_ytm=True,
    )
    assert out.lyrics_synced == _LRC
    assert out.lyrics_synced_source == "LRCLIB"
    assert out.lyrics_synced_confidence == 1
    assert out.synced_kind == "lrclib"
    assert out.synced_is_cross is False
    sources = [o.source for o in out.observations]
    assert sources == ["lrclib"]


def test_cross_check_concordant_conf2():
    out = resolve_track_synced_lyrics(
        _track(),
        "X",
        lrclib=_FakeLRCLIB(_LRC),
        ytm=_FakeYTM(lrc=_LRC),
        mxm=None,
        need_sync=True,
        need_text=False,
        sync_ytm=True,
    )
    assert out.lyrics_synced == _LRC
    assert out.lyrics_synced_source == "LRCLIB"
    assert out.lyrics_synced_confidence == 2
    assert out.synced_kind == "lrclib"
    assert out.synced_is_cross is True
    assert sorted(o.source for o in out.observations) == ["lrclib", "ytmusic"]


def test_ytm_seul_source_unique():
    out = resolve_track_synced_lyrics(
        _track(),
        "X",
        lrclib=None,
        ytm=_FakeYTM(lrc=_LRC),
        mxm=None,
        need_sync=True,
        need_text=False,
        sync_ytm=True,
    )
    assert out.lyrics_synced == _LRC
    assert out.lyrics_synced_source == "YouTube Music"
    assert out.synced_kind == "ytm"
    assert [o.source for o in out.observations] == ["ytmusic"]


def test_sync_ytm_false_ignore_le_lrc_ytm():
    # Le client YTM peut exister pour le seul fallback texte : sync_ytm=False
    # → son LRC N'EST PAS une source (aucune observation ytmusic).
    out = resolve_track_synced_lyrics(
        _track(),
        "X",
        lrclib=None,
        ytm=_FakeYTM(lrc=_LRC),
        mxm=None,
        need_sync=True,
        need_text=False,
        sync_ytm=False,
    )
    assert out.lyrics_synced is None
    assert out.observations == []


def test_musixmatch_dernier_recours():
    out = resolve_track_synced_lyrics(
        _track(),
        "X",
        lrclib=_FakeLRCLIB(None),
        ytm=None,
        mxm=_FakeMxm(_LRC),
        need_sync=True,
        need_text=False,
        sync_ytm=True,
    )
    assert out.lyrics_synced == _LRC
    assert out.lyrics_synced_source == "Musixmatch"
    assert out.lyrics_synced_confidence == 1
    assert out.synced_kind == "musixmatch"
    assert out.synced_is_cross is False
    assert [o.source for o in out.observations] == ["musixmatch"]


def test_musixmatch_pas_appele_si_verdict_lrclib():
    # LRCLIB a donné un verdict → Musixmatch (source 3) n'est PAS sollicité.
    mxm = _FakeMxm(_LRC_OTHER)
    out = resolve_track_synced_lyrics(
        _track(),
        "X",
        lrclib=_FakeLRCLIB(_LRC),
        ytm=None,
        mxm=mxm,
        need_sync=True,
        need_text=False,
        sync_ytm=True,
    )
    assert out.synced_kind == "lrclib"
    assert [o.source for o in out.observations] == ["lrclib"]


def test_fallback_texte_ytm():
    out = resolve_track_synced_lyrics(
        _track(has_lyrics=False, lyrics=None),
        "X",
        lrclib=None,
        ytm=_FakeYTM(text="des paroles", source="YouTube Music"),
        mxm=None,
        need_sync=False,
        need_text=True,
        sync_ytm=True,
    )
    assert out.text == "des paroles"
    assert out.text_source == "YouTube Music"
    # need_sync=False → aucune résolution synchro
    assert out.lyrics_synced is None
    assert out.observations == []


def test_texte_non_ecrase_si_deja_present():
    out = resolve_track_synced_lyrics(
        _track(has_lyrics=True, lyrics="déjà là"),
        "X",
        lrclib=None,
        ytm=_FakeYTM(text="autre"),
        mxm=None,
        need_sync=False,
        need_text=True,
        sync_ytm=True,
    )
    assert out.text is None  # Genius/existant a priorité


def test_duration_de_secours_ytm_transmise_a_lrclib():
    # track.duration absent → la durée YTM sert de secours au match LRCLIB.
    lrclib = _FakeLRCLIB(_LRC)
    resolve_track_synced_lyrics(
        _track(duration=None),
        "X",
        lrclib=lrclib,
        ytm=_FakeYTM(lrc=None, duration=183),
        mxm=None,
        need_sync=True,
        need_text=False,
        sync_ytm=True,
    )
    assert lrclib.last_duration == 183


def test_need_sync_false_pas_de_synchro():
    out = resolve_track_synced_lyrics(
        _track(),
        "X",
        lrclib=_FakeLRCLIB(_LRC),
        ytm=_FakeYTM(lrc=_LRC),
        mxm=_FakeMxm(_LRC),
        need_sync=False,
        need_text=False,
        sync_ytm=True,
    )
    assert out.lyrics_synced is None
    assert out.observations == []
