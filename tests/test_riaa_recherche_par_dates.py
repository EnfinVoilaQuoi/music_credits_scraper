"""Recherche RIAA par DATES : le palier écrit est celui de la DATE, pas le badge.

Fixture `riaa/search_by_date.html` : la journée du 2018-09-21, timelines
comprises (re-capture : `scripts/capture_fixtures.py --only riaa_search_by_date`).
Mesuré le 2026-10-10 : la page rend le badge COURANT de chaque award à la date
de la certification qui a matché — Kanye « ALL MINE » 3x Platinum au 2018-09-21,
jour de son Platinum. 659 lignes mal datées étaient entrées au clean ainsi. Le
palier de la date est sur le bouton de partage ; la timeline sert d'ORACLE.
"""

import logging

import pytest
from bs4 import BeautifulSoup

from src.scrapers.riaa_scraper_v2 import (
    _palier_de_la_date,
    _parse_main,
    _parse_results,
    _signaler_paliers_non_lus,
    _units_for,
)
from src.utils.update_riaa import _flatten_records
from tests.conftest import load_fixture

FIXTURE = "riaa/search_by_date.html"


@pytest.fixture(scope="module")
def avec_timeline():
    return _parse_results(load_fixture(FIXTURE), get_details=True)


@pytest.fixture(scope="module")
def sans_timeline():
    """Ce que le balayage lit réellement : la ligne principale seule."""
    return _parse_results(load_fixture(FIXTURE), get_details=False)


def _ligne(resultats, titre):
    return next(r for r in resultats if r["title"] == titre)


def test_palier_de_la_date_concorde_avec_la_timeline(avec_timeline):
    """L'oracle : chaque (palier, date) écrit figure dans l'historique de l'award."""
    assert avec_timeline
    for r in avec_timeline:
        paliers = {(h["certification_level"], h["certification_date"]) for h in r["history"]}
        assert paliers, f"timeline absente : {r['title']} — re-capturer ?"
        assert (r["certification_level"], r["certification_date"]) in paliers, r


def test_la_fixture_reste_representative(avec_timeline):
    """Au moins une ligne où le badge est FAUX pour la date : sans elle, la
    fixture ne pourrait plus montrer le défaut (re-capturer une autre journée)."""
    faux = [
        r
        for r in avec_timeline
        if (r["award_level"], r["certification_date"])
        not in {(h["certification_level"], h["certification_date"]) for h in r["history"]}
    ]
    assert faux
    assert all(r["award_level"] != r["certification_level"] for r in faux)


def test_all_mine(sans_timeline):
    r = _ligne(sans_timeline, "ALL MINE")
    assert r["award_level"] == "3x Platinum"  # badge courant (3x : 2023-06-14)
    assert r["certification_level"] == "Platinum"
    assert r["certification_date"] == "2018-09-21"
    assert r["units"] == _units_for("Platinum")
    assert r["palier_de_la_date_lu"]


def test_toutes_les_lignes_portent_le_palier_de_partage(sans_timeline):
    assert all(r["palier_de_la_date_lu"] for r in sans_timeline)


def test_le_balayage_ecrit_le_palier_de_la_date(sans_timeline):
    """Bout de chaîne : ce que `_flatten_records` verse au brut."""
    lignes = _flatten_records(sans_timeline)
    all_mine = [ligne for ligne in lignes if ligne["Title"] == "ALL MINE"]
    assert [(x["Certification_Type"], x["Certification_Date"]) for x in all_mine] == [
        ("Platinum", "2018-09-21")
    ]
    assert all_mine[0]["Units"] == "1000000"
    revival = next(x for x in lignes if x["Title"] == "REVIVAL")
    assert (revival["Certification_Type"], revival["Certification_Date"]) == (
        "Gold",
        "2018-09-21",
    )


def _row(alt="badge DI level 3", partage='data-share-img="https://x/icons/1_big.png"'):
    bouton = f'<button class="share_text" {partage}></button>' if partage is not None else ""
    html = f"""<table><tr class="table_award_row" id="default_1">
      <td class="tw-gnp-badge-td"><img class="tw-atom-badge" alt="{alt}" src="x/3_big.png"></td>
      <td class="tw-artists_cell">ARTISTE</td>
      <td class="others_cell">TITRE</td>
      <td class="others_cell">LABEL</td>
      <td class="others_cell format_cell">SINGLE</td>
      <td class="others_cell">September 21, 2018</td>
      <td class="tw-gnp-actions-td">{bouton}</td>
    </tr></table>"""
    return BeautifulSoup(html, "html.parser").select_one("tr")


def test_sans_bouton_de_partage_retombe_sur_le_badge():
    r = _parse_main(_row(partage=None))
    assert r["certification_level"] == r["award_level"] == "3x Platinum"
    assert not r["palier_de_la_date_lu"]


def test_famille_latine_prise_sur_le_badge():
    """Le nom de fichier du partage ne porte pas la famille : c'est l'award qui
    la donne (« badge LA level 3 » → Platino, pas Platinum)."""
    assert _palier_de_la_date(_row(alt="badge LA level 3"), "LA") == "Platino"
    r = _parse_main(_row(alt="badge LA level 3"))
    assert (r["award_level"], r["certification_level"]) == ("3x Platino", "Platino")


def test_partage_illisible():
    assert _palier_de_la_date(_row(partage='data-share-img="x.png"'), "DI") == ""


def test_signale_quand_aucune_ligne_ne_porte_le_palier(caplog):
    lignes = [_parse_main(_row(partage=None)) for _ in range(3)]
    with caplog.at_level(logging.WARNING):
        _signaler_paliers_non_lus(lignes)
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_silencieux_quand_tout_est_lu(caplog, sans_timeline):
    with caplog.at_level(logging.WARNING):
        _signaler_paliers_non_lus(sans_timeline)
    assert not caplog.records
