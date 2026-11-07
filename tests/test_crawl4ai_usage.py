"""L'échelle CDP → headless → fenêtre visible vaut UN appel logique.

`_patchright_fetch` est monkeypatché : aucun navigateur n'est lancé.
"""

import asyncio

import pytest

from src.observability import source_usage as su
from src.observability.issues import IssueKind
from src.scrapers import crawl4ai_scraper_base as base
from src.scrapers.crawl4ai_scraper_base import CrawlAIScraperBase


@pytest.fixture(autouse=True)
def _propre(monkeypatch, tmp_path):
    monkeypatch.setattr(base, "_dossier_des_defis", lambda: tmp_path / "defis")
    su.reset()
    su.set_sink(None)
    monkeypatch.delenv("GENIUS_CDP_URL", raising=False)  # échelle headless → visible
    yield
    su.reset()
    su.set_sink(None)


def _scraper(passes, health_key="genius_scrape"):
    """Scraper dont chaque passe rend le prochain (html, blocked) de `passes`."""
    scraper = CrawlAIScraperBase(headless=True, health_key=health_key)
    restantes = list(passes)

    async def faux_fetch(*args, **kwargs):
        html, blocked = restantes.pop(0)
        if isinstance(html, Exception):
            raise html
        return None, html, blocked

    scraper._patchright_fetch = faux_fetch
    return scraper


def _jouer(scraper, url="https://genius.com/x"):
    html = asyncio.run(scraper.acrawl_page(url))
    return html, su.flush()


def test_headless_bloque_puis_fenetre_visible_vaut_un_succes():
    """Le cas nominal derrière Cloudflare : deux passes, UN verdict `ok`.
    Compter deux échecs ici afficherait la source en rouge alors qu'elle sert."""
    html, verdicts = _jouer(_scraper([(None, True), ("<html>paroles</html>", False)]))
    assert html == "<html>paroles</html>"
    assert len(verdicts) == 1
    assert verdicts[0].issue == IssueKind.OK
    assert verdicts[0].attempts == 2
    assert verdicts[0].expected_blocked == 1, "le contournement doit rester visible"


def test_headless_suffisant_vaut_un_succes_sans_blocage():
    _, verdicts = _jouer(_scraper([("<html>ok</html>", False)]))
    assert verdicts[0].issue == IssueKind.OK
    assert verdicts[0].expected_blocked == 0


def test_bloque_partout_donne_un_seul_echec():
    html, verdicts = _jouer(_scraper([(None, True), (None, True)]))
    assert html is None
    assert len(verdicts) == 1
    assert verdicts[0].issue == IssueKind.UNREACHABLE


def test_une_erreur_patchright_est_classee():
    """Piège du dépôt : patchright a ses PROPRES classes d'exception."""
    boum = base.PatchrightError("Target closed")
    _, verdicts = _jouer(_scraper([(boum, True), (boum, True)]))
    assert len(verdicts) == 1
    assert verdicts[0].issue in {IssueKind.CRASH, IssueKind.TIMEOUT, IssueKind.UNREACHABLE}


def test_la_cle_de_source_suit_le_scraper():
    """Ultratop passe par la même base mais compte pour BRMA."""
    _, verdicts = _jouer(
        _scraper([("<html>certifs</html>", False)], health_key="brma"),
        url="https://www.ultratop.be/fr/or-platine/2024/singles",
    )
    assert verdicts[0].source_key == "brma"


def test_le_pont_sync_rejoue_les_passes_dans_l_observation_de_l_appelant():
    """Le test qui discrimine : `run_sync` n'est PAS simulé.

    `update_brma._lire_page` tient une observation `brma` sur son thread pour y
    poser le verdict de PARSE ; le rendu tourne sur la boucle. Quand le pont
    ouvrait sa propre observation côté boucle, l'appelant restait sans tentative
    — run BRMA du 2026-09-17 : 78 pages `ok` ET 78 `indeterminate`. Ici : UN
    verdict, portant les deux passes, sur le thread de l'appelant.
    """
    import threading

    vu = {}
    scraper = _scraper([(None, True), ("<html>ok</html>", False)], health_key="brma")
    interne = scraper._patchright_fetch

    async def faux_fetch(*args, **kwargs):
        vu["thread"] = threading.current_thread().name
        return await interne(*args, **kwargs)

    scraper._patchright_fetch = faux_fetch
    with su.observe("brma", label="page") as obs:
        html = scraper._crawl_page("https://www.ultratop.be/x")
        obs.ok()  # le verdict de parse de l'appelant
    assert html == "<html>ok</html>"
    assert vu["thread"] == "asyncio-loop", "le rendu doit bien changer de thread"
    (verdict,) = su.flush()
    assert verdict.issue == IssueKind.OK
    assert verdict.attempts == 2 and verdict.expected_blocked == 1


