"""La recherche par artiste doit couvrir les DEUX programmes RIAA.

Le site sépare le programme classique (Gold/Platinum/Diamond) du programme
latin (Oro/Platino/Diamante) en deux ONGLETS, et une recherche ne rend que
l'onglet demandé. N'interroger que celui par défaut — ce que faisait
`scrape_by_artist` — revenait à déclarer « aucune certification » pour un
artiste hispanophone qui en a des dizaines : l'absence était FABRIQUÉE par la
requête, pas constatée. C'est la même famille de défaut que le `absent` servi
en repli d'un cas non classé, en amont d'un cran : un manque produit par notre
propre façon de demander.

Aucun réseau ici : `_render` est remplacé, et on observe les URL demandées.
"""

from urllib.parse import parse_qs, urlparse

import pytest

from src.scrapers.riaa_scraper_v2 import RIAAScraperV2

_LIGNE = (
    '<tr class="table_award_row" id="default_{rid}">'
    '<td class="tw-artists_cell">{artiste}</td>'
    '<td class="others_cell">{titre}</td>'
    '<td class="others_cell">March 1, 2020</td>'
    '<td class="format_cell">ALBUM</td>'
    '<td class="others_cell"><img class="tw-atom-badge" src="/x.png" alt="badge {fam} level {n}"></td>'
    "</tr>"
)


def page(*lignes):
    return "<table>" + "".join(lignes) + "</table>"


@pytest.fixture
def scraper(monkeypatch):
    """Un scraper dont le rendu est simulé : il note les URL et sert du HTML."""
    s = RIAAScraperV2(headless=True)
    s.urls_demandees = []
    s.reponses = {}

    def faux_render(url, load_all, get_details):
        s.urls_demandees.append(url)
        onglet = parse_qs(urlparse(url).query).get("tab_active", [""])[0]
        return s.reponses.get(onglet)

    monkeypatch.setattr(s, "_render", faux_render)
    return s


def onglets(scraper):
    return [parse_qs(urlparse(u).query)["tab_active"][0] for u in scraper.urls_demandees]


class TestLesDeuxOngletsSontInterroges:
    def test_deux_requetes_une_par_programme(self, scraper):
        scraper.reponses = {"default-award": page(), "platinum-latin": page()}

        scraper.scrape_by_artist("Bad Bunny", get_details=False)

        assert onglets(scraper) == ["default-award", "platinum-latin"]

    def test_le_nom_part_dans_les_deux(self, scraper):
        scraper.reponses = {"default-award": page(), "platinum-latin": page()}

        scraper.scrape_by_artist("Bad Bunny", get_details=False)

        for url in scraper.urls_demandees:
            assert parse_qs(urlparse(url).query)["ar"] == ["Bad Bunny"]

    def test_les_resultats_des_deux_onglets_sont_reunis(self, scraper):
        scraper.reponses = {
            "default-award": page(
                _LIGNE.format(rid="1", artiste="LUIS FONSI", titre="DESPACITO", fam="DI", n="2")
            ),
            "platinum-latin": page(
                _LIGNE.format(rid="2", artiste="LUIS FONSI", titre="IMAGINA", fam="LA", n="61")
            ),
        }

        res = scraper.scrape_by_artist("Luis Fonsi", get_details=False)

        assert sorted(r["title"] for r in res) == ["DESPACITO", "IMAGINA"]

    def test_chaque_ligne_declare_son_programme(self, scraper):
        """Le programme vient du BADGE, pas de l'onglet : fusionner ne mélange rien."""
        scraper.reponses = {
            "default-award": page(
                _LIGNE.format(rid="1", artiste="LUIS FONSI", titre="DESPACITO", fam="DI", n="2")
            ),
            "platinum-latin": page(
                _LIGNE.format(rid="2", artiste="LUIS FONSI", titre="IMAGINA", fam="LA", n="61")
            ),
        }

        par_titre = {r["title"]: r for r in scraper.scrape_by_artist("Luis Fonsi", False)}

        assert par_titre["IMAGINA"]["award_family"] == "LA"
        assert par_titre["DESPACITO"]["award_family"] == "DI"
        assert par_titre["IMAGINA"]["award_programme"] != par_titre["DESPACITO"]["award_programme"]


class TestRobustesse:
    def test_une_ligne_vue_dans_les_deux_onglets_ne_compte_qu_une_fois(self, scraper):
        commune = _LIGNE.format(rid="1", artiste="SHAKIRA", titre="HIPS", fam="DI", n="2")
        scraper.reponses = {"default-award": page(commune), "platinum-latin": page(commune)}

        assert len(scraper.scrape_by_artist("Shakira", get_details=False)) == 1

    def test_un_onglet_muet_n_efface_pas_l_autre(self, scraper):
        """Un incident sur le latin ne doit pas emporter des certifs bien lues."""
        scraper.reponses = {
            "default-award": page(
                _LIGNE.format(rid="1", artiste="DAFT PUNK", titre="GET LUCKY", fam="DI", n="8")
            ),
            "platinum-latin": None,
        }

        res = scraper.scrape_by_artist("Daft Punk", get_details=False)

        assert [r["title"] for r in res] == ["GET LUCKY"]

    def test_les_deux_onglets_muets_rendent_une_liste_vide(self, scraper):
        scraper.reponses = {"default-award": None, "platinum-latin": None}

        assert scraper.scrape_by_artist("Inconnu", get_details=False) == []

    def test_le_second_onglet_est_interroge_meme_si_le_premier_est_tombe(self, scraper):
        scraper.reponses = {"default-award": None, "platinum-latin": page()}

        scraper.scrape_by_artist("X", get_details=False)

        assert "platinum-latin" in onglets(scraper)
