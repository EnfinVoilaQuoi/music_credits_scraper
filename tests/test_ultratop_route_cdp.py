"""Ultratop = route CDP, posée UNE fois (2026-09-17).

Le Cloudflare d'ultratop.be fait boucler tout navigateur lancé par de
l'automation ; seul un Chrome démarré normalement, auquel patchright s'attache,
passe (JOURNAL 2026-06-29). Ce savoir vivait à trois endroits AU-DESSUS du
fetch (GUI `cdp_amont`, `run_brma.ps1`, `capture_fixtures`) et nulle part
dedans : `python -m src.utils.update_brma` partait boucler en headless. Et la
base lisait `GENIUS_CDP_URL` à l'IMPORT, si bien qu'un script ne pouvait même
pas poser sa route lui-même.

Aucun test ne lance Chrome : `conftest._aucun_chrome_de_debug` neutralise
`ensure_cdp_chrome` ; ceux qui exercent la route la posent ou le remplacent.
"""

import os

import pytest

from src.scrapers import crawl4ai_scraper_base as base
from src.scrapers import ultratop_fetch as uf


class TestPreparerRouteCdp:
    def test_variable_deja_posee_respectee_sans_lancer_chrome(self, monkeypatch):
        """GUI, `.env`, Brave lancé à la main : on ne relance rien par-dessus."""
        monkeypatch.setenv("GENIUS_CDP_URL", "http://127.0.0.1:9333")

        def jamais(*a, **k):
            raise AssertionError("ensure_cdp_chrome ne doit pas être appelé")

        monkeypatch.setattr("src.scrapers.cdp_chrome.ensure_cdp_chrome", jamais)
        assert uf.preparer_route_cdp() == "http://127.0.0.1:9333"

    def test_sans_variable_lance_chrome_et_pose_la_variable(self, monkeypatch):
        """C'est la variable posée qui fait la route : la base la lit à l'appel."""
        monkeypatch.setattr(
            "src.scrapers.cdp_chrome.ensure_cdp_chrome", lambda *a, **k: "http://127.0.0.1:9222"
        )
        assert uf.preparer_route_cdp() == "http://127.0.0.1:9222"
        assert os.environ["GENIUS_CDP_URL"] == "http://127.0.0.1:9222"
        assert base._cdp_url() == "http://127.0.0.1:9222"

    def test_chrome_introuvable_rend_none_sans_poser_de_variable(self, monkeypatch, caplog):
        with caplog.at_level("ERROR"):
            assert uf.preparer_route_cdp() is None
        assert "GENIUS_CDP_URL" not in os.environ
        assert any("route CDP obligatoire" in r.message for r in caplog.records)


class TestFetchSansRoute:
    def test_refuse_de_tenter_le_headless(self, monkeypatch):
        """Sans route, l'échelle headless → fenêtre visible ne rend jamais une
        page d'Ultratop : on ne l'emprunte pas, on rend None tout de suite."""

        def jamais():
            raise AssertionError("aucun navigateur ne doit être ouvert sans route CDP")

        monkeypatch.setattr(uf, "_get_scraper", jamais)
        assert uf.fetch_ultratop_html(2021, "singles") is None
        assert uf.fetch_ultratop_soup(2021, "singles") is None


class TestLectureParesseuse:
    """`GENIUS_CDP_URL` se lit À L'APPEL : posée après l'import de la base
    (par le script lui-même), elle est vue. Une constante de module ne l'était
    pas — d'où le wrapper PowerShell qui posait la variable AVANT Python."""

    def test_variable_posee_apres_import_est_vue(self, monkeypatch):
        assert base._cdp_url() is None
        monkeypatch.setenv("GENIUS_CDP_URL", "http://cdp")
        assert base._cdp_url() == "http://cdp"

    def test_aucune_constante_de_module_ne_fige_la_route(self):
        assert not hasattr(base, "_CDP_URL")


class TestCribleDesAppelants:
    """Le savoir « Ultratop = CDP » n'a qu'UN habitat : personne d'autre ne
    lance `ensure_cdp_chrome` pour Ultratop ni ne bricole la base."""

    @pytest.mark.parametrize(
        "chemin",
        ["src/utils/update_brma.py", "src/utils/source_health.py", "scripts/capture_fixtures.py"],
    )
    def test_les_lecteurs_d_ultratop_passent_par_preparer_route_cdp(self, chemin):
        import re
        from pathlib import Path

        source = Path(chemin).read_text(encoding="utf-8")
        # `\b_CDP_URL\b` : la variable d'environnement GENIUS_CDP_URL, elle, a
        # le droit d'être NOMMÉE dans un message.
        assert not re.search(r"\b_CDP_URL\b", source), f"{chemin} bricole la constante retirée"
        assert "ensure_cdp_chrome" not in source, f"{chemin} prépare le CDP à côté de la route"