def test_le_pont_sync_sans_appelant_observant_ouvre_la_sienne():
    """Genius sync : aucun `observe` chez l'appelant — le pont en ouvre une,
    comme avant, et le verdict porte les passes."""
    scraper = _scraper([("<html>ok</html>", False)])
    scraper._crawl_page("https://genius.com/x")
    (verdict,) = su.flush()
    assert verdict.issue == IssueKind.OK and verdict.attempts == 1


# --- La fenêtre visible : suit le défi, bornée pendant un lot ---------------------

DEFI = "<html><title>Just a moment...</title></html>"
SANS_PAROLES = "<html><div>Lyrics for this song have yet to be transcribed</div></html>"
AVEC_PAROLES = "<html><div data-lyrics-container='true'>paroles</div></html>"


class FaussePage:
    """Sert un HTML par regard ; le dernier reste. Une horloge qu'on fait avancer."""

    def __init__(self, etats, horloge=None):
        self.etats = list(etats)
        self.regards = 0
        self.horloge = horloge
        self.fermee = False
        self.devant = 0  # appels à bring_to_front
        self.bornes = None  # la dernière position demandée à la fenêtre

    # La session CDP de la fenêtre : la fausse page la joue elle-même.
    @property
    def context(self):
        return self

    async def new_cdp_session(self, _page):
        return self

    async def send(self, methode, params=None):
        if methode == "Browser.getWindowForTarget":
            return {"windowId": 1}
        self.bornes = params["bounds"]

    async def bring_to_front(self):
        self.devant += 1

    def _courant(self):
        etat = self.etats[min(self.regards, len(self.etats) - 1)]
        if isinstance(etat, Exception):
            raise etat
        return etat

    async def query_selector(self, _sel):
        return "conteneur" if "data-lyrics-container" in self._courant() else None

    async def content(self):
        return self._courant()

    async def wait_for_selector(self, _sel, timeout):
        assert timeout != 0, "jamais d'attente sans limite d'un conteneur"
        if "data-lyrics-container" in self._courant():
            return "conteneur"
        raise base.PatchrightError("Timeout")

    async def wait_for_timeout(self, ms):
        self.regards += 1
        if self.horloge:
            self.horloge["t"] += ms / 1000

    def is_closed(self):
        return self.fermee


def _attendre(page, limite, monkeypatch):
    monkeypatch.setattr(base.settings, "fenetre_visible_max_s", limite)
    if page.horloge:
        monkeypatch.setattr(base.time, "monotonic", lambda: page.horloge["t"])
    scraper = CrawlAIScraperBase(headless=True, health_key="genius_scrape")
    return asyncio.run(scraper._attendre_fin_du_defi(page, "[data-lyrics-container='true']", "u"))


