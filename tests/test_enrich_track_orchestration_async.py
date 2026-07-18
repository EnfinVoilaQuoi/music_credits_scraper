"""Tests miroir de l'orchestrateur async `enrich_track_async` (Phase F2c) — sans réseau.

Même harnais que `test_enrich_track_orchestration.py` (fakes héritant des
providers réels → les VRAIS `gate()` tournent), piloté par `asyncio.run` : la
voie async doit produire exactement les mêmes ordres d'appel, valeurs de
résultat, court-circuits et nettoyages que la voie sync.
"""

import asyncio
import threading

from src.concurrency.serial_worker import SerialWorker
from src.enrichment.providers.bpmfinder import BpmFinderProvider
from src.enrichment.providers.deezer import DeezerProvider
from src.enrichment.providers.discogs import DiscogsProvider
from src.enrichment.providers.getsongbpm import GetSongBpmProvider
from src.enrichment.providers.reccobeats import ReccoBeatsProvider
from src.enrichment.providers.songbpm import SongBpmProvider
from src.enrichment.providers.spotify_id import SpotifyIdProvider
from src.models.artist import Artist
from src.models.track import Track
from src.utils.data_enricher import DataEnricher

ALL_SOURCES = [
    "spotify_id",
    "reccobeats",
    "getsongbpm",
    "songbpm",
    "bpmfinder",
    "deezer",
    "discogs",
]

SYNC_THREAD = "test-enrich-sync"


class _FakeEnrich:
    """Mixin : `enrich()` journalisé + `enrich_async` direct (sans thread sync).

    Le gating vient des classes réelles ; `enrich_async` est court-circuité
    pour tester l'ORCHESTRATION (les ponts sync réels sont testés à part par
    `test_pont_sync_execute_sur_le_thread_dedie`).
    """

    def __init__(self, calls, result=True, exc=None, on_enrich=None):
        super().__init__()  # ctor réel, sans ressource
        self._calls = calls
        self._result = result
        self._exc = exc
        self._on_enrich = on_enrich
        self.enrich_calls = 0
        self.last_ctx = None

    def is_available(self):
        return True

    def enrich(self, track, ctx):
        self._calls.append(self.name)
        self.enrich_calls += 1
        self.last_ctx = ctx
        if self._exc is not None:
            raise self._exc
        if self._on_enrich is not None:
            return self._on_enrich(track, ctx)
        return self._result

    async def enrich_async(self, track, ctx):
        return self.enrich(track, ctx)


class _FakeSpotify(_FakeEnrich, SpotifyIdProvider):
    pass


class _FakeGetSongBpm(_FakeEnrich, GetSongBpmProvider):
    pass


class _FakeSongBpm(_FakeEnrich, SongBpmProvider):
    pass


class _FakeBpmFinder(_FakeEnrich, BpmFinderProvider):
    pass


class _FakeDeezer(_FakeEnrich, DeezerProvider):
    pass


class _FakeDiscogs(_FakeEnrich, DiscogsProvider):
    pass


class _FakeRecco(_FakeEnrich, ReccoBeatsProvider):
    """ReccoBeats a un 2ᵉ point d'entrée : la voie ISRC (pré-étape)."""

    def __init__(self, calls, isrc_result=False, **kwargs):
        super().__init__(calls, **kwargs)
        self._isrc_result = isrc_result
        self.isrc_calls = 0

    def try_by_isrc(self, track, ctx):
        self._calls.append("isrc")
        self.isrc_calls += 1
        return self._isrc_result

    async def try_by_isrc_async(self, track, ctx):
        return self.try_by_isrc(track, ctx)


class _JournalSyncSongBpm(SongBpmProvider):
    """SANS override d'enrich_async : exerce le PONT réel via ctx.sync_runner."""

    def __init__(self):
        super().__init__()
        self.seen_thread = None

    def is_available(self):
        return True

    def enrich(self, track, ctx):
        self.seen_thread = threading.current_thread().name
        return True


def _track(**attrs):
    track = Track(title="Solo", artist=Artist(name="X"))
    for name, value in attrs.items():
        setattr(track, name, value)
    return track


def _enricher(**overrides):
    """DataEnricher sans __init__ : fakes + journal + runner sync de test."""
    calls = []
    fakes = {
        "spotify_id": _FakeSpotify(calls),
        "reccobeats": _FakeRecco(calls),
        "getsongbpm": _FakeGetSongBpm(calls),
        "songbpm": _FakeSongBpm(calls),
        "bpmfinder": _FakeBpmFinder(calls),
        "deezer": _FakeDeezer(calls),
        "discogs": _FakeDiscogs(calls),
    }
    fakes.update(overrides)

    enricher = DataEnricher.__new__(DataEnricher)
    enricher.genius_client = None
    enricher.apis_available = {name: True for name in ALL_SOURCES}
    enricher._spotify_id_provider = fakes["spotify_id"]
    enricher._reccobeats_provider = fakes["reccobeats"]
    enricher._getsongbpm_provider = fakes["getsongbpm"]
    enricher._songbpm_provider = fakes["songbpm"]
    enricher._bpmfinder_provider = fakes["bpmfinder"]
    enricher._deezer_provider = fakes["deezer"]
    enricher._discogs_provider = fakes["discogs"]
    enricher._http = None  # les fakes ne touchent pas au réseau
    enricher.sync_runner = SerialWorker(SYNC_THREAD)
    return enricher, fakes, calls


def _run(enricher, track, **kwargs):
    return asyncio.run(enricher.enrich_track_async(track, **kwargs))


