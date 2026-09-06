"""Parsers RIAA v2 sur page réelle enregistrée (tests/fixtures/riaa/).

La fixture est une recherche par artiste rendue avec la timeline déclenchée
(historique des paliers). Re-capture : scripts/capture_fixtures.py --only riaa.
Sentinelle : Daft Punk (catalogue RIAA stable et court).

**Ces tests ont laissé passer une panne de deux mois** : le site a été refait le
2026-09-06, le scraper rendait 0 ligne en production, et la suite restait verte
parce qu'elle rejouait une page enregistrée AVANT la refonte. Le filet ne peut
donc pas être « le parseur sait lire cette page » — il faut aussi que la page
enregistrée reste représentative. D'où, ci-dessous, des assertions sur ce que le
site fournit RÉELLEMENT (niveau sur chaque ligne, historique daté, date de sortie
et genre) : une prochaine refonte les fera tomber dès la re-capture.
"""

import re

import pytest

from src.scrapers.riaa_scraper_v2 import (
    _level_from_img,
    _level_from_label,
    _parse_results,
    _to_iso,
    _units_for,
)
from tests.conftest import load_fixture

FIXTURE = "riaa/search_results.html"
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LEVEL_RE = re.compile(r"^(Gold|Platinum|Diamond|\d+x Platinum)$")


@pytest.fixture(scope="module")
def results():
    return _parse_results(load_fixture(FIXTURE), get_details=True)


def test_certifications_extraites(results):
    assert len(results) >= 3
    assert any("daft punk" in r["artist"].lower() for r in results)


def test_structure_ligne_principale(results):
    for cert in results:
        assert cert["artist"]
        assert cert["title"]
        assert _LEVEL_RE.match(cert["award_level"]), cert["award_level"]
        assert cert["certification_level"] == cert["award_level"]
        assert cert["units"] == _units_for(cert["award_level"])
        assert cert["units"] and cert["units"] >= 500_000
        # _to_iso a déjà été appliqué par _parse_main
        assert cert["certification_date"] == "" or _ISO_RE.match(cert["certification_date"])


def test_toutes_les_lignes_sont_extraites(results):
    """Aucune ligne du tableau ne doit être silencieusement écartée.

    C'est l'assertion qui manquait : après la refonte, `_parse_main` rendait
    `None` pour CHAQUE ligne (cellule artiste renommée) et le scraper se
    contentait de dire « 0 certification extraite ».
    """
    from src.scrapers.riaa_scraper_v2 import _count_award_rows

    assert len(results) == _count_award_rows(load_fixture(FIXTURE))


def test_historique_details(results):
    """La fixture est capturée avec get_details=True → au moins un historique."""
    histories = [c["history"] for c in results if c.get("history")]
    assert histories, "aucun historique (timeline) dans la fixture — re-capturer ?"
    for history in histories:
        for step in history:
            assert step["certification_level"]
            assert step["certification_date"] == "" or _ISO_RE.match(step["certification_date"])


def test_historique_porte_sortie_et_genre(results):
    """La timeline donne la date de SORTIE et le GENRE — colonnes du CSV qui
    étaient déclarées mais restaient vides avec l'ancien bloc de détails."""
    etapes = [step for cert in results for step in cert.get("history") or []]
    assert etapes, "aucune étape d'historique — re-capturer ?"
    assert any(_ISO_RE.match(step.get("release_date") or "") for step in etapes)
    assert any((step.get("genre") or "").strip() for step in etapes)


def test_paliers_croissants_dans_le_temps(results):
    """Un palier plus récent ne peut pas être inférieur au précédent.

    Contrôle de cohérence bon marché : il attrape un appariement date↔niveau
    décalé d'une ligne, faute que l'œil ne voit pas dans un tableau.
    """
    for cert in results:
        etapes = [s for s in cert.get("history") or [] if s["certification_date"] and s["units"]]
        par_date = sorted(etapes, key=lambda s: s["certification_date"])
        unites = [s["units"] for s in par_date]
        assert unites == sorted(unites), (cert["title"], unites)


