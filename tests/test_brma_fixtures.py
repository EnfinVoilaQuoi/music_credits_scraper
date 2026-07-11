"""Parsers BRMA/Ultratop sur page réelle enregistrée (tests/fixtures/brma/).

Fixture = une page annuelle or-platine (2021/singles) fetchée via l'anti-CF du
projet. Re-capture : scripts/capture_fixtures.py --only brma.
"""

import logging
import re

import pytest
from bs4 import BeautifulSoup

from src.scrapers.scraper_brma import UltratopScraperInitial
from tests.conftest import load_fixture

FIXTURE = "brma/ultratop_2021_singles.html"
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@pytest.fixture(scope="module")
def scraper():
    # __init__ crée le dossier de sortie + un FileHandler de log → esquivé :
    # les méthodes testées n'utilisent que self.logger et les parseurs purs.
    s = UltratopScraperInitial.__new__(UltratopScraperInitial)
    s.logger = logging.getLogger("test_brma_fixtures")
    # Neutraliser le fallback LLM : si les sélecteurs ne trouvent rien, on veut
    # un test rouge, pas un appel Ollama.
    s._extract_with_llm = lambda soup, year, category: []
    return s


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("08/02/2019: Platine", [("2019-02-08", "Platine")]),
        (
            "01/03/2020: Or 05/06/2021: 2x Platine",
            [("2020-03-01", "Or"), ("2021-06-05", "2x Platine")],
        ),
        ("pas de certification ici", []),
    ],
)
def test_parse_certification_date(scraper, text, expected):
    assert scraper.parse_certification_date(text) == expected


def test_extract_certifications(scraper):
    soup = BeautifulSoup(load_fixture(FIXTURE), "html.parser")
    certifications = scraper.extract_certifications(soup, 2021, "singles")

    assert len(certifications) >= 10, "sélecteurs Ultratop sans résultat — structure changée ?"
    for cert in certifications:
        assert cert["artist"]
        assert cert["category"] == "singles"
        assert cert["year_page"] == 2021
        assert cert["certification_level"]
        assert _ISO_RE.match(cert["certification_date"]), cert["certification_date"]
        assert cert["detail_url"].startswith("https://www.ultratop.be")
