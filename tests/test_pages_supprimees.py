"""Pages Genius SUPPRIMÉES (2026-11-23) : le « Oops! Page not found » reconnu
au crawl, constat daté sans rien effacer, désactivation en fin de cycle des
seules pages sans identifiant de plateforme, retour de la page (même ID qui
répond, ou page recréée sous un autre ID) — `services.pages_supprimees`."""

from datetime import datetime, timedelta

import pytest

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.scrapers import genius_scraper_v3
from src.scrapers.genius_scraper_v3 import GeniusScraperV3, page_introuvable
from src.services import credits, pages_supprimees
from src.services import runtime as module_runtime
from src.services.pages_supprimees import MOTIF_PAGE_SUPPRIMEE, ORIGINE_REGLE
from src.utils import disabled_tracks_manager as dtm
from src.utils.disabled_tracks_manager import DisabledTracksManager

_A = Artist(name="Logic")

#: Extrait de la page servie pour https://genius.com/Logic-summer-99-lyrics
#: (brut `genius_scrape`, lu le 2026-11-23 : ~350 Ko, sans conteneur de paroles).
_PAGE_INTROUVABLE = """<!DOCTYPE html><html><head><title>Burrr! | Genius</title></head>
<body><div class="global_messages"><!----></div>
<div class="render_404">
  <img class="render_404-image" src="https://assets.genius.com/images/winking_sgnarly.gif">
  <h1 class="render_404-headline">Oops! Page not found</h1>
  <p>Sorry, we didn't mean for that to happen!</p>
</div></body></html>"""

_PAGE_LUE = """<html><body>
<div data-lyrics-container="true">[Couplet 1]<br/>Un deux trois quatre cinq six</div>
<div class="Credit__Container-x"><div class="Credit__Label-x">Producer</div>
<div class="Credit__Contributor-x">6ix</div></div>
</body></html>"""


def _fiche(tid, titre=None, **kw):
    t = Track(title=titre or f"Titre {tid}", artist=_A)
    t.id = tid
    t.genius_id = 1000 + tid
    t.genius_url = f"https://genius.com/logic-{tid}-lyrics"
    for k, v in kw.items():
        setattr(t, k, v)
    return t


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(dtm, "DATA_DIR", tmp_path)
    return DisabledTracksManager()


class _Constats:
    """Les constats d'absence en mémoire (`runtime.constater_absences` /
    `absences_recentes`) : la base n'est pas en jeu ici."""

    def __init__(self, monkeypatch):
        self.poses: dict[int, datetime] = {}
        self.appels: list = []
        monkeypatch.setattr(module_runtime, "constater_absences", self._constater)
        monkeypatch.setattr(module_runtime, "absences_recentes", self._recentes)

    def _constater(self, rt, cle, poser, retirer=()):
        poser, retirer = list(poser), list(retirer)
        self.appels.append((cle, poser, retirer))
        if cle != module_runtime.ABSENCE_PAGE_GENIUS:
            return
        for tid, at in poser:
            self.poses[tid] = at
        for tid in retirer:
            self.poses.pop(tid, None)

    def _recentes(self, rt, sources):
        if module_runtime.ABSENCE_PAGE_GENIUS not in sources:
            return {}
        return {tid: {module_runtime.ABSENCE_PAGE_GENIUS} for tid in self.poses}


@pytest.fixture
def constats(monkeypatch):
    return _Constats(monkeypatch)


def _scraper(monkeypatch, html):
    s = GeniusScraperV3.__new__(GeniusScraperV3)
    s.derniers_ecarts_api = None
    s._crawl_page = lambda **kw: html
    monkeypatch.setattr(genius_scraper_v3.time, "sleep", lambda *_: None)
    return s


class TestDetection:
    def test_la_page_introuvable_est_reconnue(self):
        assert page_introuvable(_PAGE_INTROUVABLE)

    def test_une_page_lue_ne_l_est_pas(self):
        assert not page_introuvable(_PAGE_LUE)
        assert not page_introuvable(None) and not page_introuvable("")

    def test_le_mot_seul_dans_des_paroles_ne_suffit_pas(self):
        html = '<div data-lyrics-container="true">render_404 Oops! Page not found</div>'
        assert not page_introuvable(html)


