"""Tests du provider BPM Finder (src/enrichment/providers/bpmfinder) — sans réseau.

Scraper mocké. On évite la recherche YouTube en fournissant un youtube_url au
track (le provider ne cherche un lien que s'il en manque un).
"""

import asyncio

from src.concurrency.serial_worker import SerialWorker
from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.bpmfinder import BpmFinderProvider
from src.models.artist import Artist
from src.models.track import Track


class _FakeScraper:
    def __init__(self, result=None, failure_reason=None):
        self._result = result
        self.last_failure_reason = failure_reason
        self.calls = 0

    def analyze(self, url):
        self.calls += 1
        return self._result


class _FakeAsyncScraper:
    def __init__(self, result=None, failure_reason=None):
        self._result = result
        self.last_failure_reason = failure_reason
        self.calls = 0

    async def analyze_async(self, url):
        self.calls += 1
        return self._result


def _track(bpm=None, yt="https://youtu.be/abc"):
    t = Track(title="Solo", artist=Artist(name="X"))
    t.audio.bpm = bpm
    t.youtube_url = yt
    return t


def test_is_available():
    assert BpmFinderProvider(None).is_available() is False
    assert BpmFinderProvider(_FakeScraper()).is_available() is True


def test_not_needed_si_rien_manquant():
    provider = BpmFinderProvider(_FakeScraper({"bpm": 140}))
    track = _track(bpm=120)
    track.audio.key = 5
    track.audio.mode = 1
    assert provider.enrich(track, EnrichmentContext(force_update=False)) == "not_needed"


def test_bpm_manquant_devient_candidat():
    scraper = _FakeScraper({"bpm": 140})
    provider = BpmFinderProvider(scraper)
    track = _track(bpm=None)  # bpm manquant → analyse
    track.audio.key = 5  # key/mode présents → seul le bpm manque
    track.audio.mode = 1
    ctx = EnrichmentContext()
    assert provider.enrich(track, ctx) is True
    # E7 : BpmFinder alimente le scrutin comme toute source (plus de pose directe)
    assert ("bpmfinder", 140) in ctx.bpm_ballot.candidates


def test_emet_bpm_et_observations_key_mode():
    # FIX persistance E7 : BpmFinder alimente le MOTEUR (scrutin + observations
    # PAR SOURCE) — seul canal persisté depuis le drop des colonnes audio (e12).
    scraper = _FakeScraper({"bpm": 140, "key": 5, "mode": 0})
    provider = BpmFinderProvider(scraper)
    track = _track(bpm=None)
    track.audio.key = None
    track.audio.mode = None
    ctx = EnrichmentContext()
    assert provider.enrich(track, ctx) is True
    assert ("bpmfinder", 140) in ctx.bpm_ballot.candidates
    keys = [o for o in ctx.observations if o.field == "key" and o.source == "bpmfinder"]
    modes = [o for o in ctx.observations if o.field == "mode" and o.source == "bpmfinder"]
    assert keys and keys[0].value == 5
    assert modes and modes[0].value == 0


def test_disjoncteur_sur_timeout_puis_skipped():
    scraper = _FakeScraper(None, failure_reason="timeout")
    provider = BpmFinderProvider(scraper)
    ctx = EnrichmentContext()
    for _ in range(3):
        assert provider.enrich(_track(bpm=None), ctx) is False
    # 3 timeouts consécutifs → disjoncteur ouvert
    assert provider.enrich(_track(bpm=None), ctx) == "skipped"
    assert scraper.calls == 3  # le 4ᵉ appel n'atteint pas le scraper


def test_refus_backend_ne_declenche_pas_le_disjoncteur():
    scraper = _FakeScraper(None, failure_reason="backend")
    provider = BpmFinderProvider(scraper)
    ctx = EnrichmentContext()
    for _ in range(5):
        assert provider.enrich(_track(bpm=None), ctx) is False
    assert scraper.calls == 5  # jamais coupé : le site répondait (4xx/5xx)


def test_reset_breaker_rearme():
    scraper = _FakeScraper(None, failure_reason="timeout")
    provider = BpmFinderProvider(scraper)
    ctx = EnrichmentContext()
    for _ in range(3):
        provider.enrich(_track(bpm=None), ctx)
    assert provider.enrich(_track(bpm=None), ctx) == "skipped"
    provider.reset_breaker()
    assert provider.enrich(_track(bpm=None), ctx) is False  # ré-armé → retente


