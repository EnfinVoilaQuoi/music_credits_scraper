"""Tests du cycle de vie des providers (Refacto Phase 3.5) — sans réseau.

Règle d'ownership « qui crée ferme » : un provider ne ferme que la ressource
créée par SA factory (lazy, au 1er usage) ; une ressource injectée (tests) ou
empruntée (scraper Spotify utilisé par ReccoBeats) n'est jamais fermée par lui.
close() est idempotent et ré-ouvrable (recréation au run suivant).
"""

import pytest

from src.enrichment.base import LazyResource
from src.enrichment.providers.reccobeats import ReccoBeatsProvider
from src.enrichment.providers.songbpm import SongBpmProvider
from src.enrichment.providers.spotify_id import SpotifyIdProvider
from src.models.artist import Artist
from src.models.track import Track
from src.utils.data_enricher import DataEnricher

ALL_SOURCES = {
    "spotify_id",
    "reccobeats",
    "getsongbpm",
    "songbpm",
    "bpmfinder",
    "deezer",
    "discogs",
}


class _FakeResource:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


# ──────────────────────────────────────────────────────────────────────
# LazyResource : création lazy, ownership, panne de factory
# ──────────────────────────────────────────────────────────────────────


def test_factory_non_appelee_avant_le_premier_usage():
    calls = []

    def factory():
        calls.append(1)
        return _FakeResource()

    res = LazyResource(factory=factory)
    assert calls == []  # rien à la construction
    first = res.get()
    assert calls == [1]
    assert res.get() is first  # une seule création
    assert calls == [1]


def test_close_ferme_la_ressource_creee_et_permet_la_recreation():
    res = LazyResource(factory=_FakeResource)
    first = res.get()
    res.close()
    assert first.closed == 1
    res.close()  # idempotent
    assert first.closed == 1
    second = res.get()  # run suivant : recréation par la factory
    assert second is not first


def test_close_ne_ferme_pas_une_ressource_injectee():
    injected = _FakeResource()
    res = LazyResource(resource=injected)
    assert res.get() is injected
    res.close()
    assert injected.closed == 0  # « qui crée ferme » : pas créée ici


def test_factory_en_echec_marque_la_ressource_cassee():
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("panne simulée")

    res = LazyResource(factory=boom, label="test")
    assert res.available() is True  # avant le 1er essai
    assert res.get() is None
    assert res.available() is False
    assert res.get() is None  # pas de retentative pour la session
    assert calls == [1]


# ──────────────────────────────────────────────────────────────────────
# Ownership du scraper Spotify partagé (SpotifyIdProvider ↔ ReccoBeats)
# ──────────────────────────────────────────────────────────────────────


def test_scraper_partage_ferme_par_son_proprietaire_seulement():
    spotify = SpotifyIdProvider(scraper_factory=_FakeResource)
    client = _FakeResource()  # client ReccoBeats injecté → jamais fermé
    recco = ReccoBeatsProvider(client=client, spotify_scraper_getter=lambda: spotify.scraper)

    borrowed = recco._spotify_scraper_getter()  # EMPRUNT au moment de l'usage
    assert borrowed is spotify.scraper  # même instance, créée lazy

    recco.close()  # ne ferme NI le scraper emprunté NI le client injecté
    assert borrowed.closed == 0
    assert client.closed == 0

    spotify.close()  # le propriétaire ferme
    assert borrowed.closed == 1
    assert spotify.scraper is not borrowed  # recréé pour le run suivant


def test_provider_ferme_son_client_cree_par_factory():
    created = []

    def factory():
        created.append(_FakeResource())
        return created[-1]

    recco = ReccoBeatsProvider(client_factory=factory)
    track = Track(title="Solo", artist=Artist(name="X"))
    from src.enrichment.context import EnrichmentContext

    recco.try_by_isrc(track, EnrichmentContext())  # 1er usage → création
    assert len(created) == 1
    recco.close()
    assert created[0].closed == 1


def test_provider_sans_ressource_indisponible_et_enrich_false():
    def boom():
        raise RuntimeError("panne simulée")

    provider = SongBpmProvider(scraper_factory=boom)
    assert provider.is_available() is True  # avant le 1er essai
    from src.enrichment.context import EnrichmentContext

    track = Track(title="Solo", artist=Artist(name="X"))
    assert provider.enrich(track, EnrichmentContext()) is False
    assert provider.is_available() is False