class TestScrapeSansRienEffacer:
    def _fiche_pleine(self):
        t = _fiche(1)
        t.credits = [Credit(name="6ix", role=CreditRole.PRODUCER, source="genius")]
        t.lyrics.text = "[Couplet]\nDes paroles déjà lues"
        t.lyrics.present = True
        t.last_scraped = datetime(2026, 1, 1)
        t.album = "Young Sinatra"
        return t

    def test_les_credits_et_paroles_en_base_restent(self, monkeypatch):
        s = _scraper(monkeypatch, _PAGE_INTROUVABLE)
        t = self._fiche_pleine()
        assert s.scrape_track_credits(t) == []
        assert s.derniere_page_introuvable
        assert [c.name for c in t.credits] == ["6ix"]
        assert t.lyrics.text == "[Couplet]\nDes paroles déjà lues" and t.lyrics.present
        # Pas lue : la date de lecture des crédits ne bouge pas.
        assert t.last_scraped == datetime(2026, 1, 1)

    def test_le_lot_compte_un_constat_pas_un_echec(self, monkeypatch):
        s = _scraper(monkeypatch, _PAGE_INTROUVABLE)
        res = s.scrape_multiple_tracks([self._fiche_pleine()])
        assert res["introuvables"] == [1]
        assert res["failed"] == 0 and res["success"] == 0

    def test_les_paroles_ne_sont_pas_videes(self, monkeypatch):
        s = _scraper(monkeypatch, _PAGE_INTROUVABLE)
        t = _fiche(2)
        t.lyrics.present = None
        res = s.scrape_lyrics_batch([t])
        assert res["introuvables"] == [2] and res["failed"] == 0
        assert t.lyrics.present is None

    def test_une_page_lue_reste_une_lecture(self, monkeypatch):
        s = _scraper(monkeypatch, _PAGE_LUE)
        t = _fiche(3)
        s.scrape_track_credits(t)
        assert not s.derniere_page_introuvable and t.last_scraped is not None


class TestConstat:
    def test_la_cle_fait_taire_les_credits_genius_pas_les_paroles(self):
        cle = module_runtime.ABSENCE_PAGE_GENIUS
        assert cle in module_runtime.SOURCES_DU_MANQUE[module_runtime.Manque.CREDITS_GENIUS]
        assert cle not in module_runtime.SOURCES_DU_MANQUE[module_runtime.Manque.PAROLES]

    def test_l_etape_credits_pose_le_constat(self, monkeypatch, constats):
        monkeypatch.setattr(module_runtime, "morceaux_en_echec", lambda rt, sources: set())
        perdue, lue = _fiche(1), _fiche(2)
        perdue.credits = [Credit(name="6ix", role=CreditRole.PRODUCER, source="genius")]
        constats.poses[2] = datetime.now() - timedelta(days=1)  # page revenue

        class _Genius:
            def scrape_multiple_tracks(self, tracks, progress_callback=None):
                lue.last_scraped = datetime.now()
                return {"success": 1, "failed": 0, "errors": [], "introuvables": [1]}

            def close(self):
                pass

        clients = credits.Clients(genius=lambda: _Genius())
        opts = credits.OptionsCredits(
            genius=True,
            discogs=False,
            youtube=False,
            paroles_genius=False,
            paroles_ytm=False,
            sync_lrclib=False,
            sync_ytm=False,
            force_credits=True,
        )
        rt = type("Rt", (), {"data_manager": None, "disabled": None})()
        monkeypatch.setattr(credits, "_sauver", lambda rt, a, t, bilan: bilan)
        monkeypatch.setattr(credits, "_phase_albums", lambda *a, **k: None)
        bilan = credits.run(rt, _A, [perdue, lue], opts, credits.Hooks(), clients)
        assert bilan.genius_introuvables == 1
        assert set(constats.poses) == {1}  # posé pour la perdue, levé pour la revenue
        assert [c.name for c in perdue.credits] == ["6ix"]

    def test_la_phase_des_paroles_ne_rouvre_pas_une_page_supprimee(self, constats):
        constats.poses[1] = datetime.now()
        rt = object()
        a, b = _fiche(1), _fiche(2)
        assert credits.sans_page_supprimee(rt, [a, b], force=False) == [b]
        # Forcé (ou sans délais) : on redemande.
        assert credits.sans_page_supprimee(rt, [a, b], force=True) == [a, b]


