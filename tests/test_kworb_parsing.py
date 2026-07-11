"""Tests des parsers Kworb (pages statiques kworb.net) + _names_match.

HTML de fixture construit en ligne, d'après la structure documentée en tête
de kworb_scraper.py (validée en live le 2026-07-02, cf. JOURNAL).
"""

from datetime import datetime

from bs4 import BeautifulSoup

from src.scrapers.kworb_scraper import KworbScraper
from src.utils.update_kworb import _names_match


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


class TestParseNumber:
    def test_separateurs_virgules(self):
        assert KworbScraper._parse_number("20,706,079") == 20706079

    def test_espaces(self):
        assert KworbScraper._parse_number("1 234") == 1234

    def test_non_parseable(self):
        assert KworbScraper._parse_number("") is None
        assert KworbScraper._parse_number("abc") is None
        assert KworbScraper._parse_number(None) is None


class TestParseArtistName:
    """Le <title> Kworb sert de VALIDATION D'IDENTITÉ (bug historique Isha/Limsa)."""

    def test_page_songs(self):
        soup = _soup("<html><head><title>ISHA - Spotify Top Songs</title></head></html>")
        assert KworbScraper._parse_artist_name(soup) == "ISHA"

    def test_page_albums(self):
        soup = _soup("<html><head><title>Limsa d'Aulnay - Spotify Top Albums</title></head></html>")
        assert KworbScraper._parse_artist_name(soup) == "Limsa d'Aulnay"

    def test_sans_titre(self):
        assert KworbScraper._parse_artist_name(_soup("<html></html>")) is None


class TestParseLastUpdated:
    def test_date_presente(self):
        soup = _soup("<html><body>Last updated: 2026/07/02</body></html>")
        assert KworbScraper._parse_last_updated(soup) == datetime(2026, 7, 2)

    def test_date_absente(self):
        assert KworbScraper._parse_last_updated(_soup("<html><body>rien</body></html>")) is None

    def test_date_invalide(self):
        soup = _soup("<html><body>Last updated: 2026/13/45</body></html>")
        assert KworbScraper._parse_last_updated(soup) is None


TABLE_ENTRIES = """
<table class="addpos sortable">
  <tr><th>Song</th><th>Streams</th><th>Daily</th></tr>
  <tr>
    <td class="text"><div><a href="https://open.spotify.com/track/AAA111">Titre Lead</a></div></td>
    <td>20,706,079</td><td>5,432</td>
  </tr>
  <tr>
    <td class="text"><div>*<a href="https://open.spotify.com/track/BBB222">Titre Feat</a></div></td>
    <td>1,000</td><td></td>
  </tr>
  <tr>
    <td class="text"><div>Pas de lien ici</div></td>
    <td>999</td><td>1</td>
  </tr>
</table>
"""


class TestParseEntries:
    def _entries(self):
        return KworbScraper()._parse_entries(_soup(TABLE_ENTRIES))

    def test_ligne_lead(self):
        lead = self._entries()[0]
        assert lead == {
            "title": "Titre Lead",
            "streams": 20706079,
            "daily_streams": 5432,
            "spotify_id": "AAA111",
            "is_feature": False,
        }

    def test_ligne_feat_etoile(self):
        feat = self._entries()[1]
        assert feat["is_feature"] is True
        assert feat["spotify_id"] == "BBB222"
        assert feat["daily_streams"] == 0  # daily vide → 0

    def test_ligne_sans_lien_ignoree(self):
        assert len(self._entries()) == 2

    def test_sans_table_addpos(self):
        assert KworbScraper()._parse_entries(_soup("<table><tr></tr></table>")) == []


TABLE_SUMMARY = """
<table>
  <tr><th>Streams</th><th>Total</th><th>As lead</th><th>Solo</th><th>As feature</th></tr>
  <tr><td>Streams</td><td>100,000</td><td>80,000</td><td>50,000</td><td>20,000</td></tr>
  <tr><td>Daily</td><td>500</td><td>400</td><td>250</td><td>100</td></tr>
</table>
"""


class TestParseSummary:
    def test_recap_total_lead_solo_feature(self):
        summary = KworbScraper()._parse_summary(_soup(TABLE_SUMMARY))
        assert summary["streams"] == {
            "total": 100000,
            "as_lead": 80000,
            "solo": 50000,
            "as_feature": 20000,
        }
        assert summary["daily"]["total"] == 500

    def test_sans_table_recap(self):
        assert KworbScraper()._parse_summary(_soup("<html></html>")) is None


class TestNamesMatch:
    """Validation d'identité de la page Kworb (anti-homonyme, bug Isha/Limsa)."""

    def test_exact_et_casse(self):
        assert _names_match("ISHA", "Isha")

    def test_accents(self):
        assert _names_match("Maës", "Maes")

    def test_inclusion(self):
        assert _names_match("Isha", "Isha (officiel)")

    def test_artistes_differents(self):
        assert not _names_match("Limsa d'Aulnay", "Isha")

    def test_page_sans_nom(self):
        assert not _names_match(None, "Isha")
        assert not _names_match("", "Isha")
