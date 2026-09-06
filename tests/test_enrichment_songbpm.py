"""Tests du provider SongBPM (src/enrichment/providers/songbpm) — sans réseau."""

import asyncio

import pytest
from playwright.async_api import Error as PlaywrightError

from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.songbpm import SongBpmProvider
from src.models.artist import Artist
from src.models.track import Track


class _FakeScraper:
    def __init__(self, data):
        self._data = data
        self.calls = []

    def search_track(self, title, artist, spotify_id=None, fetch_details=False):
        self.calls.append((title, artist, spotify_id, fetch_details))
        return self._data


class _FakeScraperAsync(_FakeScraper):
    """Variante async. `_data` peut être une exception (levée) ou un délai
    (`_lent`) pour éprouver la garde `asyncio.timeout`."""

    def __init__(self, data, lent=False):
        super().__init__(data)
        self._lent = lent
        self.ferme = False

    async def search_track_async(self, title, artist, spotify_id=None, fetch_details=False):
        self.calls.append((title, artist, spotify_id, fetch_details))
        if self._lent:
            await asyncio.sleep(3600)
        if isinstance(self._data, Exception):
            raise self._data
        return self._data

    async def aclose(self):
        self.ferme = True


class _RunnerSync:
    """Faux `sync_runner` : exécute le corps sync tel quel (c'est ce que fait le
    SerialWorker du run, sur son thread dédié)."""

    async def run(self, fn, *args):
        return fn(*args)


def _track():
    return Track(title="Solo", artist=Artist(name="Sofiane Pamart"))


def test_is_available():
    assert SongBpmProvider(None).is_available() is False
    assert SongBpmProvider(_FakeScraper({})).is_available() is True


def test_bpm_candidat_key_mode_duration():
    # E7 : BPM au scrutin, key/mode en observations PAR SOURCE (plus de pose
    # legacy directe). Duration reste une colonne (posée telle quelle).
    data = {"bpm": 90, "key": 5, "mode": 1, "duration": 200}
    provider = SongBpmProvider(_FakeScraper(data))
    track = _track()
    ctx = EnrichmentContext()
    assert provider.enrich(track, ctx) is True
    assert ("songbpm", 90) in ctx.bpm_ballot.candidates
    keys = [o for o in ctx.observations if o.field == "key" and o.source == "songbpm"]
    modes = [o for o in ctx.observations if o.field == "mode" and o.source == "songbpm"]
    assert keys and keys[0].value == 5
    assert modes and modes[0].value == 1
    assert track.duration == 200


def test_candidat_bpm_compte_comme_succes_meme_si_bpm_deja_present():
    # Régression : SongBPM confirme un BPM déjà présent → SUCCÈS (2ᵉ vote),
    # plus « ÉCHEC » à tort. Le candidat rejoint le scrutin.
    provider = SongBpmProvider(_FakeScraper({"bpm": 146}))
    track = _track()
    track.audio.bpm = 146  # déjà renseigné
    ctx = EnrichmentContext()
    assert provider.enrich(track, ctx) is True
    assert ("songbpm", 146) in ctx.bpm_ballot.candidates


def test_scraper_vide_renvoie_false():
    provider = SongBpmProvider(_FakeScraper(None))
    assert provider.enrich(_track(), EnrichmentContext()) is False


# ──────────────────────────────────────────────────────────────────────
# gate() — DÉPARTAGE : skip seulement si consensus BPM ET rien de manquant
# ──────────────────────────────────────────────────────────────────────


def _track_complet():
    track = _track()
    track.audio.key = 5
    track.audio.mode = 1
    track.duration = 200
    return track


def test_gate_skip_si_consensus_et_donnees_completes():
    ctx = EnrichmentContext()
    ctx.bpm_ballot.add("reccobeats", 100)
    ctx.bpm_ballot.add("getsongbpm", 100)  # 2 candidats concordants = consensus
    assert SongBpmProvider().gate(_track_complet(), ctx) == "not_needed"


