"""Parsers Genius v3 (BeautifulSoup) sur page réelle enregistrée.

Genius est LA source qui a cassé le plus souvent (v1→v2→v3) : ces tests
rejouent les extracteurs BS4 sur une vraie page de morceau capturée.
Re-capture : scripts/capture_fixtures.py --only genius.
Sentinelle : Josman — Dans le vide (album Matrix).

Le fallback LLM (_extract_with_llm, Ollama) n'est volontairement PAS testé.
"""

import pytest
from bs4 import BeautifulSoup

from src.models import Credit, CreditRole
from src.scrapers.genius_scraper_v3 import GeniusScraperV3
from tests.conftest import load_fixture

FIXTURE = "genius/song_page.html"


@pytest.fixture(scope="module")
def scraper():
    # __init__ sonde Ollama (LLMExtractor.is_available) et construit la config
    # navigateur → esquivé : les extracteurs BS4 n'utilisent aucun état d'instance.
    return GeniusScraperV3.__new__(GeniusScraperV3)


@pytest.fixture(scope="module")
def html():
    return load_fixture(FIXTURE)


@pytest.fixture(scope="module")
def soup(html):
    return BeautifulSoup(html, "html.parser")


def test_lyrics(scraper, soup):
    lyrics = scraper._extract_lyrics_bs4(soup)
    assert len(lyrics) > 200, "paroles vides/tronquées — conteneurs data-lyrics-container changés ?"
    assert "\n" in lyrics
    assert "vide" in lyrics.lower()  # « Dans le vide »
    assert "You might also like" not in lyrics
    assert not lyrics.endswith("Embed")


def test_album(scraper, html):
    album = scraper._extract_album_bs4(html)
    assert album is not None, "album introuvable — lien /albums/ changé ?"
    assert "matrix" in album.lower()


def test_anecdotes(scraper, soup):
    anecdotes = scraper._extract_anecdotes_bs4(soup)
    assert anecdotes is not None, "section About introuvable — SongDescription__Content changé ?"
    assert len(anecdotes) > 50


def test_credits_fallback_bs4(scraper, html):
    credits = scraper._extract_fallback_bs4(html)
    assert len(credits) >= 2, "aucun crédit extrait — DOM Credit__Container changé ?"
    assert all(isinstance(c, Credit) for c in credits)
    assert all(c.name for c in credits)
    assert all(c.source == "genius" for c in credits)
    roles = {c.role for c in credits}
    assert roles & {CreditRole.PRODUCER, CreditRole.WRITER}, roles


@pytest.mark.parametrize(
    ("genius_role", "expected"),
    [
        ("Producer", CreditRole.PRODUCER),
        ("Writers", CreditRole.WRITER),
        ("Mixing Engineer", CreditRole.MIXING_ENGINEER),
        ("Video Line Producer", CreditRole.VIDEO_PRODUCER),
        ("Rôle Exotique Inconnu", CreditRole.OTHER),
    ],
)
def test_map_genius_role_to_enum(scraper, genius_role, expected):
    assert scraper._map_genius_role_to_enum(genius_role) == expected


def test_deduplicate_credits(scraper):
    credits = [
        Credit(name="Myth Syzer", role=CreditRole.PRODUCER),
        Credit(name="myth syzer ", role=CreditRole.PRODUCER),  # doublon (casse/espaces)
        Credit(name="Myth Syzer", role=CreditRole.WRITER),  # même nom, autre rôle → gardé
    ]
    unique = scraper._deduplicate_credits(credits)
    assert [(c.name, c.role) for c in unique] == [
        ("Myth Syzer", CreditRole.PRODUCER),
        ("Myth Syzer", CreditRole.WRITER),
    ]
