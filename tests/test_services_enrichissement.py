"""Service enrichissement (`src/services/enrichissement.py`).

L'ordre du teardown a été acquis à la dure (EPIPE Playwright à l'arrêt, browsers
survivants) : il est GELÉ ici. Le reste : `spotify_id` toujours présent,
save → record_pending par morceau, arrêt entre deux morceaux dit dans le bilan.
"""

import asyncio

from src.models import Artist, Track
from src.services import enrichissement as enr
from src.services.runtime import Hooks, Runtime


class _Runner:
    def __init__(self, journal):
        self.journal = journal

    async def run(self, fn, *a):
        return fn(*a)


class _Enricher:
    def __init__(self, journal, results=None):
        self.journal = journal
        self.results = results or {"reccobeats": True}
        self.sync_runner = _Runner(journal)
        self.sources_vues = None
        self.reset = False

    def get_available_sources(self):
        return ["reccobeats", "deezer"]

    def reset_bpmfinder_breaker(self):
        self.reset = True

    async def enrich_track_async(
        self, track, *, sources, force_update, artist_tracks, clear_on_failure
    ):
        self.sources_vues = list(sources)
        self.journal.append(("enrich", track.title))
        return dict(self.results)

    async def aclose_async_scrapers(self):
        self.journal.append("aclose_async_scrapers")

    def close(self):
        self.journal.append("close")

    async def aclose_http(self):
        self.journal.append("aclose_http")


class _DM:
    def __init__(self, journal):
        self.journal = journal

    def save_track(self, t):
        self.journal.append(("save", t.title))

    def record_pending(self, t):
        self.journal.append(("pending", t.title))


def _setup(monkeypatch, results=None):
    journal = []
    monkeypatch.setattr(enr, "_stop_playwright_async", _fake_async(journal, "stop_pw_async"))
    monkeypatch.setattr(enr, "_stop_playwright_sync", lambda: journal.append("stop_pw_sync"))
    enricher = _Enricher(journal, results)
    rt = Runtime(
        data_manager=_DM(journal),
        genius_api=None,
        data_enricher=enricher,
        deleted=None,
        disabled=None,
    )
    return rt, enricher, journal


def _fake_async(journal, tag):
    async def f():
        journal.append(tag)

    return f


def _tracks(*titles):
    out = []
    for t in titles:
        tr = Track(title=t, artist=Artist(name="A"))
        tr.id = len(out) + 1
        out.append(tr)
    return out


def _run(rt, tracks, options=None, hooks=None):
    artist = Artist(name="A")
    artist.tracks = tracks
    return asyncio.run(
        enr.run_async(rt, artist, tracks, options or enr.OptionsEnrich(), hooks or Hooks())
    )


class TestTeardown:
    def test_ordre_exact(self, monkeypatch):
        rt, _, journal = _setup(monkeypatch)
        _run(rt, _tracks("x"))
        fin = [e for e in journal if isinstance(e, str)]
        assert fin == [
            "aclose_async_scrapers",
            "stop_pw_async",
            "close",
            "stop_pw_sync",
            "aclose_http",
        ]

    def test_une_fermeture_qui_echoue_ne_bloque_pas_les_suivantes(self, monkeypatch):
        rt, enricher, journal = _setup(monkeypatch)

        async def boom():
            raise RuntimeError("pipe cassé")

        enricher.aclose_async_scrapers = boom
        _run(rt, _tracks("x"))
        assert "aclose_http" in journal and "close" in journal


class TestBatch:
    def test_spotify_id_toujours_dans_les_sources(self, monkeypatch):
        rt, enricher, _ = _setup(monkeypatch)
        _run(rt, _tracks("x"), enr.OptionsEnrich(sources=("reccobeats",)))
        assert enricher.sources_vues == ["reccobeats", "spotify_id"]
        _run(rt, _tracks("x"))  # None → disponibles + spotify_id
        assert enricher.sources_vues == ["reccobeats", "deezer", "spotify_id"]

    def test_save_puis_record_pending_par_morceau(self, monkeypatch):
        rt, enricher, journal = _setup(monkeypatch)
        bilan = _run(rt, _tracks("x", "y"))
        assert enricher.reset
        assert [e for e in journal if isinstance(e, tuple)] == [
            ("enrich", "x"),
            ("save", "x"),
            ("pending", "x"),
            ("enrich", "y"),
            ("save", "y"),
            ("pending", "y"),
        ]
        assert bilan.complete and bilan.traites == 2

    def test_arret_entre_deux_morceaux(self, monkeypatch):
        rt, _, journal = _setup(monkeypatch)
        n = {"v": 0}

        def stop():
            n["v"] += 1
            return n["v"] > 1

        bilan = _run(rt, _tracks("x", "y"), hooks=Hooks(should_stop=stop))
        assert bilan.traites == 1 and not bilan.complete and "1/2" in bilan.motif
        assert "aclose_http" in journal  # teardown même sur arrêt

    def test_nettoyes_comptes(self, monkeypatch):
        rt, _, _ = _setup(monkeypatch, results={"reccobeats": False, "cleaned": True})
        bilan = _run(rt, _tracks("x"))
        assert bilan.nettoyes == 1


class TestResume:
    def test_legende_et_compte(self, monkeypatch):
        rt, _, _ = _setup(monkeypatch, results={"reccobeats": True, "deezer": "not_needed"})
        bilan = _run(rt, _tracks("x"))
        texte = enr.resume(bilan, enr.OptionsEnrich(), desactives=2)
        assert "✅ 1 réussi(s)" in texte and "RC:✓ | DZ:-" in texte
        assert "2 morceaux désactivés ignorés" in texte
