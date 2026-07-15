"""Tests du provider Discogs (src/enrichment/providers/discogs) — délégation pure."""

from src.api.discogs_api import DiscogsClient
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


def test_enrich_transmet_not_needed_tel_quel():
    """Le sentinel « rien de nouveau » remonte intact à l'orchestrateur."""
    provider = DiscogsProvider(_FakeDiscogs("not_needed"))
    assert provider.enrich(Track(title="X"), EnrichmentContext()) == "not_needed"


def _client(track_data):
    """DiscogsClient (sans token, construction locale) avec search_track patché."""
    client = DiscogsClient()
    client.search_track = lambda *a, **k: track_data
    return client


def test_enrich_track_data_nouvelle_donnee_renvoie_true():
    track = Track(title="X")
    result = _client({"discogs_id": 456}).enrich_track_data(track)
    assert result is True
    assert track.discogs_id == 456


def test_enrich_track_data_matche_sans_nouveaute_renvoie_not_needed():
    """Release matchée mais rien de NOUVEAU (ici : uniquement des labels, jamais
    posés sur le track) → "not_needed", pas False : ce n'est pas un échec."""
    result = _client({"labels": ["Some Label"]}).enrich_track_data(Track(title="X"))
    assert result == "not_needed"


def test_enrich_track_data_aucun_match_renvoie_false():
    assert _client(None).enrich_track_data(Track(title="X")) is False
