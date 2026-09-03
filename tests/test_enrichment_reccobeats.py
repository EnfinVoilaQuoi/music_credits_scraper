"""Tests du provider ReccoBeats (src/enrichment/providers/reccobeats) — sans réseau.

Deux voies : ISRC (try_by_isrc) et Spotify ID (enrich). Clients mockés.
"""

from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.reccobeats import ReccoBeatsProvider
from src.models.artist import Artist
from src.models.track import Track


class _FakeReccoClient:
    def __init__(self, isrc_info=None, track_info=None):
        self._isrc_info = isrc_info
        self._track_info = track_info

    def get_track_info_by_isrc(self, isrc):
        return self._isrc_info

    def get_track_info(self, spotify_id):
        return self._track_info


class _FakeSpotifyScraper:
    def __init__(self, spotify_id=None, page_title=None):
        self._id = spotify_id
        self._title = page_title

    def get_spotify_id(self, artist, title):
        return self._id

    def get_spotify_page_title(self, spotify_id):
        return self._title


def _track():
    return Track(title="Solo", artist=Artist(name="Sofiane Pamart"))


def test_is_available():
    assert ReccoBeatsProvider(None).is_available() is False
    assert ReccoBeatsProvider(_FakeReccoClient()).is_available() is True


def test_try_by_isrc_applique_bpm_et_resolution():
    client = _FakeReccoClient(isrc_info={"success": True, "bpm": 120, "key": 5, "mode": 1})
    provider = ReccoBeatsProvider(client)
    track = _track()
    track.isrc = "FRX9820001"
    ctx = EnrichmentContext()
    assert provider.try_by_isrc(track, ctx) is True
    assert ("reccobeats", 120) in ctx.bpm_ballot.candidates
    assert track.audio.reccobeats_resolution == "isrc"


def test_try_by_isrc_sans_isrc_renvoie_false():
    provider = ReccoBeatsProvider(_FakeReccoClient())
    assert provider.try_by_isrc(_track(), EnrichmentContext()) is False


def test_gate_skip_avec_resultat_true_si_isrc_satisfaite():
    # La voie ISRC (pré-étape) a déjà satisfait la source : pas de second appel,
    # et le résultat posé dans le dict est True (valeur historique)
    ctx = EnrichmentContext(isrc_satisfied=True)
    assert ReccoBeatsProvider().gate(_track(), ctx) is True


def test_gate_execute_sinon():
    assert ReccoBeatsProvider().gate(_track(), EnrichmentContext()) is None


def test_enrich_par_spotify_id_existant_valide():
    client = _FakeReccoClient(
        track_info={"success": True, "bpm": 140, "key": 7, "mode": 0, "duration": 200}
    )
    provider = ReccoBeatsProvider(client)
    track = _track()
    track.spotify_id = "spot123"
    ctx = EnrichmentContext(
        artist_tracks=[Track(title="Autre")],
        validate_spotify_id_unique=lambda sid, t, tracks: True,
    )
    assert provider.enrich(track, ctx) is True
    assert ("reccobeats", 140) in ctx.bpm_ballot.candidates
    assert track.audio.reccobeats_resolution == "spotify_id"
    assert track.duration == 200


def test_enrich_scrape_spotify_si_autorise():
    client = _FakeReccoClient(track_info={"success": True, "bpm": 100})
    scraper = _FakeSpotifyScraper(spotify_id="scraped99", page_title="Solo - Sofiane Pamart")
    provider = ReccoBeatsProvider(client, spotify_scraper_getter=lambda: scraper)
    track = _track()
    ctx = EnrichmentContext(allow_spotify_scrape=True)
    assert provider.enrich(track, ctx) is True
    assert track.spotify_id == "scraped99"


def test_enrich_sans_id_ni_scrape_renvoie_false():
    scraper = _FakeSpotifyScraper(None)
    provider = ReccoBeatsProvider(_FakeReccoClient(), spotify_scraper_getter=lambda: scraper)
    track = _track()
    ctx = EnrichmentContext(allow_spotify_scrape=False)
    assert provider.enrich(track, ctx) is False


def test_try_by_isrc_emet_observation_provenance():
    """La provenance ReccoBeats est émise en observation → persistée (survit au reload)."""
    from src.enrichment.observation import Observation

    client = _FakeReccoClient(isrc_info={"success": True, "bpm": 120})
    provider = ReccoBeatsProvider(client)
    track = _track()
    track.isrc = "FRX9820001"
    ctx = EnrichmentContext()
    provider.try_by_isrc(track, ctx)
    assert Observation("reccobeats_resolution", "isrc", "reccobeats") in ctx.observations


