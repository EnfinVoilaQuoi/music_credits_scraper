"""Cache, pertinence et vote d'identité du scraper Spotify (`spotify_id_scraper_v2`).

371 statements à 24 %. Complète `test_spotify_id_extraction.py` (extraction d'ID
depuis une URL) et `test_spotify_embed_fixtures.py` (parseur `__NEXT_DATA__` sur
page réelle) en couvrant ce qui restait : le cache disque, le score de
pertinence, le nettoyage de titre de page et surtout `get_artist_id_from_track`
— le vote NAME-AWARE qui empêche le bug historique Isha × Limsa d'Aulnay.

Aucun navigateur : `__init__` est paresseux (driver au premier usage). Attention
en revanche, il LIT son fichier de cache dès la construction — les tests lui
passent toujours un chemin en `tmp_path`.
"""

import json

import pytest

from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper


@pytest.fixture
def scraper(tmp_path):
    """Scraper au cache isolé (le défaut lirait `spotify_ids_cache.json` du cwd)."""
    return SpotifyIDScraper(cache_file=str(tmp_path / "cache.json"), headless=True)


class TestCache:
    def test_cache_absent(self, scraper):
        assert scraper.cache == {}

    def test_aller_retour(self, tmp_path):
        chemin = tmp_path / "cache.json"
        s = SpotifyIDScraper(cache_file=str(chemin))
        s.cache["jul::bande organisée"] = "SP1"
        s._save_cache()
        assert SpotifyIDScraper(cache_file=str(chemin)).cache == {"jul::bande organisée": "SP1"}

    def test_cache_corrompu_reparti_de_zero(self, tmp_path):
        """Un JSON tronqué ne doit pas empêcher le scraper de démarrer."""
        chemin = tmp_path / "cache.json"
        chemin.write_text("{tronqué", encoding="utf-8")
        assert SpotifyIDScraper(cache_file=str(chemin)).cache == {}

    def test_ecriture_impossible_journalisee(self, tmp_path, monkeypatch, caplog):
        s = SpotifyIDScraper(cache_file=str(tmp_path / "cache.json"))
        monkeypatch.setattr(
            "builtins.open", lambda *a, **k: (_ for _ in ()).throw(OSError("disque plein"))
        )
        s._save_cache()  # ne lève pas
        assert "Erreur sauvegarde cache" in caplog.text

    def test_cle_insensible_a_la_casse_et_aux_espaces(self, scraper):
        """Sans normalisation, « Jul » et « jul  » ouvriraient deux entrées pour
        le même morceau — et un scrape serait refait pour rien."""
        assert scraper._get_cache_key("  JUL ", " Bande Organisée  ") == scraper._get_cache_key(
            "jul", "bande organisée"
        )

    def test_accents_conserves(self, scraper):
        """La clé ne déaccentue PAS : deux titres différents restent distincts."""
        assert scraper._get_cache_key("a", "éte") != scraper._get_cache_key("a", "ete")

    def test_cache_lisible_a_l_oeil(self, tmp_path):
        chemin = tmp_path / "cache.json"
        s = SpotifyIDScraper(cache_file=str(chemin))
        s.cache["angèle::balance"] = "SP1"
        s._save_cache()
        assert "angèle" in chemin.read_text(encoding="utf-8")  # ensure_ascii=False


class TestPertinence:
    def test_artiste_et_titre_presents(self, scraper):
        score = scraper._calculate_relevance("Jul", "Bande organisée", "Jul - Bande organisée")
        assert score == pytest.approx(1.0)

    def test_texte_absent_donne_un_score_neutre(self, scraper):
        """Pas de texte = pas d'information : ni bon ni mauvais signal."""
        assert scraper._calculate_relevance("Jul", "Titre", "") == 0.5

    def test_artiste_seul(self, scraper):
        score = scraper._calculate_relevance("Jul", "Bande organisée", "Jul - Autre chose")
        assert 0.4 <= score < 1.0

    def test_aucune_correspondance(self, scraper):
        assert scraper._calculate_relevance("Jul", "Titre", "Rien de commun") == 0.0

    def test_apostrophes_typographiques_unifiees(self, scraper):
        """Spotify écrit « L'empire », notre base « L'empire » — la différence
        d'apostrophe ne doit pas faire chuter le score."""
        score = scraper._calculate_relevance("Jul", "L’empire", "Jul - L'empire")
        assert score == pytest.approx(1.0)

    def test_score_plafonne_a_un(self, scraper):
        score = scraper._calculate_relevance(
            "Jul Ninho SCH", "Bande organisée ensemble", "Jul Ninho SCH Bande organisée ensemble"
        )
        assert score == 1.0

    def test_mots_courts_ignores(self, scraper):
        """Les mots de 1-2 lettres matcheraient partout : ils ne comptent pas."""
        assert scraper._calculate_relevance("Le", "La", "Le texte de La chose") < 1.0

    @pytest.mark.parametrize("apostrophe", ["’", "‘", "`", "´"])
    def test_normalisation_des_apostrophes(self, apostrophe):
        assert SpotifyIDScraper._normalize_apostrophes(f"L{apostrophe}empire") == "L'empire"


