"""Tests du provider Discogs (src/enrichment/providers/discogs) — délégation pure."""

from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.discogs import DiscogsProvider
from src.models.track import Track


class _FakeDiscogs:
    def __init__(self, ret):
        self._ret = ret
        self.calls = []

    def enrich_track_data(self, track, force_update):
        self.calls.append((track, force_update))
        return self._ret


def test_is_available():
    assert DiscogsProvider(None).is_available() is False
    assert DiscogsProvider(_FakeDiscogs(True)).is_available() is True


def test_enrich_delegue_et_transmet_force_update():
    client = _FakeDiscogs(True)
    provider = DiscogsProvider(client)
    track = Track(title="X")
    assert provider.enrich(track, EnrichmentContext(force_update=True)) is True
    assert client.calls == [(track, True)]