class TestDesactivation:
    def test_seule_la_page_sans_identifiant_est_desactivee(self):
        nue, deezer, spotify, apple = (
            _fiche(1),
            _fiche(2, deezer_id=5),
            _fiche(3, spotify_id="x"),
            _fiche(4, apple_music_id="9"),
        )
        vivante = _fiche(5)
        pages = pages_supprimees.a_desactiver(
            [nue, deezer, spotify, apple, vivante], set(), {1, 2, 3, 4}
        )
        assert pages == [nue]

    def test_en_fin_de_cycle_avec_son_motif(self, manager, constats):
        constats.poses[1] = datetime.now()
        rt = type("Rt", (), {"disabled": manager})()
        assert pages_supprimees.desactiver_pages_supprimees(rt, _A, [_fiche(1), _fiche(2)]) == 1
        assert manager.load_disabled_tracks("Logic") == {1}
        motif = manager.load_motifs("Logic")[1]
        assert motif["motif"] == MOTIF_PAGE_SUPPRIMEE and motif["origine"] == ORIGINE_REGLE

    def test_une_page_reactivee_a_la_main_ne_l_est_plus(self, manager, constats):
        constats.poses[1] = datetime.now()
        rt = type("Rt", (), {"disabled": manager})()
        pages_supprimees.desactiver_pages_supprimees(rt, _A, [_fiche(1)])
        manager.save_disabled_tracks("Logic", set())  # réactivée dans l'IHM
        assert pages_supprimees.desactiver_pages_supprimees(rt, _A, [_fiche(1)]) == 0
        assert manager.load_disabled_tracks("Logic") == set()

    def test_le_cycle_la_desactive_apres_identite_credits_streams(self, monkeypatch):
        from src.services import cycle
        from src.services.runtime import Bilan

        appels = []
        monkeypatch.setattr(
            pages_supprimees, "desactiver_pages_supprimees", lambda *a: appels.append(a) or 3
        )
        rt = type("Rt", (), {"disabled": object()})()
        a = Artist(name="Logic")
        a.tracks = []
        incomplet = cycle.BilanCycle(etapes={"identite": Bilan(), "credits": Bilan()})
        cycle._desactiver_pages_supprimees(rt, a, incomplet)
        assert appels == []
        complet = cycle.BilanCycle(etapes={e: Bilan() for e in ("identite", "credits", "streams")})
        cycle._desactiver_pages_supprimees(rt, a, complet)
        assert complet.pages_supprimees_desactivees == 3


class _Api:
    def __init__(self, repondent):
        self.repondent = set(repondent)
        self.appels = []

    def page_repond(self, genius_id, track_id=None):
        self.appels.append(genius_id)
        return genius_id in self.repondent


class TestMemePageQuiRevient:
    def test_la_page_qui_repond_est_reactivee_et_son_constat_leve(self, manager, constats):
        manager.desactiver("Logic", {1, 2}, MOTIF_PAGE_SUPPRIMEE, ORIGINE_REGLE)
        constats.poses.update({1: datetime.now(), 2: datetime.now()})
        api = _Api({1001})
        rt = type("Rt", (), {"disabled": manager, "genius_api": api})()
        n = pages_supprimees.reactiver_pages_revenues(rt, _A, [_fiche(1), _fiche(2)])
        assert n == 1
        assert manager.load_disabled_tracks("Logic") == {2}
        assert set(constats.poses) == {2}

    def test_une_desactivation_a_la_main_n_est_jamais_levee(self, manager, constats):
        manager.save_disabled_tracks("Logic", {1})
        api = _Api({1001})
        rt = type("Rt", (), {"disabled": manager, "genius_api": api})()
        assert pages_supprimees.reactiver_pages_revenues(rt, _A, [_fiche(1)]) == 0
        assert manager.load_disabled_tracks("Logic") == {1} and api.appels == []


class _DM:
    def __init__(self):
        self.rattachees = []

    def rattacher_page_genius(self, track_id, **kw):
        self.rattachees.append((track_id, kw["genius_id"]))
        return True


