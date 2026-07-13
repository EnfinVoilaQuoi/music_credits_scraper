"""Tests du provider BPM Finder (src/enrichment/providers/bpmfinder) — sans réseau.

Scraper mocké. On évite la recherche YouTube en fournissant un youtube_url au
track (le provider ne cherche un lien que s'il en manque un).
"""

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


def test_applique_bpm_manquant():
    scraper = _FakeScraper({"bpm": 140})
    provider = BpmFinderProvider(scraper)
    track = _track(bpm=None)  # bpm manquant → analyse
    track.key = 5  # key/mode présents → seul le bpm est appliqué
    track.mode = 1
    assert provider.enrich(track, EnrichmentContext()) is True
    assert track.bpm == 140
    assert track.bpm_source == "bpmfinder"


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