# ──────────────────────────────────────────────────────────────────────
# DataEnricher : composition sans ressource, close() par providers
# ──────────────────────────────────────────────────────────────────────


def test_init_ne_cree_aucune_ressource_de_source():
    enricher = DataEnricher()
    assert set(enricher.apis_available) == ALL_SOURCES
    # Création lazy : aucun scraper/client de source instancié au démarrage
    assert enricher._spotify_id_provider._resource._resource is None
    assert enricher._songbpm_provider._resource._resource is None
    assert enricher._reccobeats_provider._resource._resource is None
    assert enricher._discogs_provider._resource._resource is None
    enricher.close()  # no-op : rien n'a été créé


def test_close_appelle_le_close_de_chaque_provider():
    class _FakeProvider:
        def __init__(self, name):
            self.name = name
            self.closed = 0

        def close(self):
            self.closed += 1

    enricher = DataEnricher.__new__(DataEnricher)
    fakes = {name: _FakeProvider(name) for name in ALL_SOURCES}
    enricher._spotify_id_provider = fakes["spotify_id"]
    enricher._reccobeats_provider = fakes["reccobeats"]
    enricher._getsongbpm_provider = fakes["getsongbpm"]
    enricher._songbpm_provider = fakes["songbpm"]
    enricher._bpmfinder_provider = fakes["bpmfinder"]
    enricher._deezer_provider = fakes["deezer"]
    enricher._discogs_provider = fakes["discogs"]

    enricher.close()
    assert all(f.closed == 1 for f in fakes.values())


def test_close_continue_malgre_un_provider_qui_leve():
    class _BrokenProvider:
        name = "broken"

        def close(self):
            raise RuntimeError("panne simulée")

    class _OkProvider:
        def __init__(self, name):
            self.name = name
            self.closed = 0

        def close(self):
            self.closed += 1

    enricher = DataEnricher.__new__(DataEnricher)
    enricher._spotify_id_provider = _BrokenProvider()
    ok = [_OkProvider(n) for n in ("reccobeats", "getsongbpm", "songbpm", "bpmfinder", "deezer")]
    (
        enricher._reccobeats_provider,
        enricher._getsongbpm_provider,
        enricher._songbpm_provider,
        enricher._bpmfinder_provider,
        enricher._deezer_provider,
    ) = ok
    enricher._discogs_provider = _OkProvider("discogs")

    enricher.close()  # ne lève pas
    assert all(p.closed == 1 for p in ok)
    assert enricher._discogs_provider.closed == 1


def test_tous_les_providers_declarent_leur_capacite():
    # E7b (structurel) : chaque provider audio annonce {Capability.BPM}. Inerte
    # côté orchestrateur, mais le contrat est verrouillé ici.
    from src.enrichment.base import Capability
    from src.enrichment.providers.bpmfinder import BpmFinderProvider
    from src.enrichment.providers.deezer import DeezerProvider
    from src.enrichment.providers.discogs import DiscogsProvider
    from src.enrichment.providers.getsongbpm import GetSongBpmProvider
    from src.enrichment.providers.reccobeats import ReccoBeatsProvider
    from src.enrichment.providers.songbpm import SongBpmProvider
    from src.enrichment.providers.spotify_id import SpotifyIdProvider

    classes = [
        BpmFinderProvider,
        DeezerProvider,
        DiscogsProvider,
        GetSongBpmProvider,
        ReccoBeatsProvider,
        SongBpmProvider,
        SpotifyIdProvider,
    ]
    assert {c.name for c in classes} == ALL_SOURCES
    for cls in classes:
        assert cls.capabilities == {Capability.BPM}, cls.name


def test_pas_de_del_non_deterministe():
    # Le teardown est explicite (finally du worker + _on_closing) : plus de
    # __del__ dont l'ordre au shutdown de l'interpréteur causait des EPIPE.
    assert not hasattr(DataEnricher, "__del__")
    from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper

    assert not hasattr(SpotifyIDScraper, "__del__")


def test_bpmfinder_scraper_expose_pour_la_gui():
    # manual_entry (✏️) accède à data_enricher.bpmfinder_scraper : la propriété
    # de compat délègue au provider (None si source non configurée).
    enricher = DataEnricher()
    if enricher.apis_available["bpmfinder"]:
        pytest.skip("BPM Finder configuré sur cette machine (session/credentials)")
    assert enricher.bpmfinder_scraper is None
