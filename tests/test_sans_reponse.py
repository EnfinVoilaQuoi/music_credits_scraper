"""Absent ≠ sans réponse (2026-09-28).

Les clients rendaient `None` sur une panne comme sur une absence ; l'appelant
DATAIT alors un faux constat — `spotify_id_checked_at` posé sur un navigateur
mort, soit un « pas sur Spotify » permanent. La distinction existait déjà dans
l'observabilité (`absent` vs `timeout/unreachable/crash/…`) : `exiger_reponse`
la relit à la sortie d'`observe()` et lève `SansReponse`, sans second verdict.

Au passage : le jumeau ASYNC du scraper Spotify ID (celui que l'app emprunte)
n'avait ni le plancher de pertinence ni le filtre du choix LLM — la fin de
recherche est désormais PARTAGÉE, et testée ici sur les DEUX voies.
"""

import asyncio

import pytest

from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.spotify_id import SpotifyIdProvider
from src.models.artist import Artist
from src.models.track import Track
from src.observability import source_usage
from src.observability.issues import IssueKind, SansReponse, classify_exception
from src.scrapers.spotify_id_scraper_async import SpotifyIDScraperAsync
from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper

# ── Observabilité ────────────────────────────────────────────────────────────


class TestExigerReponse:
    def test_absence_constatee_ne_leve_rien(self):
        with source_usage.observe("deezer") as obs:
            obs.absent("aucun hit")
        source_usage.exiger_reponse(obs)  # ne lève pas

    def test_echec_de_transport_leve_avec_son_verdict(self):
        with source_usage.observe("deezer") as obs:
            source_usage.record_attempt("deezer", IssueKind.THROTTLED, detail="quota")
            obs.absent("aucun hit")
        with pytest.raises(SansReponse) as e:
            source_usage.exiger_reponse(obs)
        assert e.value.kind == IssueKind.THROTTLED and e.value.source == "deezer"

    def test_aucun_signal_ne_vaut_pas_absence(self):
        """`indeterminate` : on ne conclut pas, donc on ne date rien."""
        with source_usage.observe("deezer") as obs:
            pass
        with pytest.raises(SansReponse) as e:
            source_usage.exiger_reponse(obs)
        assert e.value.kind == IssueKind.INDETERMINATE

    def test_imbriquee_le_verdict_provisoire_suffit(self):
        """Réentrance : l'observation interne EST l'externe, pas encore résolue."""
        with source_usage.observe("deezer") as externe:
            with source_usage.observe("deezer") as interne:
                interne.fail(IssueKind.TIMEOUT, "délai")
            with pytest.raises(SansReponse):
                source_usage.exiger_reponse(interne)
        assert externe is interne

    def test_a_repondu_se_consulte_dans_l_observation(self):
        """Pour les caches négatifs (ReccoBeats, Musixmatch), avant la sortie."""
        with source_usage.observe("reccobeats") as obs:
            source_usage.record_attempt("reccobeats", IssueKind.OK)
            obs.absent("inconnu")
            assert source_usage.a_repondu(obs)
            source_usage.record_attempt("reccobeats", IssueKind.TIMEOUT)
            assert not source_usage.a_repondu(obs)

    def test_une_SansReponse_garde_son_verdict_dans_l_observation_externe(self):
        """Classée CRASH (classe inconnue), un 429 deviendrait un process cassé."""
        assert classify_exception(SansReponse("x", IssueKind.THROTTLED)) == IssueKind.THROTTLED


# ── Scraper Spotify ID : les DEUX jumeaux ────────────────────────────────────

_BON = "4uLU6hMCjMI75M1A2tKUQC"


class _Lien:
    def __init__(self, texte):
        self._texte = texte

    def get_attribute(self, _):
        return f"/track/{_BON}"

    def inner_text(self):
        return self._texte

    def query_selector(self, _):
        return None