class TestAttenteDuDefi:
    def test_defi_leve_sur_une_page_sans_paroles_ne_fige_plus(self, monkeypatch):
        """« Souvenir Du Bled » d'Aketo, Talib Kweli : le conteneur n'existera jamais."""
        page = FaussePage([DEFI, DEFI, SANS_PAROLES])
        assert _attendre(page, 0, monkeypatch) is False
        assert page.regards == 2

    def test_defi_leve_le_conteneur_est_trouve(self, monkeypatch):
        assert _attendre(FaussePage([DEFI, AVEC_PAROLES]), 0, monkeypatch) is True

    def test_sans_limite_on_attend_l_humain(self, monkeypatch):
        """Hors lot : une heure de défi, et la page vient quand même."""
        horloge = {"t": 0.0}
        page = FaussePage([DEFI] * 1800 + [AVEC_PAROLES], horloge)
        assert _attendre(page, 0, monkeypatch) is True
        assert horloge["t"] >= 3600

    def test_dans_un_lot_le_defi_non_leve_rend_la_main(self, monkeypatch):
        horloge = {"t": 0.0}
        assert _attendre(FaussePage([DEFI], horloge), 180, monkeypatch) is False
        assert 180 <= horloge["t"] < 185

    def test_une_navigation_du_defi_n_arrete_pas_l_attente(self, monkeypatch):
        """Le contexte détruit pendant une redirection n'est pas une fermeture."""
        page = FaussePage([DEFI, base.PatchrightError("context destroyed"), AVEC_PAROLES])
        assert _attendre(page, 0, monkeypatch) is True

    def test_l_onglet_ferme_abandonne(self, monkeypatch):
        page = FaussePage([base.PatchrightError("Target closed")])
        page.fermee = True
        with pytest.raises(base.PatchrightError):
            _attendre(page, 0, monkeypatch)

    def test_un_defi_revenu_pendant_la_grace_est_encore_attendu(self, monkeypatch):
        """La page semble passée, le défi revient : ne pas capturer le défi."""

        class Revient(FaussePage):
            async def wait_for_selector(self, _sel, timeout):
                self.regards += 1
                raise base.PatchrightError("Timeout")

        assert _attendre(Revient([SANS_PAROLES, DEFI, DEFI, AVEC_PAROLES]), 0, monkeypatch) is True


def test_le_defi_non_leve_n_est_pas_une_page():
    """La limite atteinte : le HTML du défi ne doit ni être lu ni gardé dans le brut,
    et la page reste en échec (relue au prochain passage)."""
    html, verdicts = _jouer(_scraper([(None, True), (DEFI, True)]))
    assert html is None
    assert verdicts[0].issue == IssueKind.UNREACHABLE
    assert verdicts[0].expected_blocked == 2


def test_la_fenetre_visible_suit_le_defi_de_bout_en_bout(tmp_path, monkeypatch):
    """`_patchright_fetch` sans navigateur : le défi non levé rend `blocked`."""
    import patchright.async_api as pa

    page = FaussePage([DEFI], {"t": 0.0})
    monkeypatch.setattr(base.time, "monotonic", lambda: page.horloge["t"])
    monkeypatch.setattr(base.settings, "fenetre_visible_max_s", 180)

    async def goto(*_a, **_k):
        return None

    page.goto = goto

    class Contexte:
        pages = [page]

        async def close(self):
            page.fermee = True

    class Chromium:
        async def launch_persistent_context(self, *_a, **_k):
            return Contexte()

    class Playwright:
        chromium = Chromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(pa, "async_playwright", Playwright)
    scraper = CrawlAIScraperBase(headless=True, health_key="genius_scrape")
    scraper.profile_dir = str(tmp_path)
    _, html, blocked = asyncio.run(
        scraper._patchright_fetch("u", None, None, 120_000, 120_000, 0, headless=False)
    )
    assert blocked is True and html == DEFI
    assert page.devant == 1, "un défi de 180 s demande l'humain"


# --- La fenêtre du défi ne vient devant que s'il faut un humain (2026-11-08) ------


class _FauxNavigateur:
    """`async_playwright` sans navigateur : garde les arguments de lancement."""

    def __init__(self, page):
        self.page = page
        self.lancements = []
        test = self

        class Contexte:
            pages = [page]

            async def close(self):
                page.fermee = True

        class Chromium:
            async def launch_persistent_context(self, _profil, **kwargs):
                test.lancements.append(kwargs)
                return Contexte()

        class Playwright:
            chromium = Chromium()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a):
                return False

        self.Playwright = Playwright


def _fetch(page, monkeypatch, tmp_path, *, headless):
    import patchright.async_api as pa

    nav = _FauxNavigateur(page)
    monkeypatch.setattr(pa, "async_playwright", nav.Playwright)
    scraper = CrawlAIScraperBase(headless=True, health_key="genius_scrape")
    scraper.profile_dir = str(tmp_path)
    rendu = asyncio.run(
        scraper._patchright_fetch("u", None, None, 1_000, 1_000, 0, headless=headless)
    )
    return rendu, nav.lancements