def test_reccobeats_resolution_repose_au_chargement():
    """apply_resolutions repose reccobeats_resolution depuis son observation (reload)."""
    from src.enrichment.observation import Observation
    from src.enrichment.reconcile import apply_resolutions, reconcile

    track = _track()
    track.audio.reccobeats_resolution = None  # état « rechargé » par le mapper (colonne droppée)
    obs = [Observation("reccobeats_resolution", "spotify_id", "reccobeats")]
    apply_resolutions(track, reconcile(obs))
    assert track.audio.reccobeats_resolution == "spotify_id"


# ── Voie ASYNC (F2/F3b) ───────────────────────────────────────────────────────
#
# Les jumeaux async partagent l'APPLICATION du résultat (`_apply_result`,
# `_apply_spotify_info`) avec la voie sync : seuls les appels réseau changent.
# Ce qu'on vérifie ici, c'est que la voie async aboutit au MÊME état de track
# depuis les mêmes payloads — et qu'elle sait retomber sur le pont sync quand
# aucun scraper async n'est configuré.

import asyncio  # noqa: E402


class _FakeReccoClientAsync:
    def __init__(self, isrc_info=None, track_info=None, leve=None):
        self._isrc_info = isrc_info
        self._track_info = track_info
        self._leve = leve

    async def get_track_info_by_isrc_async(self, http, isrc):
        if self._leve:
            raise self._leve
        return self._isrc_info

    async def get_track_info_async(self, http, spotify_id):
        if self._leve:
            raise self._leve
        return self._track_info


class _FakeDeezerAsync:
    def __init__(self, isrc=None):
        self._isrc = isrc

    async def get_isrc_async(self, http, artist, title):
        return self._isrc


class _FakeScraperAsync:
    def __init__(self, spotify_id=None, page_title=None):
        self._id = spotify_id
        self._title = page_title

    async def get_spotify_id_async(self, artist, title):
        return self._id

    async def get_spotify_page_title_async(self, spotify_id):
        return self._title


class _SyncRunner:
    """Pont sync du run : exécute le bloc bloquant tel quel."""

    def __init__(self):
        self.appels = 0

    async def run(self, fn, *args):
        self.appels += 1
        return fn(*args)


def test_try_by_isrc_async_applique_le_meme_resultat_que_le_sync():
    payload = {"success": True, "bpm": 120, "key": 5, "mode": 1}
    track_sync, track_async = _track(), _track()
    track_sync.isrc = track_async.isrc = "FRX9820001"

    ctx_sync, ctx_async = EnrichmentContext(), EnrichmentContext()
    ReccoBeatsProvider(_FakeReccoClient(isrc_info=payload)).try_by_isrc(track_sync, ctx_sync)
    asyncio.run(
        ReccoBeatsProvider(_FakeReccoClientAsync(isrc_info=payload)).try_by_isrc_async(
            track_async, ctx_async
        )
    )
    assert ctx_async.bpm_ballot.candidates == ctx_sync.bpm_ballot.candidates
    assert track_async.audio.reccobeats_resolution == track_sync.audio.reccobeats_resolution


def test_try_by_isrc_async_recupere_l_isrc_via_deezer():
    """Sans ISRC en base, Deezer le fournit — c'est ce qui évite un scrape
    Spotify pour ce morceau."""
    provider = ReccoBeatsProvider(
        _FakeReccoClientAsync(isrc_info={"success": True, "bpm": 90}),
        deezer_client=_FakeDeezerAsync(isrc="FRX9820001"),
    )
    track = _track()
    assert asyncio.run(provider.try_by_isrc_async(track, EnrichmentContext())) is True
    assert track.isrc == "FRX9820001"


def test_try_by_isrc_async_sans_isrc_ni_deezer():
    provider = ReccoBeatsProvider(_FakeReccoClientAsync())
    assert asyncio.run(provider.try_by_isrc_async(_track(), EnrichmentContext())) is False


def test_try_by_isrc_async_api_en_erreur():
    provider = ReccoBeatsProvider(_FakeReccoClientAsync(leve=KeyError("payload inattendu")))
    track = _track()
    track.isrc = "FRX9820001"
    assert asyncio.run(provider.try_by_isrc_async(track, EnrichmentContext())) is False


