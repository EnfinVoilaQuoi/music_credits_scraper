"""Parité des jumeaux ASYNC des scrapers Playwright (Phase F3).

`tests/test_api_async_twins.py` couvre les jumeaux des clients httpx ; ici ce
sont les scrapers, qui ne se testent pas avec un `MockTransport` mais avec un
faux arbre d'éléments.

Deux contrats sont vérifiés :

1. **Les garde-fous sync lèvent.** Playwright sync est THREAD-AFFINE : appeler
   une méthode sync sur une instance async depuis la boucle asyncio produit un
   blocage, pas une erreur claire. Chaque jumeau remplace donc ces méthodes par
   un `RuntimeError` explicite — un contrat facile à casser au prochain ajout de
   méthode.

2. **Le parseur async rend EXACTEMENT ce que rend le sync.** Les deux versions
   sont des copies ligne à ligne (mêmes sélecteurs, mêmes seuils) : le seul
   risque réel est qu'elles divergent. Le test les confronte aux mêmes données.
"""

import asyncio

import pytest

from src.scrapers.bpmfinder_scraper_async import BPMFinderScraperAsync
from src.scrapers.songbpm_scraper_async import SongBPMScraperAsync
from src.scrapers.songbpm_scraper_v2 import SongBPMScraper
from src.scrapers.spotify_id_scraper_async import SpotifyIDScraperAsync
from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper

# ────────────────────────────────────────────────────── faux DOM sync et async


class _ElementSync:
    def __init__(self, enfants=None, attrs=None, texte=""):
        self._enfants = enfants or {}
        self._attrs = attrs or {}
        self._texte = texte

    def query_selector_all(self, selecteur):
        return self._enfants.get(selecteur, [])

    def get_attribute(self, nom):
        return self._attrs.get(nom)

    def inner_text(self):
        return self._texte


class _ElementAsync:
    """Même arbre, mêmes valeurs — seule l'API devient awaitable."""

    def __init__(self, enfants=None, attrs=None, texte=""):
        self._enfants = enfants or {}
        self._attrs = attrs or {}
        self._texte = texte

    async def query_selector_all(self, selecteur):
        return self._enfants.get(selecteur, [])

    async def get_attribute(self, nom):
        return self._attrs.get(nom)

    async def inner_text(self):
        return self._texte


def _arbre(fabrique, artiste="Jul", titre="Bande organisée", href=None, spotify_url=None):
    """Un conteneur de résultat SongBPM, construit avec la classe donnée."""
    href = href or "https://songbpm.com/@jul/bande-organisee"
    infos = fabrique(
        enfants={
            "p": [
                fabrique(attrs={"class": "text-sm text-muted"}, texte=artiste),
                fabrique(attrs={"class": "text-lg font-bold"}, texte=titre),
            ]
        }
    )
    metriques = [
        fabrique(enfants={"span": [fabrique(texte=lab), fabrique(texte=val)]})
        for lab, val in (("BPM", "140"), ("KEY", "F"), ("DURATION", "3:34"))
    ]
    lien = fabrique(
        attrs={"href": href},
        enfants={"div.flex-1": [infos], "div.flex.flex-1.flex-col.items-center": metriques},
    )
    enfants = {"a[href*='/@']": [lien]}
    if spotify_url:
        enfants["a[href*='spotify.com/track/']"] = [fabrique(attrs={"href": spotify_url})]
    return fabrique(enfants=enfants)


class _PageSync:
    def __init__(self, conteneurs):
        self._c = conteneurs

    def query_selector_all(self, selecteur):
        return self._c if selecteur == "div.bg-card" else []


class _PageAsync:
    def __init__(self, conteneurs):
        self._c = conteneurs

    async def query_selector_all(self, selecteur):
        return self._c if selecteur == "div.bg-card" else []


# ──────────────────────────────────────────────────────────── garde-fous sync


