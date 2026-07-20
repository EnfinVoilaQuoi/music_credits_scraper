"""Tests du provider BPM Finder (src/enrichment/providers/bpmfinder) — sans réseau.

Scraper mocké. On évite la recherche YouTube en fournissant un youtube_url au
track (le provider ne cherche un lien que s'il en manque un).
"""

import asyncio

from src.concurrency.serial_worker import SerialWorker
from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.bpmfinder import BpmFinderProvider
from src.models.artist import Artist
from src.models.track import Track


class _FakeScraper:
    def __init__(self, result=None, failure_reason=None):
        self._result = result
        self.last_failure_reason = failure_reason
        self.calls = 0

    def analyze(self, url):
        self.calls += 1
        return self._result


class _FakeAsyncScraper:
    def __init__(self, result=None, failure_reason=None):
        self._result = result
        self.last_failure_reason = failure_reason
        self.calls = 0

    async def analyze_async(self, url):
        self.calls += 1
        return self._result


def _track(bpm=None, yt="https://youtu.be/abc"):
    t = Track(title="Solo", artist=Artist(name="X"))
    t.bpm = bpm
    t.youtube_url = yt
    return t


def test_is_available():
    assert BpmFinderProvider(None).is_available() is False
    assert BpmFinderProvider(_FakeScraper()).is_available() is True


def test_not_needed_si_rien_manquant():
    provider = BpmFinderProvider(_FakeScraper({"bpm": 140}))
    track = _track(bpm=120)
    track.key = 5
    track.mode = 1
    assert provider.enrich(track, EnrichmentContext(force_update=False)) == "not_needed"


def test_bpm_manquant_devient_candidat():
    scraper = _FakeScraper({"bpm": 140})
    provider = BpmFinderProvider(scraper)
    track = _track(bpm=None)  # bpm manquant → analyse
    track.key = 5  # key/mode présents → seul le bpm manque
    track.mode = 1
    ctx = EnrichmentContext()
    assert provider.enrich(track, ctx) is True
    # E7 : BpmFinder alimente le scrutin comme toute source (plus de pose directe)
    assert ("bpmfinder", 140) in ctx.bpm_ballot.candidates


def test_emet_bpm_et_observations_key_mode():
    # FIX persistance E7 : BpmFinder alimente le MOTEUR (scrutin + observations
    # PAR SOURCE) — seul canal persisté depuis le drop des colonnes audio (e12).
    scraper = _FakeScraper({"bpm": 140, "key": 5, "mode": 0})
    provider = BpmFinderProvider(scraper)
    track = _track(bpm=None)
    track.key = None
    track.mode = None
    ctx = EnrichmentContext()
    assert provider.enrich(track, ctx) is True
    assert ("bpmfinder", 140) in ctx.bpm_ballot.candidates
    keys = [o for o in ctx.observations if o.field == "key" and o.source == "bpmfinder"]
    modes = [o for o in ctx.observations if o.field == "mode" and o.source == "bpmfinder"]
    assert keys and keys[0].value == 5
    assert modes and modes[0].value == 0


def test_disjoncteur_sur_timeout_puis_skipped():
    scraper = _FakeScraper(None, failure_reason="timeout")
    provider = BpmFinderProvider(scraper)
    ctx = EnrichmentContext()
    for _ in range(3):
        assert provider.enrich(_track(bpm=None), ctx) is False
    # 3 timeouts consécutifs → disjoncteur ouvert
    assert provider.enrich(_track(bpm=None), ctx) == "skipped"
    assert scraper.calls == 3  # le 4ᵉ appel n'atteint pas le scraper


def test_refus_backend_ne_declenche_pas_le_disjoncteur():
    scraper = _FakeScraper(None, failure_reason="backend")
    provider = BpmFinderProvider(scraper)
    ctx = EnrichmentContext()
    for _ in range(5):
        assert provider.enrich(_track(bpm=None), ctx) is False
    assert scraper.calls == 5  # jamais coupé : le site répondait (4xx/5xx)


def test_reset_breaker_rearme():
    scraper = _FakeScraper(None, failure_reason="timeout")
    provider = BpmFinderProvider(scraper)
    ctx = EnrichmentContext()
    for _ in range(3):
        provider.enrich(_track(bpm=None), ctx)
    assert provider.enrich(_track(bpm=None), ctx) == "skipped"
    provider.reset_breaker()
    assert provider.enrich(_track(bpm=None), ctx) is False  # ré-armé → retente


# ──────────────────────────────────────────────────────────────────────
# enrich_async (F3d) — repli sync par défaut, voie native si factory fournie
# ──────────────────────────────────────────────────────────────────────


def test_enrich_async_repli_sync_sans_factory():
    # Sans async_scraper_factory : enrich_async passe par le PONT SYNC (scraper
    # sync via ctx.sync_runner) — comportement livré tant que l'async n'est pas
    # activé.
    scraper = _FakeScraper({"bpm": 140})
    provider = BpmFinderProvider(scraper)
    track = _track(bpm=None)
    track.key = None
    track.mode = None
    runner = SerialWorker("test-bpmfinder")
    ctx = EnrichmentContext(sync_runner=runner)
    try:
        result = asyncio.run(provider.enrich_async(track, ctx))
    finally:
        runner.shutdown()
    assert result is True
    assert scraper.calls == 1  # le scraper SYNC a été utilisé (pont)
    assert ("bpmfinder", 140) in ctx.bpm_ballot.candidates


def test_enrich_async_natif_avec_factory():
    # Avec async_scraper_factory : enrich_async utilise analyze_async (voie native),
    # jamais le scraper sync.
    async_scraper = _FakeAsyncScraper({"bpm": 140, "key": 5, "mode": 0})
    sync_scraper = _FakeScraper({"bpm": 999})  # ne doit PAS être appelé
    provider = BpmFinderProvider(scraper=sync_scraper, async_scraper_factory=lambda: async_scraper)
    track = _track(bpm=None)
    track.key = None
    track.mode = None
    runner = SerialWorker("test-bpmfinder")
    ctx = EnrichmentContext(sync_runner=runner)
    try:
        result = asyncio.run(provider.enrich_async(track, ctx))
    finally:
        runner.shutdown()
    assert result is True
    assert async_scraper.calls == 1  # voie native
    assert sync_scraper.calls == 0  # pas de pont sync
    assert ("bpmfinder", 140) in ctx.bpm_ballot.candidates
    keys = [o for o in ctx.observations if o.field == "key" and o.source == "bpmfinder"]
    modes = [o for o in ctx.observations if o.field == "mode" and o.source == "bpmfinder"]
    assert keys and keys[0].value == 5
    assert modes and modes[0].value == 0