@pytest.mark.parametrize(
    ("src", "alt", "level"),
    [
        # Nouveau gabarit : l'alt porte la FAMILLE d'award.
        (".../icons/0_big.png", "badge DI level 0", "Gold"),
        (".../icons/2_big.png", "badge DI level 2", "2x Platinum"),
        (".../icons/10_big.png", "badge ST level 10", "Diamond"),
        # Awards LATINS : mêmes numéros, autre vocabulaire. L'ancienne lecture
        # du seul nom de fichier annonçait « 61x Platinum » — un niveau inventé.
        (".../icons/la_61_big.png", "badge LA level 61", "61x Platino"),
        (".../icons/la_0_big.png", "badge LA level 0", "Oro"),
        # Ancien gabarit (pages enregistrées avant la refonte) : pas d'alt.
        ("1_big.png", "", "Platinum"),
        ("", "", ""),
    ],
)
def test_level_from_img(src, alt, level):
    assert _level_from_img(src, alt) == level


@pytest.mark.parametrize(
    ("label", "level"),
    [
        ("15X PLATINUM", "15x Platinum"),
        ("GOLD", "Gold"),
        ("DIAMOND", "Diamond"),
        ("ORO", "Oro"),
        ("61X PLATINO", "61x Platino"),
        ("", ""),
    ],
)
def test_level_from_label(label, level):
    assert _level_from_label(label) == level


@pytest.mark.parametrize(
    ("raw", "iso"),
    [
        ("April 10, 2026", "2026-04-10"),
        ("Feb 3, 1999", "1999-02-03"),
        ("11/22/2013", "2013-11-22"),
        ("2020-01-01", "2020-01-01"),
        ("", ""),
    ],
)
def test_to_iso(raw, iso):
    assert _to_iso(raw) == iso


class TestProgrammeDAward:
    """La FAMILLE du badge est relevée à la source, pas devinée du libellé.

    Un award latin (`alt="badge LA level 61"`) est un autre programme, avec une
    autre échelle : Platino 60 000 unités contre 1 000 000 pour Platinum.
    L'ancien code lisait `la_61_big.png` comme « 61x Platinum » — 61 millions
    d'unités annoncées pour 3,66.
    """

    def test_toutes_les_lignes_portent_leur_programme(self, results):
        assert all(c["award_programme"] for c in results)

    def test_la_sentinelle_est_un_catalogue_classique(self, results):
        """Daft Punk : aucun award latin attendu."""
        assert {c["award_programme"] for c in results} == {"US"}

    @pytest.mark.parametrize(
        ("alt", "niveau", "famille"),
        [
            ("badge DI level 2", "2x Platinum", "DI"),
            ("badge ST level 0", "Gold", "ST"),
            ("badge LA level 61", "61x Platino", "LA"),
            ("badge LA level 0", "Oro", "LA"),
            ("", "", ""),
        ],
    )
    def test_badge_infos(self, alt, niveau, famille):
        from src.scrapers.riaa_scraper_v2 import _badge_infos

        assert _badge_infos("", alt) == (niveau, famille)

    def test_le_programme_suit_la_famille_du_badge(self):
        """Et non le vocabulaire : c'est la source qui sait."""
        from bs4 import BeautifulSoup

        from src.scrapers.riaa_scraper_v2 import _parse_main

        html = (
            '<tr class="table_award_row" id="default_1">'
            '<td><img class="tw-atom-badge" src="la_61_big.png" alt="badge LA level 61"></td>'
            '<td class="tw-artists_cell">ROMEO SANTOS</td>'
            '<td class="others_cell">ODIO</td>'
            '<td class="others_cell">SONY LATIN</td>'
            '<td class="others_cell format_cell">SINGLE</td>'
            '<td class="others_cell tw-text-center">August 17, 2026</td></tr>'
        )
        ligne = _parse_main(BeautifulSoup(html, "html.parser").select_one("tr"))

        assert ligne["award_programme"] == "LATIN"
        assert ligne["certification_level"] == "61x Platino"
        assert ligne["units"] == 61 * 60_000  # et non 61 millions
