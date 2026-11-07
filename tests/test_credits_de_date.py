"""« Released in » et « Expected » ne sont pas des crédits (2026-11-06) : ni à l'entrée
Genius, ni dans l'existant (`scripts/retirer_credits_de_date.py`)."""

import pytest
from sqlalchemy import text

from src.models import Artist
from src.models.track import Track
from src.scrapers.genius_scraper_v3 import GeniusScraperV3
from src.utils import credits_genius_api

_PAGE = """<html><body>
<div class="Credit__Container-x"><div class="Credit__Label-x">Producer</div>
<div class="Credit__Contributor-x"><div><a href="#">Kanye West</a></div></div></div>
<div class="Credit__Container-x"><div class="Credit__Label-x">Released in</div>
<div class="Credit__Contributor-x">2016</div></div>
<div class="Credit__Container-x"><div class="Credit__Label-x">Expected</div>
<div class="Credit__Contributor-x">October 16, 2026</div></div>
</body></html>"""


def test_le_scrape_ne_les_enregistre_plus():
    scraper = GeniusScraperV3.__new__(GeniusScraperV3)
    assert {c.name for c in scraper._extract_fallback_bs4(_PAGE)} == {"Kanye West"}


def test_la_voie_api_ne_les_enregistre_plus():
    song = {
        "custom_performances": [
            {"label": "Released in", "artists": [{"name": "2016"}]},
            {"label": "Expected", "artists": [{"name": "2026"}]},
            {"label": "Mixed By", "artists": [{"name": "Mike Dean"}]},
        ]
    }
    assert [c.name for c in credits_genius_api.credits_du_detail(song)] == ["Mike Dean"]


class TestRattrapage:
    @pytest.fixture
    def fiche(self, data_manager):
        artiste = Artist(name="Kanye West")
        artiste.id = data_manager.save_artist(artiste)
        data_manager.save_track(Track(title="Golden", artist=artiste, genius_id=1))
        tid = data_manager.get_artist_tracks(artiste.id)[0].id
        lignes = [
            ("2016", "Other", "Released in"),
            ("October 16, 2026", "Other", "Expected"),
            ("1993", "Producer", None),
            ("2002", "Other", "Recorded At"),
            ("Mike Dean", "Other", "Mixed By"),
        ]
        with data_manager.engine.begin() as conn:
            for nom, role, detail in lignes:
                conn.execute(
                    text(
                        "INSERT INTO credits (track_id, name, role, role_detail, source) "
                        "VALUES (:t, :n, :r, :d, 'genius')"
                    ),
                    {"t": tid, "n": nom, "r": role, "d": detail},
                )
        return tid

    def _noms(self, data_manager, tid):
        with data_manager.engine.connect() as conn:
            return {
                r[0]
                for r in conn.execute(
                    text("SELECT name FROM credits WHERE track_id = :t"), {"t": tid}
                )
            }

    def _lancer(self, monkeypatch, data_manager, *args):
        import src.utils.data_manager as module_dm
        from scripts import retirer_credits_de_date as script

        monkeypatch.setattr(module_dm, "DataManager", lambda: data_manager)
        monkeypatch.setattr("sys.argv", ["retirer_credits_de_date.py", *args])

        class _Sauvegarde:
            def create_backup(self, nom):
                return nom

        monkeypatch.setattr("src.utils.database_backup.get_backup_manager", lambda: _Sauvegarde())
        return script.main()

    def test_a_blanc_rien_ne_part(self, data_manager, fiche, monkeypatch, capsys):
        assert self._lancer(monkeypatch, data_manager) == 0
        sortie = capsys.readouterr().out
        assert "2 ligne(s) à retirer" in sortie and "(laissé)" in sortie
        assert "2016" in self._noms(data_manager, fiche)

    def test_apply_ne_retire_que_les_metadonnees(self, data_manager, fiche, monkeypatch):
        assert self._lancer(monkeypatch, data_manager, "--apply") == 0
        assert self._noms(data_manager, fiche) == {"1993", "2002", "Mike Dean"}
        assert data_manager.credits_hors_credits() == []
