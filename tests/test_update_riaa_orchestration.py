"""RIAA — autour du scraper : dernière date connue (absent / illisible /
sans date), fusion qui lève, `fetch_artist`, et les branches de `main()`.

Complète `test_riaa_raw.py` et `test_certifs_codes_de_sortie.py`. Aucun
navigateur : `RIAAScraperV2` est remplacé là où il est instancié.
"""

from datetime import datetime

import pytest

from src.utils import update_riaa as u
from tests import test_riaa_raw
from tests.test_certifs_codes_de_sortie import _lancer

# Fixtures du module voisin, réexposées sous leur nom (pytest les cherche dans
# l'espace du module ; ruff verrait une redéfinition avec un import direct).
riaa_tmp = test_riaa_raw.riaa_tmp
updater = test_riaa_raw.updater


class TestDerniereDate:
    def test_fichier_absent_amorce_en_2017(self, updater, riaa_tmp):
        assert updater.get_last_update_date() == datetime(2017, 10, 1)

    def test_fichier_illisible_rend_none_et_la_maj_refuse(self, updater, riaa_tmp, monkeypatch):
        """Le repli 2017 sur un fichier corrompu relançait ~108 tranches
        mensuelles ; on refuse et on le dit."""
        u.CERTIF_CSV.write_text("x", encoding="utf-8")

        def casse(*a, **k):
            raise OSError("verrouillé")

        monkeypatch.setattr(u.pd, "read_csv", casse)
        assert updater.get_last_update_date() is None
        assert updater.update_missing_months() is False
        assert not u.RIAA_META.exists()

    def test_sans_colonne_de_date_repart_de_2017(self, updater, riaa_tmp):
        u.CERTIF_CSV.write_text("Artist,Title\nA,B\n", encoding="utf-8-sig")
        assert updater.get_last_update_date() == datetime(2017, 10, 1)


class TestFusion:
    def test_fusion_qui_leve_rend_zero(self, updater, monkeypatch):
        def casse(rows):
            raise ValueError("colonne manquante")

        monkeypatch.setattr(u, "_merge_certif_csv", casse)
        assert updater.update_from_scraped_data([{"artist": "A"}]) == (0, 0)


class TestFetchArtist:
    def test_sans_resultat_rend_false(self, monkeypatch, riaa_tmp, capsys):
        class _Scraper:
            def __init__(self, headless=True):
                pass

            def scrape_by_artist(self, artist, get_details=False):
                return []

        monkeypatch.setattr("src.scrapers.riaa_scraper_v2.RIAAScraperV2", _Scraper)
        assert u.fetch_artist("Nobody") is False
        assert "Aucune certification" in capsys.readouterr().out

    def test_fusionne_les_resultats(self, monkeypatch, riaa_tmp):
        vus = {}

        class _Scraper:
            def __init__(self, headless=True):
                pass

            def scrape_by_artist(self, artist, get_details=False):
                vus["details"] = get_details
                return [{"artist": artist}]

        monkeypatch.setattr("src.scrapers.riaa_scraper_v2.RIAAScraperV2", _Scraper)
        monkeypatch.setattr(u, "_flatten_records", lambda r: [{"Artist": "Drake"}])
        monkeypatch.setattr(u, "_merge_certif_csv", lambda rows: (10, 1))
        assert u.fetch_artist("Drake") is True and vus["details"] is True


class TestMain:
    def test_clean_sort_en_1_sur_rapport_en_erreur(self, monkeypatch):
        monkeypatch.setattr(u, "clean_certif_csv", lambda apply=True: {"error": "brut vide"})
        monkeypatch.setattr(u, "format_clean_report", lambda r: "RAPPORT")
        assert _lancer(u, "--clean", "--dry-run") == 1

    def test_from_sans_to_sort_en_2(self, capsys):
        assert _lancer(u, "--from", "01-01-2024") == 2
        assert "--from et --to vont ensemble" in capsys.readouterr().out

    def test_periode(self, monkeypatch):
        vus = {}
        monkeypatch.setattr(u, "fetch_periode", lambda d, f: vus.update(d=d, f=f) or False)
        assert _lancer(u, "--from", "01-01-2024", "--to", "31-01-2024") == 1
        assert vus == {"d": "01-01-2024", "f": "31-01-2024"}

    def test_artistes_repetables(self, monkeypatch):
        vus = []
        monkeypatch.setattr(u, "fetch_artist", lambda n: vus.append(n) or n == "B")
        assert _lancer(u, "--artist", "A", "--artist", "B") == 0 and vus == ["A", "B"]
        monkeypatch.setattr(u, "fetch_artist", lambda n: False)
        assert _lancer(u, "--artist", "A") == 1

    def test_auto(self, monkeypatch):
        class _Updater:
            def update_missing_months(self):
                return True

        monkeypatch.setattr(u, "RIAADatabaseUpdater", _Updater)
        assert _lancer(u, "--auto") == 0

    def test_stats(self, monkeypatch, capsys):
        class _Updater:
            def get_statistics(self):
                return {
                    "total": 2,
                    "last_updated": "2024-01-01",
                    "by_level": {"Gold": 2},
                    "top_artists": [("Drake", 2)],
                }

        monkeypatch.setattr(u, "RIAADatabaseUpdater", _Updater)
        _lancer(u, "--stats")
        out = capsys.readouterr().out
        assert "Total certifications: 2" in out and "Drake: 2" in out


class TestMenuManuel:
    def test_choix_3_et_4_passent_par_les_entrees_decoupees(self, updater, monkeypatch):
        """Le menu appelait `scrape_by_date_range` d'un bloc — la route qui
        tronque à ~6 000 lignes — et `scrape_by_artist` sans la timeline."""
        vus = []
        monkeypatch.setattr(u, "fetch_periode", lambda d, f: vus.append(("periode", d, f)))
        monkeypatch.setattr(u, "fetch_artist", lambda n: vus.append(("artiste", n)))
        reponses = iter(["3", "01-01-2024", "31-01-2024"])
        monkeypatch.setattr("builtins.input", lambda *a: next(reponses))
        updater.manual_update()
        reponses = iter(["4", "Drake"])
        updater.manual_update()
        assert vus == [("periode", "01-01-2024", "31-01-2024"), ("artiste", "Drake")]


@pytest.fixture(autouse=True)
def _pas_de_scrape_reel(monkeypatch):
    monkeypatch.setattr(u, "RIAAScraper", lambda *a, **k: pytest.fail("scraper réel"))
