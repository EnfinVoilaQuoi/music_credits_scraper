"""Parsers Kworb sur page réelle enregistrée (tests/fixtures/kworb/).

Complète test_kworb_parsing.py (HTML minimal en ligne) : ici on rejoue les
parseurs sur une vraie page artiste capturée — si Kworb change sa structure,
re-capturer (scripts/capture_fixtures.py --only kworb) et le rouge ci-dessous
localise la casse. Sentinelle : Josman (cf. capture_fixtures.CAPTURES).
"""

import re
from datetime import datetime

import pytest
from bs4 import BeautifulSoup

from src.scrapers.kworb_scraper import KworbScraper
from tests.conftest import load_fixture

FIXTURE = "kworb/artist_songs.html"
_SPOTIFY_ID_RE = re.compile(r"^[A-Za-z0-9]{22}$")


@pytest.fixture(scope="module")
def soup():
    return BeautifulSoup(load_fixture(FIXTURE), "html.parser")


def test_artist_name(soup):
    assert KworbScraper._parse_artist_name(soup) == "Josman"


def test_last_updated(soup):
    updated = KworbScraper._parse_last_updated(soup)
    assert isinstance(updated, datetime)
    assert 2015 < updated.year < 2100


def test_summary(soup):
    summary = KworbScraper()._parse_summary(soup)
    assert summary is not None
    assert "streams" in summary
    streams = summary["streams"]
    assert set(streams) == {"total", "as_lead", "solo", "as_feature"}
    assert streams["total"] > 1_000_000
    assert streams["total"] >= streams["as_lead"] >= streams["solo"]


def test_entries(soup):
    entries = KworbScraper()._parse_entries(soup)
    assert len(entries) >= 10

    for entry in entries:
        assert set(entry) == {"title", "streams", "daily_streams", "spotify_id", "is_feature"}
        assert entry["title"]
        assert isinstance(entry["streams"], int) and entry["streams"] > 0
        assert isinstance(entry["daily_streams"], int)
        assert isinstance(entry["is_feature"], bool)

    # Les IDs Spotify (base du matching Kworb v2) doivent être extraits des href
    with_id = [e for e in entries if e["spotify_id"]]
    assert len(with_id) >= len(entries) * 0.9
    assert all(_SPOTIFY_ID_RE.match(e["spotify_id"]) for e in with_id)