class TestPremierPlan:
    def test_un_defi_leve_seul_ne_vient_jamais_devant(self, monkeypatch):
        """197 / 197 défis levés en ≤ 5 s : personne n'a à voir la fenêtre."""
        page = FaussePage([DEFI] * 5 + [AVEC_PAROLES], {"t": 0.0})
        assert _attendre(page, 0, monkeypatch) is True
        assert page.devant == 0 and page.bornes is None

    def test_un_defi_qui_dure_vient_devant_une_seule_fois(self, monkeypatch):
        """Sans limite (l'app, hors lot) : une heure d'attente, UN passage devant."""
        page = FaussePage([DEFI] * 1800 + [AVEC_PAROLES], {"t": 0.0})
        assert _attendre(page, 0, monkeypatch) is True
        assert page.devant == 1
        assert page.bornes["left"] >= 0 and page.bornes["top"] >= 0, "ramenée à l'écran"

    def test_la_fenetre_du_defi_s_ouvre_hors_de_l_ecran(self, monkeypatch, tmp_path):
        page = FaussePage([AVEC_PAROLES], {"t": 0.0})

        async def goto(*_a, **_k):
            return None

        page.goto = goto
        (_, html, blocked), (lancement,) = _fetch(page, monkeypatch, tmp_path, headless=False)
        assert html == AVEC_PAROLES and blocked is False
        assert base._POSITION_HORS_ECRAN in lancement["args"]
        assert "--disable-backgrounding-occluded-windows" in lancement["args"]
        assert page.devant == 0

    def test_headless_et_sessions_sans_position(self):
        scraper = CrawlAIScraperBase(headless=True, health_key="genius_scrape")
        assert "args" not in scraper._launch_kwargs(True, hors_ecran=True)
        assert "args" not in scraper._launch_kwargs(False), "session() : rien de changé"


# --- Une coupure réseau n'est pas un défi (2026-11-08, Kaaris) --------------------

COUPURE = "Page.goto: net::ERR_NAME_NOT_RESOLVED at https://genius.com/Kaaris-ecoute-lyrics"


class TestCoupureReseau:
    def test_les_erreurs_du_reseau(self):
        assert base.erreur_reseau(base.PatchrightError(COUPURE))
        assert base.erreur_reseau(base.PatchrightError("net::ERR_INTERNET_DISCONNECTED at u"))
        assert base.erreur_reseau(base.PatchrightError("net::ERR_NETWORK_CHANGED at u"))
        assert not base.erreur_reseau(base.PatchrightError("Timeout 30000ms exceeded."))
        assert not base.erreur_reseau(base.PatchrightError("Target closed"))

    def test_la_coupure_en_headless_n_ouvre_pas_de_fenetre(self):
        """Une seule passe : la fenêtre visible n'en a pas d'autre à servir."""
        html, verdicts = _jouer(_scraper([(base.PatchrightError(COUPURE), True)]))
        assert html is None
        (verdict,) = verdicts
        assert verdict.issue == IssueKind.UNREACHABLE and verdict.attempts == 1

    def test_la_coupure_n_est_pas_un_blocage(self, monkeypatch, tmp_path):
        page = FaussePage([AVEC_PAROLES])

        async def goto(*_a, **_k):
            raise base.PatchrightError(COUPURE)

        page.goto = goto
        (_, html, blocked), _ = _fetch(page, monkeypatch, tmp_path, headless=True)
        assert html is None and blocked is False

    def test_une_erreur_du_navigateur_reste_un_blocage(self, monkeypatch, tmp_path):
        """Hors réseau, l'échelle est inchangée : la fenêtre visible est tentée."""
        page = FaussePage([AVEC_PAROLES])

        async def goto(*_a, **_k):
            raise base.PatchrightError("Timeout 30000ms exceeded.")

        page.goto = goto
        (_, html, blocked), _ = _fetch(page, monkeypatch, tmp_path, headless=True)
        assert html is None and blocked is True


# --- Le journal dit comment la fenêtre s'est terminée ----------------------------


@pytest.fixture
def journal(monkeypatch):
    lignes = []
    monkeypatch.setattr(base.logger, "info", lignes.append)
    monkeypatch.setattr(base.logger, "warning", lignes.append)
    return lignes


