"""Variantes ASYNC des scrapers Playwright (Phase F3) et leur classe mère.

`tests/test_api_async_twins.py` couvre les clients httpx ; ici ce sont les
scrapers. Depuis le retrait de la voie sync de SongBPM (aucun appelant en
production), `SongBPMScraper` n'est plus que la logique PURE dont
`SongBPMScraperAsync` hérite : il n'y a plus de jumeau à comparer, et le parseur
de résultats se teste directement sur la variante async
(`tests/test_songbpm_scraper_v2.py`).

Reste le scraper Spotify ID : sa classe mère garde des méthodes sync vivantes
(vote d'ID artiste de Kworb) ; deux contrats sont vérifiés :

1. **Les garde-fous sync lèvent.** Playwright sync est THREAD-AFFINE : appeler
   une méthode sync sur une instance async depuis la boucle asyncio produit un
   blocage, pas une erreur claire. L'instance async remplace donc ces méthodes
   par un `RuntimeError` explicite.

2. **La logique de décision est héritée, jamais redéfinie**, sinon les deux
   classes pourraient diverger sans qu'on le voie.
"""

import pytest

from src.scrapers.songbpm_scraper_async import SongBPMScraperAsync
from src.scrapers.songbpm_scraper_v2 import SongBPMScraper
from src.scrapers.spotify_id_scraper_async import SpotifyIDScraperAsync
from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper

# ──────────────────────────────────────────────────────────── garde-fous sync


class TestGardeFousSync:
    """Playwright sync est thread-affine : sur une instance async, une méthode
    sync appelée depuis la boucle BLOQUE. Le RuntimeError transforme un hang en
    erreur immédiate et lisible."""

    def test_spotify(self, tmp_path):
        s = SpotifyIDScraperAsync(cache_file=str(tmp_path / "c.json"))
        for appel in (lambda: s._ensure_driver(), lambda: s.close()):
            with pytest.raises(RuntimeError, match="async"):
                appel()

    def test_songbpm_n_a_plus_d_api_sync(self):
        """La voie sync de SongBPM est retirée : rien à neutraliser."""
        for nom in ("search_track", "_ensure_driver", "_init_driver", "close"):
            assert not hasattr(SongBPMScraper, nom)


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
    """Les variantes async SOUS-CLASSENT : la logique de décision ne doit pas
    être redéfinie."""

    def test_songbpm_matching_identique(self):
        assert SongBPMScraperAsync._match_track is SongBPMScraper._match_track
        assert SongBPMScraperAsync._title_key is SongBPMScraper._title_key
        assert SongBPMScraperAsync._details_from_text is SongBPMScraper._details_from_text

    def test_spotify_logique_identique(self):
        assert SpotifyIDScraperAsync._calculate_relevance is SpotifyIDScraper._calculate_relevance
        # Lecture de l'embed : le point d'entrée du `__NEXT_DATA__` et les deux
        # vues qui en dérivent (artistes, identité) sont PARTAGÉS — seul le
        # TRANSPORT diffère.
        for nom in (
            "_parse_embed_entity",
            "_parse_embed_artists",
            "_identite_depuis_embed",
            "_titre_de_page",
            "_url_embed",
        ):
            assert _fonction_nue(SpotifyIDScraperAsync, nom) is _fonction_nue(SpotifyIDScraper, nom)
        assert (
            SpotifyIDScraperAsync.extract_spotify_id_from_url
            is SpotifyIDScraper.extract_spotify_id_from_url
        )

    def test_songbpm_async_utilise_le_matching_herite(self):
        """Vérification d'usage, pas seulement d'identité."""
        s = SongBPMScraperAsync(headless=True)
        assert s._match_track("Bande organisée", "Jul", "Bande organisée", "Jul") is True
        assert s._title_key("FREESTYLE BOOSKA-POGO") == "freestyle booska pogo"

    def test_spotify_async_partage_le_cache(self, tmp_path):
        s = SpotifyIDScraperAsync(cache_file=str(tmp_path / "c.json"))
        assert s._get_cache_key(" JUL ", " Titre ") == "jul::titre"
