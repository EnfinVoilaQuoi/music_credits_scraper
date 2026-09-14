"""BRMA — ce qui entoure le parseur : relances, dédup du brut, écriture
atomique, rattrapage des pages manquantes, runs manuel/programmé.

Aucun navigateur : `fetch_page` est remplacé, `time.sleep` neutralisé, et
l'updater vit dans `tmp_path` (il écrit des logs au setup).
"""

import pandas as pd
import pytest
from bs4 import BeautifulSoup

from src.utils import update_brma as ub
from src.utils.update_brma import UltratopUpdater


@pytest.fixture(autouse=True)
def _sans_attente(monkeypatch):
    monkeypatch.setattr(ub.time, "sleep", lambda *_: None)
    monkeypatch.setattr(ub.random, "uniform", lambda a, b: 0)


@pytest.fixture
def updater(tmp_path):
    return UltratopUpdater(
        database_path=str(tmp_path / "certif_brma.csv"), output_dir=str(tmp_path)
    )


def _soup(html="<div></div>"):
    return BeautifulSoup(html, "html.parser")


class TestLoggerPrint:
    def test_stdout_ferme_ne_casse_pas_le_run(self, updater, monkeypatch, caplog):
        def casse(*a, **k):
            raise ValueError("I/O operation on closed file")

        monkeypatch.setattr("builtins.print", casse)
        with caplog.at_level("INFO"):
            updater.logger_print("message")
        assert any(r.message == "message" for r in caplog.records)


class TestFetchPageWithRetry:
    def test_relance_jusqu_au_succes(self, updater, monkeypatch):
        reponses = [None, None, _soup()]
        attentes = []
        monkeypatch.setattr(updater, "fetch_page", lambda y, c: reponses.pop(0))
        monkeypatch.setattr(ub.time, "sleep", attentes.append)
        assert updater.fetch_page_with_retry(2024, "albums") is not None
        assert attentes == [10, 20]  # 10 s, 20 s — jamais avant la 1ʳᵉ tentative

    def test_epuisement_rend_none(self, updater, monkeypatch, caplog):
        appels = []
        monkeypatch.setattr(updater, "fetch_page", lambda y, c: appels.append(1))
        with caplog.at_level("ERROR"):
            assert updater.fetch_page_with_retry(2024, "albums", max_retries=2) is None
        assert len(appels) == 2 and any("Échec après 2" in r.message for r in caplog.records)


class TestExtractionResiliente:
    def test_une_ligne_qui_leve_est_comptee_sans_arreter_les_autres(self, updater, monkeypatch):
        """Le parseur de dates lève sur la 1ʳᵉ ligne : elle compte en erreur,
        la 2ᵉ est lue quand même (boucle batch résiliente)."""
        ligne = (
            '<div style="display:table-row">'
            '<div class="chart_title"><a href="/fr/song/x">{a}<br>T</a></div>'
            '<div class="company">01/01/2021: Or</div></div>'
        )
        vrai = updater.parse_certification_date
        n = {"v": 0}

        def parse(text):
            n["v"] += 1
            if n["v"] == 1:
                raise ValueError("format inattendu")
            return vrai(text)

        monkeypatch.setattr(updater, "parse_certification_date", parse)
        bilan = {}
        certs = updater.extract_certifications(
            _soup(ligne.format(a="A") + ligne.format(a="B")), 2021, "singles", bilan
        )
        assert [c["artist"] for c in certs] == ["B"] and bilan["erreurs"] == 1


class TestDedupDatabase:
    def test_brut_vide_rend_une_erreur(self, updater):
        report = updater.dedup_database(apply=False)
        assert "error" in report and not report.get("applied")

    def test_regenere_le_clean_depuis_le_brut(self, updater, tmp_path):
        raw = pd.DataFrame(
            [
                {
                    "artist": "A",
                    "title": "T",
                    "category": "singles",
                    "certification_level": "Or",
                    "certification_date": "2021-01-01",
                    "year_page": 2021,
                    "detail_url": "",
                },
                {
                    "artist": "A",
                    "title": "T",
                    "category": "singles",
                    "certification_level": "Or",
                    "certification_date": "2021-01-01",
                    "year_page": 2021,
                    "detail_url": "",
                },
            ]
        )
        raw.to_csv(updater.raw_path, index=False, encoding="utf-8-sig")
        apercu = updater.dedup_database(apply=False)
        assert apercu["rows_in"] == 2 and apercu["rows_out"] == 1
        assert not updater.database_path.exists()  # dry-run : rien d'écrit
        report = updater.dedup_database(apply=True)
        assert report["applied"] and updater.database_path.exists()
        assert report["levels"] == {"Or": 1}
        assert len(pd.read_csv(updater.database_path)) == 1


class TestEcritureAtomique:
    def test_echec_d_ecriture_nettoie_le_temporaire_et_releve(self, updater, monkeypatch):
        def casse(self, path, *a, **k):
            open(path, "w").close()  # le tmp existe au moment de l'échec
            raise OSError("disque plein")

        monkeypatch.setattr(pd.DataFrame, "to_csv", casse)
        with pytest.raises(OSError):
            updater._write_raw(pd.DataFrame([{"a": 1}]))
        assert not updater.raw_path.with_suffix(".rawtmp").exists()
        with pytest.raises(OSError):
            updater._write_clean(pd.DataFrame([{"a": 1}]))
        assert not updater.database_path.with_suffix(".tmp").exists()


class TestRetryMissingPages:
    def test_parcourt_trois_annees_deux_categories_et_continue_sur_erreur(
        self, updater, monkeypatch
    ):
        vus = []

        def lire(year, category, *, avec_retry=False):
            vus.append((year, category, avec_retry))
            if category == "albums":
                raise ValueError("html inattendu")
            return [{"artist": "A"}]

        monkeypatch.setattr(updater, "_lire_page", lire)
        certs = updater.retry_missing_pages()
        assert len(vus) == 6 and all(r for _, _, r in vus)
        assert len(certs) == 3  # les 3 pages « singles »


class TestRuns:
    def test_run_manuel_enchaine_et_rend_le_verdict(self, updater, monkeypatch):
        journal = []
        monkeypatch.setattr(updater, "retry_missing_pages", lambda: journal.append("retry") or [])
        monkeypatch.setattr(
            updater, "update_recent_years", lambda n: journal.append(("recent", n)) or []
        )
        monkeypatch.setattr(updater, "save_updated_database", lambda c: journal.append("save"))
        assert updater.run_manual_update(years_back=3) is True
        assert journal == ["retry", ("recent", 3), "save"]

    def test_run_manuel_qui_leve_rend_false(self, updater, monkeypatch):
        def casse():
            raise RuntimeError("cloudflare")

        monkeypatch.setattr(updater, "retry_missing_pages", casse)
        assert updater.run_manual_update() is False

    def test_run_programme(self, updater, monkeypatch):
        journal = []
        monkeypatch.setattr(updater, "retry_missing_pages", lambda: [])
        monkeypatch.setattr(updater, "update_current_year", lambda: journal.append("cur") or [])
        monkeypatch.setattr(updater, "save_updated_database", lambda c: journal.append("save"))
        assert updater.run_scheduled_update() is True and journal == ["cur", "save"]

        def casse():
            raise RuntimeError("x")

        monkeypatch.setattr(updater, "update_current_year", casse)
        assert updater.run_scheduled_update() is False