class _Page:
    """Page de recherche factice : `texte=None` ⇒ aucun lien de piste rendu."""

    def __init__(self, texte):
        self.texte = texte

    def goto(self, *a, **k):
        return None

    def wait_for_selector(self, *a, **k):
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        if self.texte is None:
            raise PlaywrightTimeoutError("rien")

    def query_selector_all(self, _):
        return [_Lien(self.texte)]


class _LienAsync(_Lien):
    async def get_attribute(self, _):
        return f"/track/{_BON}"

    async def inner_text(self):
        return self._texte

    async def query_selector(self, _):
        return None


class _PageAsync(_Page):
    async def goto(self, *a, **k):
        return None

    async def wait_for_selector(self, *a, **k):
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        if self.texte is None:
            raise PlaywrightTimeoutError("rien")

    async def query_selector_all(self, _):
        return [_LienAsync(self.texte)]


def _sync(tmp_path, texte):
    s = SpotifyIDScraper(cache_file=str(tmp_path / "c.json"), headless=True)
    s._ensure_driver = lambda: None
    s._handle_cookies = lambda: None
    s.page = _Page(texte)
    return s, lambda: s.get_spotify_id("Isha", "Durag")


def _async(tmp_path, texte):
    s = SpotifyIDScraperAsync(cache_file=str(tmp_path / "c.json"), headless=True)

    async def _rien():
        return None

    s._ensure_driver_async = _rien
    s._handle_cookies_async = _rien
    s.page = _PageAsync(texte)
    return s, lambda: asyncio.run(s.get_spotify_id_async("Isha", "Durag"))


@pytest.fixture(params=[_sync, _async], ids=["sync", "async"])
def jumeau(request, tmp_path):
    return lambda texte: request.param(tmp_path, texte)


class TestFinDeRecherchePartagee:
    def test_candidat_pertinent_retenu(self, jumeau):
        s, chercher = jumeau("durag isha")
        assert chercher() == _BON
        assert s.cache["isha::durag"] == _BON

    def test_plancher_de_pertinence_sur_les_deux_voies(self, jumeau):
        """Le jumeau async rendait `found_tracks[0]` quel que soit son score."""
        s, chercher = jumeau("une autre chanson de quelqu'un d'autre")
        assert chercher() is None  # absence CONSTATÉE : aucune exception
        assert s.cache["isha::durag"] == "not_found"

    def test_aucune_page_rendue_n_est_pas_une_absence(self, jumeau):
        s, chercher = jumeau(None)
        with pytest.raises(SansReponse) as e:
            chercher()
        assert e.value.kind == IssueKind.TIMEOUT
        assert "isha::durag" not in s.cache


# ── Provider : rien daté sans réponse ────────────────────────────────────────


class _ScraperMuet:
    def get_spotify_id(self, artist, title):
        raise SansReponse("spotify_embed", IssueKind.CRASH, "browser mort")

    async def get_spotify_id_async(self, artist, title):
        raise SansReponse("spotify_embed", IssueKind.CRASH, "browser mort")


def _track():
    return Track(title="Durag", artist=Artist(name="Isha"))


def test_provider_sync_ne_date_pas_une_panne():
    track = _track()
    assert (
        SpotifyIdProvider(_ScraperMuet()).get_unique_spotify_id(
            track, EnrichmentContext(), force_scraper=True
        )
        is None
    )
    assert track.spotify_id_checked_at is None


def test_provider_async_ne_date_pas_une_panne():
    track = _track()
    provider = SpotifyIdProvider(async_scraper_factory=_ScraperMuet)
    assert asyncio.run(provider.enrich_async(track, EnrichmentContext())) is False
    assert track.spotify_id_checked_at is None