def test_ordre_nominal_identique_a_la_voie_sync():
    seen = {}

    def _deezer_vote(track, ctx):
        ctx.bpm_ballot.add("deezer", 120)
        return True

    def _discogs_observe(track, ctx):
        seen["bpm_at_discogs"] = track.bpm  # vote finalisé AVANT Discogs
        return True

    enricher, fakes, calls = _enricher()
    fakes["deezer"]._on_enrich = _deezer_vote
    fakes["discogs"]._on_enrich = _discogs_observe

    results = _run(enricher, _track())

    assert calls == [
        "isrc",
        "spotify_id",
        "reccobeats",
        "getsongbpm",
        "songbpm",
        "bpmfinder",
        "deezer",
        "discogs",
    ]
    assert results == {name: True for name in ALL_SOURCES}
    assert seen["bpm_at_discogs"] == 120
    assert fakes["deezer"].last_ctx.allow_spotify_scrape is False


def test_voie_isrc_satisfaite_court_circuite_spotify_et_reccobeats():
    recco = _FakeRecco([], isrc_result=True)
    enricher, fakes, _ = _enricher(reccobeats=recco)

    results = _run(enricher, _track())

    assert results["spotify_id"] == "not_needed"
    assert fakes["spotify_id"].enrich_calls == 0
    assert results["reccobeats"] is True
    assert recco.enrich_calls == 0


def test_consensus_bpm_saute_songbpm():
    def _vote_100(name):
        def inner(track, ctx):
            ctx.bpm_ballot.add(name, 100)
            return True

        return inner

    enricher, fakes, _ = _enricher()
    fakes["reccobeats"]._on_enrich = _vote_100("reccobeats")
    fakes["getsongbpm"]._on_enrich = _vote_100("getsongbpm")

    track = _track(key=5, mode=1, duration=200)
    results = _run(enricher, track)

    assert results["songbpm"] == "not_needed"
    assert fakes["songbpm"].enrich_calls == 0
    assert track.bpm == 100


def test_crash_songbpm_donne_none_et_bloque_le_nettoyage():
    calls = []
    overrides = {
        "spotify_id": _FakeSpotify(calls, result=False),
        "reccobeats": _FakeRecco(calls, result=False),
        "getsongbpm": _FakeGetSongBpm(calls, result=False),
        "songbpm": _FakeSongBpm(calls, exc=RuntimeError("timeout simulé")),
        "bpmfinder": _FakeBpmFinder(calls, result=False),
        "deezer": _FakeDeezer(calls, result=False),
        "discogs": _FakeDiscogs(calls, result=False),
    }
    enricher, _, _ = _enricher(**overrides)

    track = _track(bpm=95, key=5, mode=1, duration=200, musical_key="Sol majeur")
    results = _run(enricher, track, force_update=True, clear_on_failure=True)

    assert results["songbpm"] is None  # crash ≠ « pas de données »
    assert "cleaned" not in results
    assert track.bpm == 95


def test_nettoyage_si_toutes_les_tentatives_ont_echoue():
    calls = []
    overrides = {
        name: cls(calls, result=False)
        for name, cls in [
            ("spotify_id", _FakeSpotify),
            ("getsongbpm", _FakeGetSongBpm),
            ("songbpm", _FakeSongBpm),
            ("bpmfinder", _FakeBpmFinder),
            ("deezer", _FakeDeezer),
            ("discogs", _FakeDiscogs),
        ]
    }
    overrides["reccobeats"] = _FakeRecco(calls, result=False)
    enricher, _, _ = _enricher(**overrides)

    track = _track(bpm=95, key=5, mode=1, duration=200, musical_key="Sol majeur")
    results = _run(enricher, track, force_update=True, clear_on_failure=True)

    assert results["cleaned"] is True
    assert track.bpm is None
    assert track.title == "Solo"  # données essentielles intactes


def test_pas_de_nettoyage_si_aucune_source_n_a_tente():
    bpmfinder = _FakeBpmFinder([], result="skipped")
    enricher, _, _ = _enricher(bpmfinder=bpmfinder)

    track = _track(bpm=100)
    results = _run(enricher, track, sources=["bpmfinder"], force_update=True, clear_on_failure=True)

    assert results == {"bpmfinder": "skipped"}
    assert track.bpm == 100


def test_source_absente_ou_indisponible_non_appelee():
    enricher, fakes, calls = _enricher()
    results = _run(enricher, _track(), sources=["deezer"])
    assert calls == ["deezer"]
    assert set(results) == {"deezer"}

    enricher, _, calls = _enricher()
    enricher.apis_available["deezer"] = False
    results = _run(enricher, _track(), sources=["deezer"])
    assert calls == []
    assert results == {}


def test_observations_du_run_par_source():
    from src.enrichment.observation import Observation

    def _recco(track, ctx):
        ctx.bpm_ballot.add("reccobeats", 111)
        ctx.observations.extend(
            [Observation("key", 5, "reccobeats"), Observation("mode", 1, "reccobeats")]
        )
        return True

    enricher, fakes, _ = _enricher()
    fakes["reccobeats"]._on_enrich = _recco

    track = _track()
    _run(enricher, track)

    obs = {(o.field, o.source): o for o in track.observations}
    assert obs[("bpm", "reccobeats")].value == 111
    assert obs[("key", "reccobeats")].value == 5
    assert obs[("mode", "reccobeats")].value == 1


def test_pont_sync_execute_sur_le_thread_dedie():
    """Le pont réel `enrich_async` des providers scrapers doit exécuter le corps
    sync sur le thread SerialWorker du run (affinité Playwright)."""
    songbpm = _JournalSyncSongBpm()
    enricher, _, _ = _enricher(songbpm=songbpm)

    results = _run(enricher, _track(), sources=["songbpm"])

    assert results["songbpm"] is True
    assert songbpm.seen_thread == SYNC_THREAD