def test_gate_execute_sans_consensus():
    ctx = EnrichmentContext()
    ctx.bpm_ballot.add("reccobeats", 100)  # 1 seul candidat : pas de consensus
    assert SongBpmProvider().gate(_track_complet(), ctx) is None


def test_gate_execute_si_donnee_manquante_malgre_consensus():
    ctx = EnrichmentContext()
    ctx.bpm_ballot.add("reccobeats", 100)
    ctx.bpm_ballot.add("getsongbpm", 100)
    track = _track_complet()
    track.duration = None
    assert SongBpmProvider().gate(track, ctx) is None


def test_gate_execute_si_force_update():
    ctx = EnrichmentContext(force_update=True)
    ctx.bpm_ballot.add("reccobeats", 100)
    ctx.bpm_ballot.add("getsongbpm", 100)
    assert SongBpmProvider().gate(_track_complet(), ctx) is None


def test_error_result_none_pour_crash():
    # Crash/timeout ≠ « pas de données » : la valeur d'erreur est None, pas False
    assert SongBpmProvider.error_result is None


def test_spotify_id_de_songbpm_rejete_si_duplicata():
    # Un validateur qui refuse tout : l'ID trouvé par SongBPM n'est PAS posé
    data = {"spotify_id": "dup123"}
    provider = SongBpmProvider(_FakeScraper(data))
    track = _track()
    other = Track(title="Autre", artist=track.artist)
    ctx = EnrichmentContext(
        artist_tracks=[other],
        validate_spotify_id_unique=lambda sid, t, tracks: False,
    )
    provider.enrich(track, ctx)
    assert track.spotify_id is None


def test_indisponible_renvoie_false():
    assert SongBpmProvider(None).enrich(_track(), EnrichmentContext()) is False


class TestVoieSyncResiduelle:
    def test_spotify_id_de_songbpm_accepte_si_unique(self):
        scraper = _FakeScraper({"spotify_id": "SP_NEW"})
        track = _track()
        ctx = EnrichmentContext(
            artist_tracks=[_track()], validate_spotify_id_unique=lambda *_: True
        )
        assert SongBpmProvider(scraper).enrich(track, ctx) is True
        assert track.spotify_id == "SP_NEW"

    def test_timeout_du_timer_fait_jeter_le_resultat(self, monkeypatch):
        """Le Timer n'interrompt rien (l'API sync de Playwright n'est pas
        thread-safe) : il marque le résultat comme trop tardif, et c'est le
        provider qui le jette. Sans ce test, la branche restait muette."""
        import threading

        class _TimerImmediat:
            def __init__(self, delai, fn):
                self._fn = fn

            def start(self):
                self._fn()

            def cancel(self):
                pass

        monkeypatch.setattr(threading, "Timer", _TimerImmediat)
        provider = SongBpmProvider(_FakeScraper({"bpm": 90}))
        ctx = EnrichmentContext()
        assert provider.enrich(_track(), ctx) is False
        assert ctx.bpm_ballot.candidates == []  # rien n'entre au scrutin

    @pytest.mark.parametrize(
        "exc", [TimeoutError("30s"), PlaywrightError("page morte"), KeyError("k"), ValueError("v")]
    )
    def test_frontieres_du_scraper_sync(self, exc):
        class _Leve(_FakeScraper):
            def search_track(self, *a, **k):
                raise exc

        assert SongBpmProvider(_Leve({})).enrich(_track(), EnrichmentContext()) is False

    def test_fermetures(self):
        scraper = _FakeScraperAsync({})
        scraper.close = lambda: setattr(scraper, "ferme_sync", True)
        provider = SongBpmProvider(scraper_factory=lambda: scraper)
        provider._resource.get()
        provider.close()
        assert scraper.ferme_sync is True

        provider_async = SongBpmProvider(async_scraper_factory=lambda: scraper)
        provider_async._async_resource.get()
        asyncio.run(provider_async.aclose())
        assert scraper.ferme is True