class TestParseurEmbed:
    """Complète `test_spotify_embed_fixtures.py` sur les cas dégradés."""

    def _html(self, entity):
        payload = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
        return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'

    def test_artistes_extraits(self):
        html = self._html(
            {
                "artists": [
                    {"name": "Isha", "uri": "spotify:artist:AAA"},
                    {"name": "Limsa d'Aulnay", "uri": "spotify:artist:BBB"},
                ]
            }
        )
        assert SpotifyIDScraper._parse_embed_artists(html) == [
            {"name": "Isha", "id": "AAA"},
            {"name": "Limsa d'Aulnay", "id": "BBB"},
        ]

    def test_uri_non_artiste_ignoree(self):
        html = self._html({"artists": [{"name": "X", "uri": "spotify:album:AAA"}]})
        assert SpotifyIDScraper._parse_embed_artists(html) == []

    def test_sans_bloc_next_data(self):
        assert SpotifyIDScraper._parse_embed_artists("<html>rien</html>") == []

    def test_json_invalide(self):
        html = '<script id="__NEXT_DATA__">{ceci n\'est pas du JSON</script>'
        assert SpotifyIDScraper._parse_embed_artists(html) == []

    def test_structure_inattendue(self):
        """Spotify change son état interne sans prévenir : on rend [] plutôt que
        de propager une exception au milieu d'un enrichissement."""
        html = '<script id="__NEXT_DATA__">{"props": {}}</script>'
        assert SpotifyIDScraper._parse_embed_artists(html) == []

    def test_html_vide(self):
        assert SpotifyIDScraper._parse_embed_artists("") == []
        assert SpotifyIDScraper._parse_embed_artists(None) == []


class TestVoteIdentiteArtiste:
    """Garde-fou anti-Limsa : sur un projet commun, le PREMIER crédité n'est pas
    forcément le nôtre. Bug historique JOURNAL 2026-07-02."""

    def _scraper(self, tmp_path, artistes):
        s = SpotifyIDScraper(cache_file=str(tmp_path / "c.json"))
        s.get_track_artists = lambda tid: artistes
        return s

    def test_nom_attendu_choisi(self, tmp_path):
        s = self._scraper(
            tmp_path,
            [{"name": "Limsa d'Aulnay", "id": "LIMSA"}, {"name": "Isha", "id": "ISHA"}],
        )
        assert s.get_artist_id_from_track("T1", expected_name="Isha") == "ISHA"

    def test_sans_nom_attendu_le_premier_gagne(self, tmp_path):
        s = self._scraper(
            tmp_path, [{"name": "Limsa d'Aulnay", "id": "LIMSA"}, {"name": "Isha", "id": "ISHA"}]
        )
        assert s.get_artist_id_from_track("T1") == "LIMSA"

    def test_nom_absent_le_morceau_ne_vote_pas(self, tmp_path):
        """C'EST la correction du bug : plutôt que de laisser le premier crédité
        rafler le vote, le morceau s'abstient."""
        s = self._scraper(tmp_path, [{"name": "Limsa d'Aulnay", "id": "LIMSA"}])
        assert s.get_artist_id_from_track("T1", expected_name="Isha") is None

    def test_correspondance_par_inclusion(self, tmp_path):
        s = self._scraper(tmp_path, [{"name": "Isha", "id": "ISHA"}])
        assert s.get_artist_id_from_track("T1", expected_name="Isha (rappeur)") == "ISHA"

    @pytest.mark.parametrize(
        ("attendu", "credite"),
        [
            ("Isha", "Misha Van Der Werf"),
            ("SCH", "ScHoolboy Q"),
            ("IAM", "Williams"),
        ],
    )
    def test_homonyme_par_sous_chaine_ne_vote_pas(self, tmp_path, attendu, credite):
        """L'inclusion était comparée par SOUS-CHAÎNE nue jusqu'au 2026-09-05 :
        un homonyme raflait le vote, et cet ID artiste irrigue ensuite ReccoBeats,
        Kworb et les streams — le faux positif ne se voyait qu'en bout de chaîne."""
        s = self._scraper(tmp_path, [{"name": credite, "id": "ETRANGER"}])
        assert s.get_artist_id_from_track("T1", expected_name=attendu) is None

    def test_inclusion_en_mot_entier_conservee(self, tmp_path):
        """Le relâchement utile survit : « Jul » est bien un mot de « Jul & SCH »."""
        s = self._scraper(tmp_path, [{"name": "Jul & SCH", "id": "COMMUN"}])
        assert s.get_artist_id_from_track("T1", expected_name="Jul") == "COMMUN"

    def test_apostrophes_typographiques(self, tmp_path):
        s = self._scraper(tmp_path, [{"name": "Limsa d’Aulnay", "id": "LIMSA"}])
        assert s.get_artist_id_from_track("T1", expected_name="Limsa d'Aulnay") == "LIMSA"

    def test_aucun_credite(self, tmp_path):
        s = self._scraper(tmp_path, [])
        assert s.get_artist_id_from_track("T1", expected_name="Isha") is None

    def test_nom_credite_vide_ignore(self, tmp_path):
        s = self._scraper(tmp_path, [{"name": "", "id": "VIDE"}, {"name": "Isha", "id": "ISHA"}])
        assert s.get_artist_id_from_track("T1", expected_name="Isha") == "ISHA"


class TestTitreDePage:
    def test_suffixe_spotify_retire(self):
        assert (
            SpotifyIDScraper._clean_page_title("Bande organisée • Jul | Spotify")
            == "Bande organisée • Jul"
        )

    @pytest.mark.parametrize(
        "generique",
        ["", "Spotify", "spotify – Web Player", "Spotify - Web Player", "Web Player"],
    )
    def test_titres_generiques_normalises_en_none(self, generique):
        """Spotify sert parfois une coquille sans « Titre • Artiste » : la garder
        polluerait la base d'une valeur qui ne vérifie rien."""
        assert SpotifyIDScraper._clean_page_title(generique) is None

    def test_titre_absent(self):
        assert SpotifyIDScraper._clean_page_title(None) is None

    def test_titre_utile_conserve(self):
        assert SpotifyIDScraper._clean_page_title("  Titre • Artiste  ") == "Titre • Artiste"