def test_genius_200_sans_fiche_n_est_pas_un_morceau_sans_album(monkeypatch):
    """`{}` serait mémorisé « sans album » 180 jours par `tracklists_genius`."""
    from types import SimpleNamespace

    from src.api import genius_api
    from src.api.genius_api import GeniusAPI

    reponse = SimpleNamespace(
        status_code=200, raise_for_status=lambda: None, json=lambda: {"response": {}}
    )
    monkeypatch.setattr(genius_api.source_usage, "requests_get", lambda *a, **k: reponse)
    assert GeniusAPI.album_du_morceau(object.__new__(GeniusAPI), 1) is None


def test_oublier_constat_spotify_soeurs_comprises_jamais_avec_un_id(data_manager):
    """Colonne PARTAGÉE : restée chez une sœur, la synchronisation la recopierait."""
    ids = []
    for nom in ("Swing", "Isha"):
        artist = Artist(name=nom)
        artist.id = data_manager.save_artist(artist)
        track = Track(title="Grünt #33", artist=artist)
        track.genius_id = 4242
        track.spotify_id_checked_at = "2026-09-18T10:00:00"
        data_manager.save_track(track)
        ids.append(track.id)
    autre = Track(title="Avec ID", artist=artist)
    autre.spotify_id = _BON
    autre.spotify_id_checked_at = "2026-09-18T10:00:00"
    data_manager.save_track(autre)

    assert data_manager.oublier_constat_spotify(ids[0])
    assert data_manager.oublier_constat_spotify(autre.id)

    from sqlalchemy import text

    with data_manager.engine.connect() as conn:
        dates = dict(conn.execute(text("SELECT id, spotify_id_checked_at FROM tracks")).all())
    assert dates[ids[0]] is None and dates[ids[1]] is None
    assert dates[autre.id] is not None  # un ID en place est un constat


# ── Deezer ───────────────────────────────────────────────────────────────────


class TestDeezer:
    def test_enveloppe_quota_est_un_echec_pas_une_absence(self):
        from src.api.deezer_api import DeezerAPI

        with source_usage.observe("deezer") as obs:
            assert DeezerAPI._payload_or_none({"error": {"code": 4, "type": "Exception"}}) is None
        assert [a.kind for a in obs.attempts] == [IssueKind.THROTTLED]

    def test_data_not_found_reste_une_absence(self):
        from src.api.deezer_api import DeezerAPI

        with source_usage.observe("deezer") as obs:
            DeezerAPI._payload_or_none({"error": {"code": 800, "type": "DataException"}})
        assert obs.attempts == []

    @pytest.mark.parametrize("voie", ["sync", "async"])
    def test_enrich_track_leve_sans_reponse(self, monkeypatch, voie):
        from src.api.deezer_api import DeezerAPI

        api = DeezerAPI()

        def _panne(*a, **k):
            source_usage.record_attempt("deezer", IssueKind.UNREACHABLE, detail="coupé")

        async def _panne_async(*a, **k):
            _panne()

        monkeypatch.setattr(api, "search_track", _panne)
        monkeypatch.setattr(api, "search_track_async", _panne_async)
        with pytest.raises(SansReponse):
            if voie == "sync":
                api.enrich_track("Isha", "Durag")
            else:
                asyncio.run(api.enrich_track_async(None, "Isha", "Durag"))

    def test_enrich_track_absence_rend_un_resultat(self, monkeypatch):
        from src.api.deezer_api import DeezerAPI

        api = DeezerAPI()

        def _vide(*a, **k):
            source_usage.record_attempt("deezer", IssueKind.OK)

        monkeypatch.setattr(api, "search_track", _vide)
        assert isinstance(api.enrich_track("Isha", "Durag"), dict)

    def test_provider_attrape_sans_reponse(self):
        from src.enrichment.providers.deezer import DeezerProvider

        class _Client:
            async def enrich_track_async(self, *a, **k):
                raise SansReponse("deezer", IssueKind.THROTTLED)

        ctx = EnrichmentContext()
        assert asyncio.run(DeezerProvider(_Client()).enrich_async(_track(), ctx)) is False
