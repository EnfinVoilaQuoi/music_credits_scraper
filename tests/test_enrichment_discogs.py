"""Tests du provider Discogs (src/enrichment/providers/discogs) — délégation pure."""

import pytest
from discogs_client.exceptions import HTTPError

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


class _RaisingSearch:
    """Faux client discogs dont .search lève l'exception fournie (pas de _fetcher
    → _check_rate_limit devient un no-op)."""

    def __init__(self, exc):
        self._exc = exc

    def search(self, *a, **k):
        raise self._exc


def test_search_track_erreur_discogs_renvoie_none(caplog):
    """Frontière réseau resserrée : une DiscogsAPIError (dont HTTPError) est
    catchée → repli None, échec journalisé (plus de silence)."""
    client = DiscogsClient()
    client.client = _RaisingSearch(HTTPError("boom", 500))
    with caplog.at_level("ERROR"):
        assert client.search_track("Titre", "Artiste") is None
    assert any("Discogs" in r.getMessage() for r in caplog.records)


def test_search_track_erreur_inattendue_propage():
    """Une exception hors domaine (bug interne) n'est plus avalée : elle remonte
    au filet amont (_run_step) au lieu de devenir une donnée manquante muette."""
    client = DiscogsClient()
    client.client = _RaisingSearch(RuntimeError("bug interne"))
    with pytest.raises(RuntimeError):
        client.search_track("Titre", "Artiste")
