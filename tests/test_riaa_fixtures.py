"""Parsers RIAA v2 sur page réelle enregistrée (tests/fixtures/riaa/).

La fixture est une recherche par artiste rendue avec MORE DETAILS déclenché
(historique des paliers). Re-capture : scripts/capture_fixtures.py --only riaa.
Sentinelle : Daft Punk (catalogue RIAA stable et court).
"""

import re

import pytest

from src.scrapers.riaa_scraper_v2 import _parse_results, _to_iso, _units_for
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


def test_historique_details(results):
    """La fixture est capturée avec get_details=True → au moins un historique."""
    histories = [c["history"] for c in results if c.get("history")]
    assert histories, "aucun historique MORE DETAILS dans la fixture — re-capturer ?"
    for history in histories:
        for step in history:
            assert step["certification_level"]
            assert step["certification_date"] == "" or _ISO_RE.match(step["certification_date"])


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