class TestGardeFousSync:
    """Playwright sync est thread-affine : sur une instance async, une méthode
    sync appelée depuis la boucle BLOQUE. Le RuntimeError transforme un hang en
    erreur immédiate et lisible."""

    def test_songbpm(self):
        s = SongBPMScraperAsync(headless=True)
        for appel in (
            lambda: s._ensure_driver(),
            lambda: s.search_track("a", "b"),
            lambda: s.close(),
        ):
            with pytest.raises(RuntimeError, match="async"):
                appel()

    def test_spotify(self, tmp_path):
        s = SpotifyIDScraperAsync(cache_file=str(tmp_path / "c.json"))
        for appel in (
            lambda: s._ensure_driver(),
            lambda: s.get_spotify_id("a", "b"),
            lambda: s.get_spotify_page_title("id"),
            lambda: s.close(),
        ):
            with pytest.raises(RuntimeError, match="async"):
                appel()

    def test_bpmfinder(self, tmp_path, monkeypatch):
        import src.scrapers.bpmfinder_scraper as bf

        monkeypatch.setattr(bf, "_CACHE_FILE", tmp_path / "cache.json")
        s = BPMFinderScraperAsync(headless=True)
        for appel in (
            lambda: s._ensure_driver(),
            lambda: s.analyze("https://youtu.be/x"),
            lambda: s.analyze_file("f.mp3"),
            lambda: s.close(),
        ):
            with pytest.raises(RuntimeError, match="async"):
                appel()


# ────────────────────────────────────────────────────── logique pure héritée


def _fonction_nue(cls, nom):
    """La fonction SOUS-JACENTE d'un attribut de classe.

    Une `staticmethod` s'accède comme une fonction nue, une `classmethod` crée
    un objet LIÉ différent par sous-classe : comparer les deux à l'identique
    demande de descendre au `__func__` quand il existe. Sans ça, transformer une
    staticmethod partagée en classmethod partagée ferait rougir le test alors que
    rien n'a été recopié.
    """
    attribut = getattr(cls, nom)
    return getattr(attribut, "__func__", attribut)


class TestLogiquePureHeritee:
    """Les jumeaux SOUS-CLASSENT : la logique de décision ne doit pas être
    redéfinie, sinon les deux voies pourraient diverger sans qu'on le voie."""

    def test_songbpm_matching_identique(self):
        assert SongBPMScraperAsync._match_track is SongBPMScraper._match_track
        assert SongBPMScraperAsync._title_key is SongBPMScraper._title_key
        assert SongBPMScraperAsync._details_from_text is SongBPMScraper._details_from_text

    def test_spotify_logique_identique(self):
        assert SpotifyIDScraperAsync._calculate_relevance is SpotifyIDScraper._calculate_relevance
        # Lecture de l'embed : le point d'entrée du `__NEXT_DATA__` et les deux
        # vues qui en dérivent (artistes, identité) sont PARTAGÉS — seul le
        # TRANSPORT diffère entre les jumeaux. C'est ce qui a permis de corriger
        # `get_spotify_page_title` des deux côtés à la fois (2026-09-08 : il
        # interrogeait la SPA et rendait donc toujours None).
        for nom in (
            "_parse_embed_entity",
            "_parse_embed_artists",
            "_identite_depuis_embed",
            "_titre_de_page",
            "_url_embed",
        ):
            assert _fonction_nue(SpotifyIDScraperAsync, nom) is _fonction_nue(SpotifyIDScraper, nom)
        # `_clean_page_title` est une classmethod : l'accès crée un objet lié
        # DIFFÉRENT à chaque fois (lié à la sous-classe), d'où la comparaison sur
        # la fonction sous-jacente.
        assert (
            SpotifyIDScraperAsync._clean_page_title.__func__
            is SpotifyIDScraper._clean_page_title.__func__
        )
        assert (
            SpotifyIDScraperAsync.extract_spotify_id_from_url
            is SpotifyIDScraper.extract_spotify_id_from_url
        )

    def test_bpmfinder_logique_identique(self):
        from src.scrapers.bpmfinder_scraper import BPMFinderScraper

        assert BPMFinderScraperAsync._cards_from_text is BPMFinderScraper._cards_from_text
        assert BPMFinderScraperAsync._card_to_result is BPMFinderScraper._card_to_result
        assert BPMFinderScraperAsync._video_id is BPMFinderScraper._video_id

    def test_songbpm_async_utilise_le_matching_herite(self):
        """Vérification d'usage, pas seulement d'identité."""
        s = SongBPMScraperAsync(headless=True)
        assert s._match_track("Bande organisée", "Jul", "Bande organisée", "Jul") is True
        assert s._title_key("FREESTYLE BOOSKA-POGO") == "freestyle booska pogo"

    def test_spotify_async_partage_le_cache(self, tmp_path):
        s = SpotifyIDScraperAsync(cache_file=str(tmp_path / "c.json"))
        assert s._get_cache_key(" JUL ", " Titre ") == "jul::titre"