# ──────────────────────────────────────────────────────────────────────
# enrich_async (F3d) — repli sync par défaut, voie native si factory fournie
# ──────────────────────────────────────────────────────────────────────


def test_enrich_async_repli_sync_sans_factory():
    # Sans async_scraper_factory : enrich_async passe par le PONT SYNC (scraper
    # sync via ctx.sync_runner) — comportement livré tant que l'async n'est pas
    # activé.
    scraper = _FakeScraper({"bpm": 140})
    provider = BpmFinderProvider(scraper)
    track = _track(bpm=None)
    track.audio.key = None
    track.audio.mode = None
    runner = SerialWorker("test-bpmfinder")
    ctx = EnrichmentContext(sync_runner=runner)
    try:
        result = asyncio.run(provider.enrich_async(track, ctx))
    finally:
        runner.shutdown()
    assert result is True
    assert scraper.calls == 1  # le scraper SYNC a été utilisé (pont)
    assert ("bpmfinder", 140) in ctx.bpm_ballot.candidates


def test_enrich_async_natif_avec_factory():
    # Avec async_scraper_factory : enrich_async utilise analyze_async (voie native),
    # jamais le scraper sync.
    async_scraper = _FakeAsyncScraper({"bpm": 140, "key": 5, "mode": 0})
    sync_scraper = _FakeScraper({"bpm": 999})  # ne doit PAS être appelé
    provider = BpmFinderProvider(scraper=sync_scraper, async_scraper_factory=lambda: async_scraper)
    track = _track(bpm=None)
    track.audio.key = None
    track.audio.mode = None
    runner = SerialWorker("test-bpmfinder")
    ctx = EnrichmentContext(sync_runner=runner)
    try:
        result = asyncio.run(provider.enrich_async(track, ctx))
    finally:
        runner.shutdown()
    assert result is True
    assert async_scraper.calls == 1  # voie native
    assert sync_scraper.calls == 0  # pas de pont sync
    assert ("bpmfinder", 140) in ctx.bpm_ballot.candidates
    keys = [o for o in ctx.observations if o.field == "key" and o.source == "bpmfinder"]
    modes = [o for o in ctx.observations if o.field == "mode" and o.source == "bpmfinder"]
    assert keys and keys[0].value == 5
    assert modes and modes[0].value == 0


# ──────────────────────────────────────────────────────────────────────
# La recherche automatique de lien YouTube
# ──────────────────────────────────────────────────────────────────────


class _FauxChercheurYT:
    """Faux `YouTubeSearcher` (le vrai fait du réseau)."""

    def __init__(self, resultats):
        self._resultats = resultats
        self.appels = []

    def search_track(self, artist, title, max_results=5):
        self.appels.append((artist, title))
        return self._resultats


def _provider_avec_chercheur(scraper, resultats):
    provider = BpmFinderProvider(scraper)
    provider._yt_searcher = _FauxChercheurYT(resultats)
    return provider