class TestVoieAsync:
    """F3c : le scraper async remplace le `threading.Timer` par
    `asyncio.timeout`. Différence de fond — le Timer laissait la recherche
    finir et jetait le résultat, l'annulation asyncio l'INTERROMPT et recycle
    le driver. Les deux voies partagent `_apply_track_data`."""

    def _provider(self, scraper):
        return SongBpmProvider(async_scraper_factory=lambda: scraper)

    def test_memes_donnees_que_la_voie_sync(self):
        data = {"bpm": 90, "key": 5, "mode": 1, "duration": 200}
        t_sync, t_async = _track(), _track()
        ctx_sync, ctx_async = EnrichmentContext(), EnrichmentContext()

        assert SongBpmProvider(_FakeScraper(data)).enrich(t_sync, ctx_sync) is True
        assert (
            asyncio.run(self._provider(_FakeScraperAsync(data)).enrich_async(t_async, ctx_async))
            is True
        )
        assert t_sync.duration == t_async.duration
        assert ctx_sync.bpm_ballot.candidates == ctx_async.bpm_ballot.candidates

    def test_sans_variante_async_repli_sur_le_pont_sync(self):
        """Aucune fabrique async configurée (tests, compat) : le corps SYNC est
        exécuté sur le thread dédié du run, pas court-circuité."""
        provider = SongBpmProvider(_FakeScraper({"bpm": 90}))
        ctx = EnrichmentContext(sync_runner=_RunnerSync())
        assert asyncio.run(provider.enrich_async(_track(), ctx)) is True
        assert ("songbpm", 90) in ctx.bpm_ballot.candidates

    def test_timeout_annule_la_recherche_et_recycle_le_driver(self, monkeypatch):
        """Budget ramené à 0 s pour ne pas attendre 30 s : ce qui est vérifié,
        c'est que le scraper est FERMÉ (recyclé) et l'issue False."""
        scraper = _FakeScraperAsync({}, lent=True)
        vrai_timeout = asyncio.timeout
        monkeypatch.setattr(asyncio, "timeout", lambda _s: vrai_timeout(0))
        assert asyncio.run(self._provider(scraper).enrich_async(_track(), EnrichmentContext())) is (
            False
        )
        assert scraper.ferme is True

    def test_resultat_vide(self):
        p = self._provider(_FakeScraperAsync(None))
        assert asyncio.run(p.enrich_async(_track(), EnrichmentContext())) is False

    @pytest.mark.parametrize(
        "exc", [PlaywrightError("page morte"), KeyError("k"), TypeError("t"), ValueError("v")]
    )
    def test_frontiere_scraper_journalisee(self, exc):
        p = self._provider(_FakeScraperAsync(exc))
        assert asyncio.run(p.enrich_async(_track(), EnrichmentContext())) is False

    def test_artiste_principal_et_spotify_id_valide_transmis(self):
        scraper = _FakeScraperAsync({"bpm": 90})
        track = _track()
        track.is_featuring = True
        track.primary_artist_name = "Principal"
        track.spotify_id = "SP1"
        asyncio.run(self._provider(scraper).enrich_async(track, EnrichmentContext()))
        titre, artiste, spotify_id, details = scraper.calls[0]
        assert (artiste, spotify_id, details) == ("Principal", "SP1", True)

    def test_spotify_id_duplicata_non_transmis_a_la_recherche(self):
        """Même règle que la voie sync : un ID déjà porté par un autre morceau
        n'est pas une clé de recherche fiable."""
        scraper = _FakeScraperAsync({"bpm": 90})
        track = _track()
        track.spotify_id = "SP1"
        ctx = EnrichmentContext(
            artist_tracks=[_track()], validate_spotify_id_unique=lambda *_: False
        )
        asyncio.run(self._provider(scraper).enrich_async(track, ctx))
        assert scraper.calls[0][2] is None
