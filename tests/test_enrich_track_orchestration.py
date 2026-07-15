"""Tests de caractérisation de l'orchestrateur `DataEnricher.enrich_track` — sans réseau.

Figent le comportement observable AVANT la réduction en boucle de providers
(Refacto Phase 3.4) : ordre d'appel, gating interdépendant (voie ISRC, consensus
BPM), valeurs de résultat par source (songbpm `None` sur crash ≠ `False`),
nettoyage sur échec complet (garde `all([])` du bug TOTAL 90) et position du
vote BPM (entre Deezer et Discogs).

Harnais : `DataEnricher.__new__` + fakes posés à la main — le `__init__` réel
(clients HTTP, scrapers) n'est jamais appelé. Les fakes HÉRITENT des providers
réels : seul `enrich()` est simulé, si bien qu'après la Phase 3.4 (gating déplacé
dans `gate()` des providers) ces tests exercent la VRAIE logique de gating.
"""

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


class _FakeEnrich:
    """Mixin : `enrich()` journalisé et contrôlable, ressource absente.

    Tout le reste (name, et le gating une fois en Phase 3.4) vient de la classe
    réelle, pour que l'orchestration soit testée contre la vraie logique.
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


def _track(**attrs):
    track = Track(title="Solo", artist=Artist(name="X"))
    for name, value in attrs.items():
        setattr(track, name, value)
    return track


def _enricher(**overrides):
    """DataEnricher sans __init__ : fakes + journal d'appels partagé.

    `overrides` : fake à substituer par source (clé = nom de source).
    """
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
    return enricher, fakes, calls


# ──────────────────────────────────────────────────────────────────────
# Ordre d'appel et position du vote BPM
# ──────────────────────────────────────────────────────────────────────


def test_ordre_nominal_et_finalize_entre_deezer_et_discogs():
    seen = {}

    def _deezer_vote(track, ctx):
        ctx.bpm_ballot.add("deezer", 120)
        return True

    def _discogs_observe(track, ctx):
        # Le vote BPM doit être finalisé AVANT Discogs (position historique)
        seen["bpm_at_discogs"] = track.bpm
        return True

    enricher, fakes, calls = _enricher()
    fakes["deezer"]._on_enrich = _deezer_vote
    fakes["discogs"]._on_enrich = _discogs_observe

    results = enricher.enrich_track(_track())

    assert calls == [
        "isrc",  # voie ISRC prioritaire (pré-étape, avant tout scrape)
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
    assert fakes["deezer"].last_ctx.allow_spotify_scrape is False  # spotify_id dans sources


def test_allow_spotify_scrape_si_etape_spotify_id_absente():
    enricher, fakes, _ = _enricher()
    enricher.enrich_track(_track(), sources=["reccobeats"])
    assert fakes["reccobeats"].last_ctx.allow_spotify_scrape is True


# ──────────────────────────────────────────────────────────────────────
# Gating interdépendant : voie ISRC, consensus BPM, force_update
# ──────────────────────────────────────────────────────────────────────


def test_voie_isrc_satisfaite_court_circuite_spotify_et_reccobeats():
    recco = _FakeRecco([], isrc_result=True)
    enricher, fakes, _ = _enricher(reccobeats=recco)

    results = enricher.enrich_track(_track())

    assert results["spotify_id"] == "not_needed"
    assert fakes["spotify_id"].enrich_calls == 0
    assert results["reccobeats"] is True
    assert recco.enrich_calls == 0  # pas de second appel ReccoBeats


def test_consensus_bpm_saute_songbpm():
    def _vote_100(name):
        def inner(track, ctx):
            ctx.bpm_ballot.add(name, 100)
            return True

        return inner

    enricher, fakes, _ = _enricher()
    fakes["reccobeats"]._on_enrich = _vote_100("reccobeats")
    fakes["getsongbpm"]._on_enrich = _vote_100("getsongbpm")

    # key/mode/duration présents : seul le consensus décide
    track = _track(key=5, mode=1, duration=200)
    results = enricher.enrich_track(track)

    assert results["songbpm"] == "not_needed"
    assert fakes["songbpm"].enrich_calls == 0
    assert track.bpm == 100  # vote finalisé sur le consensus


def test_force_update_rappelle_le_scraper_spotify_meme_avec_id_valide():
    track_id = "A" * 22

    enricher, fakes, _ = _enricher()
    enricher.enrich_track(_track(spotify_id=track_id), force_update=False)
    assert fakes["spotify_id"].enrich_calls == 0  # ID valide → not_needed

    enricher, fakes, _ = _enricher()
    results = enricher.enrich_track(_track(spotify_id=track_id), force_update=True)
    assert fakes["spotify_id"].enrich_calls == 1
    assert results["spotify_id"] is True


# ──────────────────────────────────────────────────────────────────────
# Valeurs de résultat par source et nettoyage sur échec complet
# ──────────────────────────────────────────────────────────────────────


def _enricher_tout_en_echec(songbpm=None):
    calls = []
    overrides = {
        "spotify_id": _FakeSpotify(calls, result=False),
        "reccobeats": _FakeRecco(calls, result=False),
        "getsongbpm": _FakeGetSongBpm(calls, result=False),
        "songbpm": songbpm if songbpm is not None else _FakeSongBpm(calls, result=False),
        "bpmfinder": _FakeBpmFinder(calls, result=False),
        "deezer": _FakeDeezer(calls, result=False),
        "discogs": _FakeDiscogs(calls, result=False),
    }
    return _enricher(**overrides)


def test_crash_songbpm_donne_none_et_bloque_le_nettoyage():
    songbpm = _FakeSongBpm([], exc=RuntimeError("timeout simulé"))
    enricher, _, _ = _enricher_tout_en_echec(songbpm=songbpm)

    track = _track(bpm=95, key=5, mode=1, duration=200, musical_key="Sol majeur")
    results = enricher.enrich_track(track, force_update=True, clear_on_failure=True)

    # None (crash) ≠ False (pas de données) : n'alimente PAS « tout a échoué »
    assert results["songbpm"] is None
    assert "cleaned" not in results
    assert track.bpm == 95  # données préservées


def test_nettoyage_si_toutes_les_tentatives_ont_echoue():
    enricher, _, _ = _enricher_tout_en_echec()

    track = _track(bpm=95, key=5, mode=1, duration=200, musical_key="Sol majeur")
    results = enricher.enrich_track(track, force_update=True, clear_on_failure=True)

    assert results["cleaned"] is True
    assert track.bpm is None
    assert track.key is None
    assert track.mode is None
    assert track.duration is None
    assert track.musical_key is None
    assert track.title == "Solo"  # les données essentielles restent intactes


def test_pas_de_nettoyage_si_aucune_source_n_a_tente():
    # Bug TOTAL 90 : all([]) == True — toutes les sources en 'not_needed'/'skipped'
    # ne doit PAS passer pour « tout a échoué »
    bpmfinder = _FakeBpmFinder([], result="skipped")
    enricher, _, _ = _enricher(bpmfinder=bpmfinder)

    track = _track(bpm=100)
    results = enricher.enrich_track(
        track, sources=["bpmfinder"], force_update=True, clear_on_failure=True
    )

    assert results == {"bpmfinder": "skipped"}
    assert "cleaned" not in results
    assert track.bpm == 100


def test_pas_de_nettoyage_si_une_source_a_reussi():
    enricher, _, _ = _enricher_tout_en_echec()
    deezer_ok = _FakeDeezer([], result=True)
    enricher._deezer_provider = deezer_ok

    track = _track(bpm=95)
    results = enricher.enrich_track(track, force_update=True, clear_on_failure=True)

    assert results["deezer"] is True
    assert "cleaned" not in results
    assert track.bpm == 95


# ──────────────────────────────────────────────────────────────────────
# Filtrage par sources / disponibilité
# ──────────────────────────────────────────────────────────────────────


def test_source_absente_de_la_liste_non_appelee():
    enricher, fakes, calls = _enricher()
    results = enricher.enrich_track(_track(), sources=["deezer"])

    assert calls == ["deezer"]  # pas même la pré-étape ISRC (reccobeats exclu)
    assert set(results) == {"deezer"}
    assert fakes["spotify_id"].enrich_calls == 0


def test_source_indisponible_non_appelee():
    enricher, fakes, calls = _enricher()
    enricher.apis_available["deezer"] = False
    results = enricher.enrich_track(_track(), sources=["deezer"])

    assert calls == []
    assert results == {}


# ──────────────────────────────────────────────────────────────────────
# Observations du run (E5c-2a) : collecte PAR SOURCE dans track.observations
# (le ballot pilote encore les colonnes legacy — comportement constant)
# ──────────────────────────────────────────────────────────────────────


def test_observations_du_run_par_source():
    def _vote(name, value):
        def inner(track, ctx):
            ctx.bpm_ballot.add(name, value)
            return True

        return inner

    enricher, fakes, _ = _enricher()
    fakes["reccobeats"]._on_enrich = _vote("reccobeats", 111)
    fakes["songbpm"]._on_enrich = _vote("songbpm", 135)

    track = _track(key=5, mode=1, key_mode_source="reccobeats")
    enricher.enrich_track(track)

    obs = {(o.field, o.source): o for o in track.observations}
    # BPM : une observation par source ayant voté (valeurs BRUTES, non réconciliées)
    assert obs[("bpm", "reccobeats")].value == 111
    assert obs[("bpm", "songbpm")].value == 135
    # key/mode : miroir des colonnes, source = key_mode_source
    assert obs[("key", "reccobeats")].value == 5
    assert obs[("mode", "reccobeats")].value == 1


def test_observations_sans_key_mode_source():
    def _vote(track, ctx):
        ctx.bpm_ballot.add("deezer", 120)
        return True

    enricher, fakes, _ = _enricher()
    fakes["deezer"]._on_enrich = _vote

    track = _track()  # pas de key_mode_source → aucune observation key/mode
    enricher.enrich_track(track)

    assert {o.field for o in track.observations} == {"bpm"}


def test_observations_vides_si_aucun_candidat():
    enricher, _, _ = _enricher_tout_en_echec()
    track = _track()
    enricher.enrich_track(track)
    assert track.observations == []