class TestRechercheDeLienYouTube:
    """Un mauvais lien = un mauvais BPM en base : au-dessous du seuil de
    confiance, le provider refuse de deviner plutôt que de risquer la donnée.

    Sans lien exploitable l'issue est `not_needed`, jamais `skipped` : il n'y a
    rien à analyser, et cela ne doit pas compter comme une tentative ratée
    (`skipped` est réservé au disjoncteur).

    (Le chercheur lui-même est stubé : ce test porte sur le CONTRAT du provider,
    pas sur la façon dont les liens sont trouvés — celle-ci relève de la revue
    globale de l'intégration YouTube, encore à mener.)"""

    def test_lien_sur_du_persiste(self):
        p = _provider_avec_chercheur(
            _FakeScraper({"bpm": 90}),
            [{"url": "https://youtu.be/ok", "relevance_score": 0.99}],
        )
        track = _track(yt=None)
        assert p.enrich(track, EnrichmentContext()) is True
        assert track.youtube_url == "https://youtu.be/ok"
        assert track.youtube_url_source == "search_auto"

    def test_lien_incertain_non_persiste(self):
        p = _provider_avec_chercheur(
            _FakeScraper({"bpm": 90}),
            [{"url": "https://youtu.be/doute", "relevance_score": 0.10}],
        )
        track = _track(yt=None)
        assert p.enrich(track, EnrichmentContext()) == "not_needed"
        assert track.youtube_url is None

    def test_url_de_recherche_refusee(self):
        """Une page de RÉSULTATS n'est pas une vidéo : elle n'est pas analysable."""
        p = _provider_avec_chercheur(
            _FakeScraper({"bpm": 90}),
            [
                {
                    "url": "https://youtube.com/results?q=x",
                    "relevance_score": 0.99,
                    "is_search_url": True,
                }
            ],
        )
        track = _track(yt=None)
        assert p.enrich(track, EnrichmentContext()) == "not_needed"
        assert track.youtube_url is None

    def test_aucun_resultat(self):
        p = _provider_avec_chercheur(_FakeScraper({"bpm": 90}), [])
        assert p.enrich(_track(yt=None), EnrichmentContext()) == "not_needed"

    def test_chercheur_en_panne_ne_fait_pas_tomber_le_provider(self):
        """Lien auxiliaire best-effort : son échec dégrade, il n'interrompt pas."""

        class _Casse(_FauxChercheurYT):
            def search_track(self, artist, title, max_results=5):
                raise RuntimeError("recherche indisponible")

        p = BpmFinderProvider(_FakeScraper({"bpm": 90}))
        p._yt_searcher = _Casse([])
        assert p.enrich(_track(yt=None), EnrichmentContext()) == "not_needed"

    def test_artiste_principal_utilise_si_featuring(self):
        p = _provider_avec_chercheur(
            _FakeScraper({"bpm": 90}),
            [{"url": "https://youtu.be/ok", "relevance_score": 0.99}],
        )
        track = _track(yt=None)
        track.is_featuring = True
        track.primary_artist_name = "Principal"
        p.enrich(track, EnrichmentContext())
        assert p._yt_searcher.appels[0][0] == "Principal"


def test_scraper_increable_compte_comme_crash_pas_comme_echec():
    """`None` (crash source) ≠ `False` (pas de données) : seul le second entre
    dans le « tout a échoué » qui déclenche le nettoyage du morceau."""
    provider = BpmFinderProvider(scraper_factory=lambda: None)
    assert provider.enrich(_track(), EnrichmentContext()) is None


def test_crash_de_l_analyse_est_trace_et_compte_pour_le_disjoncteur(caplog):
    class _Casse(_FakeScraper):
        def analyze(self, url):
            raise RuntimeError("navigateur mort")

    provider = BpmFinderProvider(_Casse())
    with caplog.at_level("ERROR"):
        assert provider.enrich(_track(), EnrichmentContext()) is None
    assert provider._fail_streak == 1
    assert [r for r in caplog.records if r.exc_info], "traceback attendu"


def test_crash_de_l_analyse_async_aussi(caplog):
    """Jumeau async : même comptage pour le disjoncteur, même traceback."""

    class _Casse(_FakeAsyncScraper):
        async def analyze_async(self, url):
            raise RuntimeError("navigateur mort")

    class _Runner:
        async def run(self, fn, *args):
            return fn(*args)

    provider = BpmFinderProvider(async_scraper_factory=lambda: _Casse())
    ctx = EnrichmentContext(sync_runner=_Runner())
    with caplog.at_level("ERROR"):
        assert asyncio.run(provider.enrich_async(_track(), ctx)) is None
    assert provider._fail_streak == 1
    assert [r for r in caplog.records if r.exc_info], "traceback attendu"


def test_gate_ne_skip_jamais_le_gating_vit_dans_enrich():
    assert BpmFinderProvider().gate(_track(), EnrichmentContext()) is None


def test_emprunt_et_fermetures():
    scraper = _FakeScraper()
    scraper.close = lambda: setattr(scraper, "ferme", True)
    provider = BpmFinderProvider(scraper_factory=lambda: scraper)
    assert provider.scraper is scraper  # point d'emprunt (saisie manuelle GUI)
    provider.close()
    assert scraper.ferme is True


def test_aclose_sans_variante_async_est_un_no_op():
    """Aucune factory async fournie → rien n'a été créé, rien à fermer."""
    asyncio.run(BpmFinderProvider(_FakeScraper()).aclose())