def _page_neuve(gid, titre, **kw):
    t = Track(title=titre, artist=_A)
    t.genius_id = gid
    t.genius_url = f"https://genius.com/logic-{gid}-lyrics"
    for k, v in kw.items():
        setattr(t, k, v)
    return t


class TestPageRecreee:
    def test_meme_titre_au_juge(self):
        assert pages_supprimees.meme_titre("Summer '99", "Summer 99")
        assert pages_supprimees.meme_titre("Make Her Say (I Poke Her Face)", "Make Her Say")
        assert not pages_supprimees.meme_titre("Summer '99 (Remix)", "Summer '99")
        assert not pages_supprimees.meme_titre("Pieces pt. 2", "Pieces")

    def test_une_seule_fiche_au_meme_titre_est_rattachee(self):
        fiche = _fiche(1, "Summer '99")
        page = _page_neuve(9, "Summer 99")
        r = pages_supprimees.pages_recreees([page], [fiche, _fiche(2, "Autre")], {1})
        assert r.rattacher == [(page, fiche)] and r.a_trancher == []

    def test_une_page_connue_n_est_pas_une_recreation(self):
        fiche = _fiche(1, "Summer '99")
        page = _page_neuve(1001, "Summer '99")
        assert pages_supprimees.pages_recreees([page], [fiche], {1}).rattacher == []

    def test_deux_fiches_au_meme_titre_on_ne_devine_pas(self):
        a, b = _fiche(1, "Intro"), _fiche(2, "Intro")
        page = _page_neuve(9, "Intro", album="X")
        a.album = b.album = "X"
        r = pages_supprimees.pages_recreees([page], [a, b], {1, 2})
        assert r.rattacher == [] and r.a_trancher == [(page, [a, b])]

    def test_un_titre_seulement_proche_est_a_trancher(self):
        fiche = _fiche(1, "Summer '99")
        page = _page_neuve(9, "Summer '99 (Live)")
        r = pages_supprimees.pages_recreees([page], [fiche], {1})
        assert r.rattacher == [] and r.a_trancher == [(page, [fiche])]

    def test_un_generique_exige_le_meme_disque(self):
        fiche = _fiche(1, "Intro", album="Under Pressure")
        page = _page_neuve(9, "Intro", album="The Incredible True Story")
        assert pages_supprimees.pages_recreees([page], [fiche], {1}).rattacher == []

    def test_seule_une_fiche_a_la_page_supprimee_est_candidate(self):
        page = _page_neuve(9, "Summer '99")
        r = pages_supprimees.pages_recreees([page], [_fiche(1, "Summer '99")], set())
        assert r.rattacher == [] and r.a_trancher == []

    def test_rattachee_la_fiche_est_reactivee(self, manager, constats):
        manager.desactiver("Logic", {1}, MOTIF_PAGE_SUPPRIMEE, ORIGINE_REGLE)
        manager.save_disabled_tracks("Logic", {1, 3})  # 3 : à la main
        constats.poses.update({1: datetime.now(), 3: datetime.now()})
        dm = _DM()
        rt = type("Rt", (), {"disabled": manager, "data_manager": dm})()
        existants = [_fiche(1, "Summer '99"), _fiche(3, "Gang Related")]
        nouveaux = [_page_neuve(9, "Summer 99"), _page_neuve(8, "Gang Related")]
        faits = pages_supprimees.rattacher_pages_recreees(rt, _A, nouveaux, existants)
        # La fiche désactivée à la main n'est pas candidate.
        assert dm.rattachees == [(1, 9)] and len(faits) == 1
        assert manager.load_disabled_tracks("Logic") == {3}
        assert set(constats.poses) == {3}

    def test_une_fiche_active_au_constat_recent_reprend_aussi_sa_page(self, manager, constats):
        """Une fiche gardée par un identifiant de plateforme (jamais désactivée)
        reprend la page recréée de la même façon."""
        constats.poses[1] = datetime.now()
        dm = _DM()
        rt = type("Rt", (), {"disabled": manager, "data_manager": dm})()
        fiche = _fiche(1, "Summer '99", spotify_id="abc")
        faits = pages_supprimees.rattacher_pages_recreees(
            rt, _A, [_page_neuve(9, "Summer '99")], [fiche]
        )
        assert dm.rattachees == [(1, 9)] and faits
