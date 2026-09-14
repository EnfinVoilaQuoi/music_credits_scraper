"""SNEP — ce qui entoure le scrape d'une année : téléchargement de l'export
(types de contenu, HTTP, réseau, écriture), `_fetch` avec relances,
`_reconcilier` / `_page_artiste`, et les orchestrateurs `update_snep_database`
/ `backfill_years` (années incomplètes → code non nul).

`DATA_PATH` est redirigé vers `tmp_path` : ces fonctions construisent leurs
chemins elles-mêmes, aucun test ne doit toucher `data/certifications/snep`.
"""

from types import SimpleNamespace

import pytest
import requests

from src.utils import update_snep as us
from src.utils.update_snep import BilanAnnee
from tests.test_update_snep_scrape import HEADER


@pytest.fixture(autouse=True)
def _isole(monkeypatch, tmp_path):
    monkeypatch.setattr(us, "DATA_PATH", tmp_path)
    monkeypatch.setattr(us.time, "sleep", lambda *_: None)
    monkeypatch.setattr(us.random, "uniform", lambda a, b: 0)
    snep = tmp_path / "certifications" / "snep"
    snep.mkdir(parents=True)
    (snep / "certif-.csv").write_text("﻿" + HEADER + "\n", encoding="utf-8")
    return snep


class _Reponse:
    def __init__(self, status=200, content=b"", content_type="text/csv"):
        self.status_code = status
        self.content = content
        self.headers = {"Content-Type": content_type}


class TestTelechargement:
    def _get(self, monkeypatch, reponses):
        appels = []

        def get(source, url, **k):
            appels.append(url)
            r = reponses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        monkeypatch.setattr(us.source_usage, "requests_get", get)
        return appels

    def test_type_de_contenu_inattendu_est_ignore(self, monkeypatch, _isole):
        appels = self._get(
            monkeypatch,
            [
                _Reponse(content_type="text/html"),
                _Reponse(content=("﻿" + HEADER + "\nA;T;E;Singles;Or;2020;2021\n").encode()),
            ],
        )
        dest = us.download_latest_snep_csv()
        assert len(appels) == 2 and dest == _isole / "certif-.csv"
        assert "A;T;E;Singles;Or;2020;2021" in dest.read_text(encoding="utf-8-sig")

    def test_http_puis_reseau_puis_repli_sur_l_existant(self, monkeypatch, _isole):
        self._get(
            monkeypatch,
            [
                _Reponse(status=500),
                requests.ConnectionError("dns"),
                OSError("disque"),
            ],
        )
        assert us.download_latest_snep_csv() == _isole / "certif-.csv"  # l'existant

    def test_sans_existant_rend_none(self, monkeypatch, _isole):
        (_isole / "certif-.csv").unlink()
        self._get(monkeypatch, [_Reponse(status=404)] * 3)
        assert us.download_latest_snep_csv() is None

    def test_echec_d_ecriture_ne_laisse_ni_temporaire_ni_brut_ampute(self, monkeypatch, _isole):
        self._get(monkeypatch, [_Reponse(content=b"x")] + [_Reponse(status=404)] * 2)

        def casse(*a, **k):
            raise OSError("fusion")

        monkeypatch.setattr(us, "_merge_csv_history", casse)
        us.download_latest_snep_csv()
        assert not list(_isole.glob("*.tmp"))
        assert (_isole / "certif-.csv").read_text(encoding="utf-8-sig").startswith(HEADER)


class TestFetch:
    def test_relance_puis_leve_la_derniere_exception(self, monkeypatch):
        monkeypatch.setattr(us, "MAX_RETRIES", 3)
        attentes = []
        monkeypatch.setattr(us.time, "sleep", attentes.append)
        n = {"v": 0}

        def get(url, timeout=None):
            n["v"] += 1
            raise requests.ConnectionError(f"essai {n['v']}")

        with pytest.raises(requests.ConnectionError, match="essai 3"):
            us._fetch(SimpleNamespace(get=get), "http://x")
        assert n["v"] == 3 and len(attentes) == 2  # pas d'attente après le dernier

    def test_succes_apres_un_echec(self, monkeypatch):
        monkeypatch.setattr(us, "MAX_RETRIES", 2)
        reponses = [
            requests.ConnectionError("x"),
            SimpleNamespace(raise_for_status=lambda: None, text="ok"),
        ]

        def get(url, timeout=None):
            r = reponses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        assert us._fetch(SimpleNamespace(get=get), "http://x") == "ok"


