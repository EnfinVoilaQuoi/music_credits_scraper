"""Tests du provider Spotify ID (src/enrichment/providers/spotify_id) — sans réseau."""

import asyncio

import pytest
from playwright.async_api import Error as PlaywrightError

from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.spotify_id import SpotifyIdProvider
from src.models.artist import Artist
from src.models.track import Track


class _FakeScraper:
    def __init__(self, spotify_id=None, page_title=None, titre_leve=None, identite=None):
        self._id = spotify_id
        self._title = page_title
        self._titre_leve = titre_leve
        self._identite = identite
        self.calls = []

    def get_spotify_id(self, artist, title):
        self.calls.append((artist, title))
        return self._id

    def get_spotify_page_title(self, spotify_id):
        if self._titre_leve:
            raise self._titre_leve
        return self._title

    def get_track_identity(self, spotify_id):
        """Oracle d'identité. `None` = « on ne conclut pas » (défaut hermétique)."""
        return self._identite


class _FakeScraperAsync(_FakeScraper):
    async def get_spotify_id_async(self, artist, title):
        self.calls.append((artist, title))
        return self._id

    async def get_spotify_page_title_async(self, spotify_id):
        if self._titre_leve:
            raise self._titre_leve
        return self._title

    async def get_track_identity_async(self, spotify_id):
        """Miroir async de l'oracle d'identité."""
        return self._identite


class _RunnerSync:
    """Faux `sync_runner` : c'est le SerialWorker du run, sur son thread dédié."""

    async def run(self, fn, *args):
        return fn(*args)


def _track():
    return Track(title="Solo", artist=Artist(name="Sofiane Pamart"))


def test_is_available():
    assert SpotifyIdProvider(None).is_available() is False
    assert SpotifyIdProvider(_FakeScraper()).is_available() is True


def test_enrich_pose_id_et_titre_page():
    scraper = _FakeScraper(spotify_id="abc123", page_title="Solo - Sofiane Pamart")
    provider = SpotifyIdProvider(scraper)
    track = _track()
    ctx = EnrichmentContext(validate_spotify_id_unique=lambda *a: True)
    assert provider.enrich(track, ctx) is True
    assert track.spotify_id == "abc123"
    assert track.spotify_page_title == "Solo - Sofiane Pamart"


def test_enrich_echec_si_scraper_vide():
    provider = SpotifyIdProvider(_FakeScraper(spotify_id=None))
    assert provider.enrich(_track(), EnrichmentContext()) is False


def test_id_rejete_si_duplicata():
    scraper = _FakeScraper(spotify_id="dup")
    provider = SpotifyIdProvider(scraper)
    track = _track()
    ctx = EnrichmentContext(
        artist_tracks=[Track(title="Autre")],
        validate_spotify_id_unique=lambda sid, t, tracks: False,
    )
    assert provider.enrich(track, ctx) is False
    assert track.spotify_id is None


def test_get_unique_reutilise_id_existant_valide_sans_force():
    provider = SpotifyIdProvider(_FakeScraper(spotify_id="scraped"))
    track = _track()
    track.spotify_id = "existant"
    ctx = EnrichmentContext(validate_spotify_id_unique=lambda *a: True)
    # force_scraper=False → l'ID existant validé est réutilisé, pas de scrape
    assert provider.get_unique_spotify_id(track, ctx, force_scraper=False) == "existant"


def test_indisponible_renvoie_none():
    assert SpotifyIdProvider(None).get_unique_spotify_id(_track(), EnrichmentContext()) is None


# ──────────────────────────────────────────────────────────────────────
# gate() — gating historique du bloc « scraper Spotify ID » d'enrich_track
# ──────────────────────────────────────────────────────────────────────


def test_gate_execute_si_pas_d_id():
    assert SpotifyIdProvider().gate(_track(), EnrichmentContext()) is None


def test_gate_skip_si_id_valide_sans_force_update():
    track = _track()
    track.spotify_id = "existant"
    ctx = EnrichmentContext(validate_spotify_id_unique=lambda *a: True)
    assert SpotifyIdProvider().gate(track, ctx) == "not_needed"


def test_gate_execute_si_force_update_malgre_id_valide():
    track = _track()
    track.spotify_id = "existant"
    ctx = EnrichmentContext(force_update=True, validate_spotify_id_unique=lambda *a: True)
    assert SpotifyIdProvider().gate(track, ctx) is None


def test_gate_execute_si_id_duplique():
    track = _track()
    track.spotify_id = "dup"
    ctx = EnrichmentContext(
        artist_tracks=[Track(title="Autre")],
        validate_spotify_id_unique=lambda *a: False,
    )
    assert SpotifyIdProvider().gate(track, ctx) is None


def test_gate_skip_si_voie_isrc_satisfaite():
    # L'ISRC a fourni les données audio → le scrape Spotify devient inutile
    ctx = EnrichmentContext(force_update=True, isrc_satisfied=True)
    assert SpotifyIdProvider().gate(_track(), ctx) == "not_needed"


# ──────────────────────────────────────────────────────────────────────
# La datation du constat (colonne e17) et le jumeau async
# ──────────────────────────────────────────────────────────────────────