class TestCompteRendu:
    def test_defi_leve_page_sans_le_contenu(self, monkeypatch, journal):
        """La piste « Souvenir Du Bled » : ce que le journal ne disait pas."""
        horloge = {"t": 0.0}
        _attendre(FaussePage([DEFI] * 4 + [SANS_PAROLES], horloge), 0, monkeypatch)
        assert journal == [
            "CrawlAIScraperBase: défi levé après 8 s, page sans le contenu attendu : u"
        ]

    def test_defi_leve_contenu_attendu(self, monkeypatch, journal):
        horloge = {"t": 0.0}
        _attendre(FaussePage([DEFI, AVEC_PAROLES], horloge), 0, monkeypatch)
        assert journal == ["CrawlAIScraperBase: défi levé après 2 s, contenu attendu : u"]

    def test_la_duree_est_celle_du_defi_sans_la_grace(self, monkeypatch, journal):
        horloge = {"t": 0.0}

        class Lente(FaussePage):
            async def wait_for_selector(self, _sel, timeout):
                horloge["t"] += timeout / 1000
                raise base.PatchrightError("Timeout")

        _attendre(Lente([DEFI, SANS_PAROLES], horloge), 0, monkeypatch)
        assert "après 2 s" in journal[0]

    def test_defi_non_leve(self, monkeypatch, journal):
        _attendre(FaussePage([DEFI], {"t": 0.0}), 180, monkeypatch)
        assert journal == [
            "CrawlAIScraperBase: défi non levé seul après 15 s — "
            "fenêtre au premier plan, résous-le : u",
            "CrawlAIScraperBase: défi non levé après 180 s, "
            "page laissée pour le prochain passage : u",
        ]

    def test_fenetre_fermee(self, monkeypatch, journal):
        page = FaussePage([base.PatchrightError("Target closed")], {"t": 0.0})
        page.fermee = True
        with pytest.raises(base.PatchrightError):
            _attendre(page, 0, monkeypatch)
        assert journal == ["CrawlAIScraperBase: fenêtre fermée après 0 s : u"]

    def test_l_ouverture_nomme_le_marqueur(self, journal):
        _jouer(_scraper([(DEFI, True), (AVEC_PAROLES, False)]))
        assert "(marqueur : just a moment)" in journal[0]

    def test_l_ouverture_sans_html_dit_l_erreur(self, journal):
        _jouer(_scraper([(None, True), (AVEC_PAROLES, False)]))
        assert "(marqueur : aucun, erreur du navigateur)" in journal[0]


def test_les_marqueurs_du_defi():
    assert base._marqueur_de_defi(DEFI) == "just a moment"
    assert base._marqueur_de_defi("<script>window._cf_chl_opt={}</script>") == "_cf_chl_opt"
    assert base._marqueur_de_defi(None) is None
    assert base._marqueur_de_defi(SANS_PAROLES) is None
    assert (
        base._marqueur_de_defi('<script src="/cdn-cgi/challenge-platform/x"></script>') is None
    ), "le beacon est aussi sur les pages déjà passées"


class TestDefiGarde:
    URL = "https://genius.com/Kamelancien-souvenir-du-bled-lyrics"

    def test_le_defi_non_leve_est_garde(self):
        _jouer(_scraper([(None, True), (DEFI, True)]), url=self.URL)
        (fichier,) = base._dossier_des_defis().iterdir()
        assert fichier.name.endswith("_Kamelancien-souvenir-du-bled-lyrics.html")
        assert fichier.read_text(encoding="utf-8") == f"<!-- {self.URL} -->\n{DEFI}"

    def test_un_defi_leve_ne_garde_rien(self):
        _jouer(_scraper([(DEFI, True), (AVEC_PAROLES, False)]), url=self.URL)
        assert not base._dossier_des_defis().exists()

    def test_une_ecriture_impossible_ne_fait_pas_tomber_le_run(self, monkeypatch, tmp_path):
        (tmp_path / "fichier").write_text("x")
        monkeypatch.setattr(base, "_dossier_des_defis", lambda: tmp_path / "fichier" / "defis")
        html, verdicts = _jouer(_scraper([(None, True), (DEFI, True)]), url=self.URL)
        assert html is None and verdicts[0].issue == IssueKind.UNREACHABLE
