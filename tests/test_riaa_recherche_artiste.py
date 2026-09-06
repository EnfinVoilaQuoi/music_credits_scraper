"""L'onglet RIAA pilote l'AFFICHAGE, pas la recherche — mesuré, pas supposé.

Le site montre deux onglets, classique et « Premios de Oro y Platino ». Il est
tentant d'en conclure qu'une recherche ne rend que l'onglet demandé, donc qu'il
faut interroger les deux pour ne pas rater les awards latins d'un artiste
hispanophone. Ce raisonnement a été tenu, codé, puis **démenti par la mesure**
le 2026-09-06 : l'onglet par défaut rend TOUT, latin compris, et l'onglet latin
en est un sous-ensemble STRICT.

    Luis Fonsi   défaut 14 lignes (dont 13 latines) · latin 13 · latin \\ défaut = ∅
    Bad Bunny    défaut 93 lignes (dont 90 latines) · latin 90 · latin \\ défaut = ∅

La seconde requête ne rapportait rien et doublait le coût — avec la timeline,
90 allers-retours AJAX de plus pour Bad Bunny. Ces tests gèlent le constat pour
que personne (moi le premier) ne refasse le raisonnement sans refaire la mesure.

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
    '<td class="others_cell"><img class="tw-atom-badge" src="/x.png" '
    'alt="badge {fam} level {n}"></td>'
    "</tr>"
)


def page(*lignes):
    return "<table>" + "".join(lignes) + "</table>"


@pytest.fixture
def scraper(monkeypatch):
    """Un scraper dont le rendu est simulé : il note les URL et sert du HTML."""
    s = RIAAScraperV2(headless=True)
    s.urls_demandees = []
    s.reponse = page()

    def faux_render(url, load_all, get_details):
        s.urls_demandees.append(url)
        return s.reponse

    monkeypatch.setattr(s, "_render", faux_render)
    return s


def onglets(scraper):
    return [parse_qs(urlparse(u).query)["tab_active"][0] for u in scraper.urls_demandees]


class TestUneSeuleRequete:
    """Le cœur du constat : interroger deux fois serait payer pour rien."""

    def test_un_seul_aller_retour(self, scraper):
        scraper.scrape_by_artist("Bad Bunny", get_details=False)

        assert len(scraper.urls_demandees) == 1

    def test_sur_l_onglet_par_defaut_qui_rend_tout(self, scraper):
        scraper.scrape_by_artist("Bad Bunny", get_details=False)

        assert onglets(scraper) == ["default-award"]

    def test_le_nom_part_dans_la_requete(self, scraper):
        scraper.scrape_by_artist("Luis Fonsi", get_details=False)

        assert parse_qs(urlparse(scraper.urls_demandees[0]).query)["ar"] == ["Luis Fonsi"]

    def test_la_recherche_est_par_date_de_certification(self, scraper):
        """`release_date_toggle` absent = par date de SORTIE : le symptôme de la panne."""
        scraper.scrape_by_artist("Luis Fonsi", get_details=False)

        assert "release_date_toggle=1" in scraper.urls_demandees[0]


class TestLAwardLatinRevientDeLOngletParDefaut:
    """C'est ce qui rend la seconde requête inutile : rien n'y manque."""

    def test_les_deux_programmes_sortent_de_la_meme_page(self, scraper):
        scraper.reponse = page(
            _LIGNE.format(rid="1", artiste="LUIS FONSI", titre="DESPACITO US", fam="DI", n="2"),
            _LIGNE.format(rid="2", artiste="LUIS FONSI", titre="DESPACITO LA", fam="LA", n="61"),
        )

        res = scraper.scrape_by_artist("Luis Fonsi", get_details=False)

        assert len(scraper.urls_demandees) == 1
        assert sorted(r["title"] for r in res) == ["DESPACITO LA", "DESPACITO US"]

    def test_le_programme_vient_du_badge_pas_de_l_onglet(self, scraper):
        """La famille du badge classe la ligne : l'onglet n'y sert à rien."""
        scraper.reponse = page(
            _LIGNE.format(rid="1", artiste="LUIS FONSI", titre="DESPACITO US", fam="DI", n="2"),
            _LIGNE.format(rid="2", artiste="LUIS FONSI", titre="DESPACITO LA", fam="LA", n="61"),
        )

        par_titre = {r["title"]: r for r in scraper.scrape_by_artist("Luis Fonsi", False)}

        assert par_titre["DESPACITO LA"]["award_family"] == "LA"
        assert par_titre["DESPACITO US"]["award_family"] == "DI"
        assert (
            par_titre["DESPACITO LA"]["award_programme"]
            != par_titre["DESPACITO US"]["award_programme"]
        )

    def test_le_niveau_latin_garde_son_vocabulaire(self, scraper):
        """« 61x Platinum » était un niveau INVENTÉ par l'ancienne regex.

        Le nombre du badge est bien un multiplicateur — Despacito est mesuré à
        141x Platino — mais c'est le VOCABULAIRE qui distingue les programmes,
        et lui seul dit quelle échelle appliquer (Platino 60 000 unités contre
        Platinum 1 000 000).
        """
        scraper.reponse = page(
            _LIGNE.format(rid="1", artiste="LUIS FONSI", titre="VIDA", fam="LA", n="61")
        )

        niveau = scraper.scrape_by_artist("Luis Fonsi", False)[0]["award_level"]

        assert niveau == "61x Platino"
        assert "Platinum" not in niveau


class TestRobustesse:
    def test_une_page_non_rendue_donne_une_liste_vide(self, scraper):
        scraper.reponse = None

        assert scraper.scrape_by_artist("Inconnu", get_details=False) == []

    def test_une_page_sans_ligne_donne_une_liste_vide(self, scraper):
        assert scraper.scrape_by_artist("Inconnu", get_details=False) == []