class TestReconcilier:
    def test_sans_annee_complete_ne_conclut_pas(self, _isole):
        assert us._reconcilier(_isole / "certif-.csv", [], ["site"]) == 0

    def test_confronte_les_annees_completes_et_enregistre(self, monkeypatch, _isole):
        brut = _isole / "certif-.csv"
        brut.write_text(
            "﻿" + HEADER + "\nA;T;E;Singles;Or;01/01/2020;05/05/2024\n"
            "B;U;E;Singles;Or;01/01/2020;05/05/2019\n",
            encoding="utf-8",
        )
        vus = {}
        monkeypatch.setattr(us.snep_vues, "enregistrer", lambda p, r: vus.update(enregistre=r) or 1)
        # `_reconcilier` lit r.vues/r.remplacees pour l'affichage : un faux objet.
        monkeypatch.setattr(
            us.snep_vues, "reconcilier", lambda locales, site: vus.update(locales=locales) or _R()
        )
        monkeypatch.setattr(us.snep_vues, "confirmer", lambda r, locales, chercher: r)
        assert us._reconcilier(brut, [2024], ["site"]) == 1
        assert vus["locales"] == ["A;T;E;Singles;Or;01/01/2020;05/05/2024"]  # 2019 hors champ


class _R:
    vues = {1}
    remplacees = set()
    retirees = set()


class TestPageArtiste:
    def test_rend_none_quand_le_site_ne_repond_pas(self, monkeypatch):
        def casse(session, url):
            raise requests.ConnectionError("x")

        monkeypatch.setattr(us, "_fetch", casse)
        assert us._page_artiste("Jul") is None

    def test_parse_la_page_filtree(self, monkeypatch):
        vus = {}
        monkeypatch.setattr(us, "_fetch", lambda s, url: vus.update(url=url) or "<html/>")
        monkeypatch.setattr(us, "_parse_certifications_page", lambda html: ["bloc"])
        assert us._page_artiste("Shurik'n") == ["bloc"]
        assert vus["url"].endswith("?interprete=Shurik%27n")


class TestUpdateSnepDatabase:
    @pytest.fixture
    def orchestre(self, monkeypatch, _isole):
        journal = []
        monkeypatch.setattr(
            us, "download_latest_snep_csv", lambda: journal.append("dl") or _isole / "certif-.csv"
        )
        monkeypatch.setattr(us, "_years_to_scrape", lambda: [2026, 2025])
        monkeypatch.setattr(
            us,
            "_reconcilier",
            lambda p, completes, site: journal.append(("reconcilier", completes)),
        )
        monkeypatch.setattr(
            us,
            "_rebuild_canonical",
            lambda source, partial="": journal.append(("rebuild", partial)) or (10, 12),
        )
        return journal

    def test_sans_export_rend_false(self, monkeypatch, orchestre):
        monkeypatch.setattr(us, "download_latest_snep_csv", lambda: None)
        assert us.update_snep_database() is False

    def test_annees_completes(self, monkeypatch, orchestre):
        monkeypatch.setattr(us, "scrape_year", lambda p, a, site=None: BilanAnnee(3, True))
        assert us.update_snep_database() is True
        assert orchestre == ["dl", ("reconcilier", [2026, 2025]), ("rebuild", "")]

    def test_annee_coupee_ou_qui_leve_rend_false_et_le_dit(self, monkeypatch, orchestre, capsys):
        def scrape(p, a, site=None):
            if a == 2026:
                raise requests.ConnectionError("timeout")
            return BilanAnnee(0, False, "page 3 sans bloc")

        monkeypatch.setattr(us, "scrape_year", scrape)
        assert us.update_snep_database() is False
        assert orchestre[1] == ("reconcilier", [])  # aucune année complète
        assert orchestre[2] == ("rebuild", "2026 (ConnectionError); 2025 (page 3 sans bloc)")
        assert "MISE À JOUR PARTIELLE" in capsys.readouterr().out

    def test_check_for_updates_est_la_maj(self, monkeypatch):
        monkeypatch.setattr(us, "update_snep_database", lambda: True)
        assert us.check_for_updates() is True


class TestBackfill:
    def test_partiel_et_complet(self, monkeypatch, _isole):
        journal = []
        monkeypatch.setattr(
            us, "_reconcilier", lambda p, completes, site: journal.append(completes)
        )
        monkeypatch.setattr(us, "_rebuild_canonical", lambda source, partial="": (0, 0))
        verdicts = {2024: BilanAnnee(5, True), 2025: BilanAnnee(2, False, "coupée")}
        monkeypatch.setattr(us, "scrape_year", lambda p, a, site=None: verdicts[a])
        b = us.backfill_years([2024, 2025])
        assert b == BilanAnnee(7, False, "2025 (coupée)") and journal == [[2024]]
        b = us.backfill_years([2024])
        assert b == BilanAnnee(5, True)


class TestMain:
    def test_codes_de_sortie(self, monkeypatch):
        from tests.test_certifs_codes_de_sortie import _lancer

        monkeypatch.setattr(us, "backfill_years", lambda ys: BilanAnnee(0, False, "x"))
        assert _lancer(us, "--year", "2025") == 1
        monkeypatch.setattr(us, "schedule_monthly_update", lambda: True)
        assert _lancer(us, "--scheduled") == 0
        monkeypatch.setattr(us, "check_for_updates", lambda: False)
        assert _lancer(us, "--check") == 1
        vus = []
        monkeypatch.setattr(us, "fetch_artist_certifications", lambda n: vus.append(n) or n == "B")
        assert _lancer(us, "--artist", "A", "--artist", "B") == 0 and vus == ["A", "B"]