# ────────────────────────────────────────────── parité du parseur de résultats


class TestPariteDuParseur:
    """Le parseur async de SongBPM est une copie ligne à ligne du sync. Sur les
    mêmes données, les deux doivent rendre le même résultat — c'est le seul
    garde-fou contre une dérive lors d'une correction faite d'un seul côté."""

    def _sync(self, conteneurs):
        s = SongBPMScraper(headless=True)
        s.page = _PageSync(conteneurs)
        return s._get_search_results()

    def _async(self, conteneurs):
        s = SongBPMScraperAsync(headless=True)
        s.page = _PageAsync(conteneurs)
        return asyncio.run(s._get_search_results_async())

    def test_resultat_complet(self):
        attendu = self._sync([_arbre(_ElementSync)])
        obtenu = self._async([_arbre(_ElementAsync)])
        assert obtenu == attendu
        assert obtenu[0]["bpm"] == 140

    def test_identifiant_spotify(self):
        url = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
        attendu = self._sync([_arbre(_ElementSync, spotify_url=url)])
        obtenu = self._async([_arbre(_ElementAsync, spotify_url=url)])
        assert obtenu == attendu
        assert obtenu[0]["spotify_id"] == "4cOdK2wGLETKBW3PvgPWqT"

    @pytest.mark.parametrize("plateforme", ["/apple-music", "/spotify", "/amazon", "/youtube"])
    def test_liens_de_plateformes_ecartes_des_deux_cotes(self, plateforme):
        href = f"https://songbpm.com/@jul/titre{plateforme}"
        assert self._async([_arbre(_ElementAsync, href=href)]) == []
        assert self._sync([_arbre(_ElementSync, href=href)]) == []

    def test_lien_trop_court_ecarte_des_deux_cotes(self):
        assert self._async([_arbre(_ElementAsync, href="/@jul")]) == []
        assert self._sync([_arbre(_ElementSync, href="/@jul")]) == []

    def test_plusieurs_resultats(self):
        attendu = self._sync([_arbre(_ElementSync), _arbre(_ElementSync, titre="Autre")])
        obtenu = self._async([_arbre(_ElementAsync), _arbre(_ElementAsync, titre="Autre")])
        assert obtenu == attendu
        assert len(obtenu) == 2

    def test_page_vide(self):
        assert self._async([]) == self._sync([]) == []

    def test_conteneur_en_erreur_saute(self):
        """Résilience identique : un élément détaché ne fait pas perdre les autres."""

        class _CasseAsync(_ElementAsync):
            async def query_selector_all(self, selecteur):
                raise AttributeError("élément détaché")

        obtenu = self._async([_CasseAsync(), _arbre(_ElementAsync)])
        assert len(obtenu) == 1

    def test_page_en_erreur(self):
        class _PageCasse:
            async def query_selector_all(self, selecteur):
                raise AttributeError("page fermée")

        s = SongBPMScraperAsync(headless=True)
        s.page = _PageCasse()
        assert asyncio.run(s._get_search_results_async()) == []