def test_try_by_isrc_async_sans_client():
    assert (
        asyncio.run(ReccoBeatsProvider(None).try_by_isrc_async(_track(), EnrichmentContext()))
        is False
    )


def test_enrich_async_avec_id_existant():
    """Même contexte que la voie sync : sans `artist_tracks` NI validateur
    d'unicité, un ID existant est considéré comme un doublon et re-scrapé
    (comportement historique, cf. `_existing_spotify_id`)."""
    provider = ReccoBeatsProvider(
        _FakeReccoClientAsync(track_info={"success": True, "bpm": 140, "key": 0, "mode": 1})
    )
    track = _track()
    track.spotify_id = "spot123"
    ctx = EnrichmentContext(
        artist_tracks=[Track(title="Autre")],
        validate_spotify_id_unique=lambda sid, t, tracks: True,
    )
    assert asyncio.run(provider.enrich_async(track, ctx)) is True
    assert ("reccobeats", 140) in ctx.bpm_ballot.candidates


def test_enrich_async_id_duplique_est_rescrape():
    """Un ID existant non validé est EFFACÉ et re-scrapé — même règle des deux
    côtés, sinon les deux voies divergeraient sur la même base."""
    provider = ReccoBeatsProvider(
        _FakeReccoClientAsync(track_info={"success": True, "bpm": 100}),
        spotify_scraper_async_getter=lambda: _FakeScraperAsync(spotify_id="NOUVEAU"),
    )
    track = _track()
    track.spotify_id = "DUPLIQUE"
    ctx = EnrichmentContext(sync_runner=_SyncRunner())
    assert asyncio.run(provider.enrich_async(track, ctx)) is True
    assert track.spotify_id == "NOUVEAU"


def test_enrich_async_scrape_via_le_jumeau_async():
    """Un scraper async configuré est utilisé NATIVEMENT, sans passer par le
    pont sync (qui bloquerait un thread pour rien)."""
    runner = _SyncRunner()
    provider = ReccoBeatsProvider(
        _FakeReccoClientAsync(track_info={"success": True, "bpm": 100}),
        spotify_scraper_async_getter=lambda: _FakeScraperAsync(spotify_id="SP1"),
    )
    ctx = EnrichmentContext(sync_runner=runner)
    assert asyncio.run(provider.enrich_async(_track(), ctx)) is True
    assert runner.appels == 0


def test_enrich_async_repli_sur_le_pont_sync():
    """Sans variante async configurée (cas par défaut aujourd'hui), le scrape
    passe par le pont sync — le comportement doit rester identique."""
    runner = _SyncRunner()
    provider = ReccoBeatsProvider(
        _FakeReccoClientAsync(track_info={"success": True, "bpm": 100}),
        spotify_scraper_getter=lambda: _FakeSpotifyScraper(spotify_id="SP1"),
    )
    ctx = EnrichmentContext(sync_runner=runner)
    assert asyncio.run(provider.enrich_async(_track(), ctx)) is True
    assert runner.appels == 1


def test_enrich_async_scrape_interdit():
    """`allow_spotify_scrape=False` : l'étape 0 a déjà tenté le scrape, on ne
    rouvre pas un navigateur pour le même morceau."""
    runner = _SyncRunner()
    provider = ReccoBeatsProvider(
        _FakeReccoClientAsync(),
        spotify_scraper_async_getter=lambda: _FakeScraperAsync(spotify_id="SP1"),
    )
    ctx = EnrichmentContext(allow_spotify_scrape=False, sync_runner=runner)
    assert asyncio.run(provider.enrich_async(_track(), ctx)) is False
    assert runner.appels == 0


def test_enrich_async_sans_id_disponible():
    provider = ReccoBeatsProvider(_FakeReccoClientAsync())
    ctx = EnrichmentContext(sync_runner=_SyncRunner())
    assert asyncio.run(provider.enrich_async(_track(), ctx)) is False


def test_enrich_async_sans_client():
    assert (
        asyncio.run(ReccoBeatsProvider(None).enrich_async(_track(), EnrichmentContext())) is False
    )


def test_enrich_async_api_en_erreur():
    provider = ReccoBeatsProvider(_FakeReccoClientAsync(leve=TypeError("réponse illisible")))
    track = _track()
    track.spotify_id = "4cOdK2wGLETKBW3PvgPWqT"
    assert asyncio.run(provider.enrich_async(track, EnrichmentContext())) is False
