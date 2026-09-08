"""Les allers-retours du scrape RIAA sont comptés (constat du 2026-09-08).

Un balayage global de vingt heures, 853 certifications ramenées, se déclarait
`indeterminate` avec **zéro tentative** dans le panneau de santé — là où BPI,
sur la même journée, en comptait 17 737. Le verdict n'était pas faux : c'est le
mécanisme « un trou de capteur se signale lui-même » qui parlait, faute du
moindre signal de transport.

La cause : les tentatives sont relevées par la session httpx partagée
(`async_http`) et par `crawl4ai_scraper_base`. RIAA pilote patchright, qui ne
traverse ni l'une ni l'autre.

Le piège de la correction — et ce que ces tests gardent — est le **carrier** :
une tentative se rattache à l'observation ouverte sur la Task asyncio courante,
sinon sur le thread. `observe()` est ouvert par `scrape_by_*` sur le thread
appelant, `_render_async` s'exécute sur la boucle applicative. Enregistrée
là-bas, la tentative ne trouverait pas son observation et l'observation
resterait `indeterminate` — le défaut d'origine, déguisé. D'où la collecte puis
le rejeu côté appelant, et d'où le test qui traverse VRAIMENT le pont.

Aucun réseau : `_render_async` est remplacé.
"""

import pytest

from src.concurrency import async_loop
from src.observability import source_usage as su
from src.observability.issues import IssueKind, is_failure
from src.scrapers.riaa_scraper_v2 import (
    PatchrightError,
    RIAAScraperV2,
    _Tentatives,
)

_LIGNE = (
    '<tr class="table_award_row" id="default_1">'
    '<td class="tw-artists_cell">UN ARTISTE</td>'
    '<td class="others_cell">UN TITRE</td>'
    '<td class="others_cell">March 1, 2020</td>'
    '<td class="format_cell">ALBUM</td>'
    '<td class="others_cell"><img class="tw-atom-badge" src="/x.png" '
    'alt="badge ST level 1"></td>'
    "</tr>"
)
_PAGE = f"<table>{_LIGNE}</table>"


@pytest.fixture(autouse=True)
def _propre():
    su.reset()
    su.set_sink(None)
    yield
    su.reset()
    su.set_sink(None)
    async_loop.shutdown(timeout=2.0)


def _scraper(monkeypatch, rendu):
    """Scraper dont `_render_async` est remplacé par `rendu(…, tentatives)`."""
    s = RIAAScraperV2(headless=True)
    monkeypatch.setattr(s, "_render_async", rendu)
    return s


class TestLeRunLaisseUneTrace:
    def test_un_balayage_reussi_compte_ses_allers_retours(self, monkeypatch):
        """Le symptôme rapporté : 2 appels, 0 tentative, verdict `indeterminate`."""

        async def rendu(url, load_all, get_details, tentatives):
            tentatives.note(IssueKind.OK, "goto", 120)
            for _ in range(3):
                tentatives.note(IssueKind.OK, "show-more", 40)
            return _PAGE

        s = _scraper(monkeypatch, rendu)
        assert s.scrape_by_date_range("2020-01-01", "2020-12-31")

        (verdict,) = su.flush()
        assert verdict.issue == IssueKind.OK
        assert verdict.attempts == 4, "sans les tentatives, le verdict retombe à indeterminate"

    def test_le_pont_vers_la_boucle_est_VRAIMENT_traverse(self, monkeypatch):
        """Le test qui discrimine : `run_sync` n'est pas simulé.

        Le rendu s'exécute sur le thread de la boucle, l'observation vit sur le
        thread appelant. Si les tentatives étaient enregistrées côté boucle, le
        carrier ne correspondrait pas et `attempts` vaudrait 0 ici.
        """
        vu = {}

        async def rendu(url, load_all, get_details, tentatives):
            import threading

            vu["thread"] = threading.current_thread().name
            tentatives.note(IssueKind.OK, "goto", 10)
            return _PAGE

        s = _scraper(monkeypatch, rendu)
        s.scrape_by_date_range("2020-01-01", "2020-12-31")

        assert vu["thread"] == "asyncio-loop", "le rendu doit bien changer de thread"
        (verdict,) = su.flush()
        assert verdict.attempts == 1
        assert verdict.issue == IssueKind.OK

    def test_une_navigation_qui_echoue_est_classee_et_non_muette(self, monkeypatch):
        async def rendu(url, load_all, get_details, tentatives):
            raise OSError("connexion refusée")

        s = _scraper(monkeypatch, rendu)
        assert s.scrape_by_artist("Un Artiste") == []

        (verdict,) = su.flush()
        assert verdict.issue == IssueKind.UNREACHABLE
        assert verdict.attempts == 1, "l'échec doit laisser une tentative, pas un silence"

    def test_les_tentatives_sont_videes_apres_rejeu(self, monkeypatch):
        """Deux scrapes de suite ne doivent pas recompter le premier."""

        async def rendu(url, load_all, get_details, tentatives):
            tentatives.note(IssueKind.OK, "goto", 10)
            return _PAGE

        s = _scraper(monkeypatch, rendu)
        s.scrape_by_date_range("2020-01-01", "2020-12-31")
        s.scrape_by_date_range("2020-01-01", "2020-12-31")

        premier, second = su.flush()
        assert (premier.attempts, second.attempts) == (1, 1)


class _FaussePage:
    """Le strict nécessaire de la page patchright pour `_trigger_details`."""

    def __init__(self, ids, retour):
        self._ids = ids
        self._retour = retour

    async def eval_on_selector_all(self, *_a, **_k):
        return self._ids

    async def evaluate(self, *_a, **_k):
        if isinstance(self._retour, Exception):
            raise self._retour
        return self._retour


class TestTimelines:
    """Une timeline manquante est courante ; zéro timeline accuse le transport."""

    @staticmethod
    def _jouer(ids, retour):
        import asyncio

        t = _Tentatives()
        asyncio.run(RIAAScraperV2(headless=True)._trigger_details(_FaussePage(ids, retour), t))
        return t.relevees

    def test_les_manquantes_ne_font_pas_echouer_le_run(self):
        """5 historiques sur 10 : cinq allers-retours aboutis, aucun échec.

        Les compter en échec ferait tomber tout un balayage pour un award sans
        historique — cas parfaitement normal.
        """
        relevees = self._jouer([str(i) for i in range(10)], 5)

        assert [k for k, _, _ in relevees] == [IssueKind.OK] * 5

    def test_zero_timeline_accuse_le_transport(self):
        """Le cas que le site a produit en déplaçant son endpoint AJAX."""
        relevees = self._jouer(["1", "2"], 0)

        assert [k for k, _, _ in relevees] == [IssueKind.PARSE]

    def test_une_erreur_patchright_est_classee(self):
        """Le type exact dépend de patchright, installé ou remplacé par sa
        sentinelle : ce qui doit tenir dans les deux cas, c'est qu'UNE tentative
        est relevée et qu'elle compte comme un échec."""
        relevees = self._jouer(["1"], PatchrightError("navigateur mort"))

        assert len(relevees) == 1
        assert is_failure(relevees[0][0])

    def test_aucune_ligne_aucune_tentative(self):
        assert self._jouer([], 0) == []