class TestDatationDuConstat:
    """`spotify_id_checked_at` sépare « absent de Spotify » de « jamais
    cherché ». Elle doit être posée dès que la recherche est MENÉE À TERME,
    trouvée ou non — et par les DEUX voies : seule la sync la posait, alors que
    l'app emprunte l'async (`data_enricher` fournit une `async_scraper_factory`).
    """

    @pytest.mark.parametrize("trouve", [True, False])
    def test_voie_sync(self, trouve):
        provider = SpotifyIdProvider(_FakeScraper(spotify_id="abc" if trouve else None))
        track = _track()
        ctx = EnrichmentContext(validate_spotify_id_unique=lambda *a: True)
        provider.enrich(track, ctx)
        assert track.spotify_id_checked_at

    @pytest.mark.parametrize("trouve", [True, False])
    def test_voie_async(self, trouve):
        scraper = _FakeScraperAsync(spotify_id="abc" if trouve else None)
        provider = SpotifyIdProvider(async_scraper_factory=lambda: scraper)
        track = _track()
        ctx = EnrichmentContext(validate_spotify_id_unique=lambda *a: True)
        asyncio.run(provider.enrich_async(track, ctx))
        assert track.spotify_id_checked_at, "la voie async ne datait pas le constat"

    def test_datee_meme_quand_l_id_trouve_est_un_duplicata(self):
        """Le constat est daté avant l'arbitrage d'unicité : la recherche a bien
        eu lieu, même si son résultat est rejeté."""
        scraper = _FakeScraperAsync(spotify_id="dup")
        provider = SpotifyIdProvider(async_scraper_factory=lambda: scraper)
        track = _track()
        ctx = EnrichmentContext(
            artist_tracks=[Track(title="Autre")], validate_spotify_id_unique=lambda *a: False
        )
        assert asyncio.run(provider.enrich_async(track, ctx)) is False
        assert track.spotify_id is None
        assert track.spotify_id_checked_at

    def test_non_datee_si_le_scraper_est_indisponible(self):
        """Rien n'a été mené à terme : dater ici ferait passer un morceau JAMAIS
        cherché pour absent de Spotify."""
        track = _track()
        SpotifyIdProvider(None).get_unique_spotify_id(track, EnrichmentContext())
        assert track.spotify_id_checked_at is None


class TestVoieAsync:
    def test_pose_id_et_titre_de_page(self):
        scraper = _FakeScraperAsync(spotify_id="abc123", page_title="Solo - Sofiane Pamart")
        provider = SpotifyIdProvider(async_scraper_factory=lambda: scraper)
        track = _track()
        ctx = EnrichmentContext(validate_spotify_id_unique=lambda *a: True)
        assert asyncio.run(provider.enrich_async(track, ctx)) is True
        assert (track.spotify_id, track.spotify_page_title) == ("abc123", "Solo - Sofiane Pamart")

    def test_sans_variante_async_repli_sur_le_pont_sync(self):
        provider = SpotifyIdProvider(_FakeScraper(spotify_id="abc123"))
        ctx = EnrichmentContext(
            validate_spotify_id_unique=lambda *a: True, sync_runner=_RunnerSync()
        )
        track = _track()
        assert asyncio.run(provider.enrich_async(track, ctx)) is True
        assert track.spotify_id == "abc123"

    def test_titre_de_page_illisible_n_annule_pas_l_id(self):
        """Le titre n'est qu'une aide à la vérification visuelle : son échec ne
        doit pas faire perdre l'ID, qui est la donnée utile."""
        scraper = _FakeScraperAsync(spotify_id="abc123", titre_leve=PlaywrightError("page morte"))
        provider = SpotifyIdProvider(async_scraper_factory=lambda: scraper)
        track = _track()
        ctx = EnrichmentContext(validate_spotify_id_unique=lambda *a: True)
        assert asyncio.run(provider.enrich_async(track, ctx)) is True
        assert track.spotify_id == "abc123"

    def test_aucun_id_trouve(self):
        provider = SpotifyIdProvider(async_scraper_factory=lambda: _FakeScraperAsync())
        assert asyncio.run(provider.enrich_async(_track(), EnrichmentContext())) is False


def test_titre_de_page_illisible_n_annule_pas_l_id_en_sync():
    scraper = _FakeScraper(spotify_id="abc123", titre_leve=PlaywrightError("page morte"))
    provider = SpotifyIdProvider(scraper)
    track = _track()
    ctx = EnrichmentContext(validate_spotify_id_unique=lambda *a: True)
    assert provider.enrich(track, ctx) is True
    assert track.spotify_id == "abc123"


def test_id_existant_duplique_relance_une_recherche():
    """Sans `force_scraper`, un ID existant INVALIDE ne bloque pas : on cherche
    un remplaçant plutôt que de garder un doublon."""
    scraper = _FakeScraper(spotify_id="neuf")
    provider = SpotifyIdProvider(scraper)
    track = _track()
    track.spotify_id = "dup"
    ctx = EnrichmentContext(
        artist_tracks=[Track(title="Autre")],
        validate_spotify_id_unique=lambda sid, t, tracks: sid == "neuf",
    )
    assert provider.get_unique_spotify_id(track, ctx, force_scraper=False) == "neuf"


def test_fermetures():
    scraper = _FakeScraperAsync()
    scraper.close = lambda: setattr(scraper, "ferme_sync", True)

    async def _aclose():
        scraper.ferme_async = True

    scraper.aclose = _aclose

    provider = SpotifyIdProvider(scraper_factory=lambda: scraper)
    assert provider.scraper is scraper  # point d'emprunt (ReccoBeats)
    provider.close()
    assert scraper.ferme_sync is True

    provider_async = SpotifyIdProvider(async_scraper_factory=lambda: scraper)
    assert provider_async.async_scraper is scraper
    asyncio.run(provider_async.aclose())
    assert scraper.ferme_async is True
