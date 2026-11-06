"""L'instance Playwright sync rendue ne laisse pas de boucle au fil (2026-11-06)."""

import ast
import asyncio
import threading
from pathlib import Path

import pytest

from src.concurrency import async_loop
from src.scrapers import playwright_manager as pm
from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper

SRC = Path(__file__).resolve().parent.parent / "src"


def _une_boucle_tourne() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


async def _quarante_deux() -> int:
    return 42


@pytest.fixture(autouse=True)
def _fil_propre():
    pm.stop_playwright()
    yield
    pm.stop_playwright()


class FauxPlaywright:
    def __init__(self):
        self.arrete = 0

    def start(self):
        return self

    def stop(self):
        self.arrete += 1


@pytest.fixture
def faux(monkeypatch):
    instance = FauxPlaywright()
    monkeypatch.setattr(pm, "sync_playwright", lambda: instance)
    return instance


def test_le_vrai_playwright_rendu_laisse_run_sync_fonctionner():
    """Le test qui discrimine, avec le vrai pilote (aucun navigateur, aucun réseau) :
    Delusional Thomas, Larry Fisherman, 2026-11-06."""
    pm.get_playwright()
    assert _une_boucle_tourne(), "le constat qui fonde le correctif a changé"
    pm.rendre_playwright()
    assert not _une_boucle_tourne()
    assert async_loop.run_sync(_quarante_deux()) == 42


def test_le_dernier_emprunt_arrete_l_instance(faux):
    assert pm.get_playwright() is pm.get_playwright()
    pm.rendre_playwright()
    assert faux.arrete == 0, "un autre scraper du fil s'en sert encore"
    pm.rendre_playwright()
    assert faux.arrete == 1


def test_rendre_sans_emprunt_ne_fait_rien(faux):
    pm.rendre_playwright()
    assert faux.arrete == 0


def test_stop_remet_les_emprunts_a_zero(faux):
    pm.get_playwright()
    pm.get_playwright()
    pm.stop_playwright()
    pm.get_playwright()
    pm.rendre_playwright()
    assert faux.arrete == 2


def test_le_scraper_ferme_rend_l_instance(faux):
    scraper = SpotifyIDScraper(cache_file=None)
    scraper._playwright = pm.get_playwright()
    scraper._owner_thread = threading.get_ident()
    scraper.close()
    assert faux.arrete == 1 and scraper._playwright is None


def test_un_autre_fil_ne_rend_pas_l_instance(faux):
    """Fermer depuis un autre fil décompterait les emprunts de CE fil-là."""
    scraper = SpotifyIDScraper(cache_file=None)
    scraper._playwright = pm.get_playwright()
    scraper._owner_thread = threading.get_ident()
    fil = threading.Thread(target=scraper._cleanup_resources)
    fil.start()
    fil.join()
    assert faux.arrete == 0


def _appels(fichier: Path) -> set[str]:
    return {
        n.func.id
        for n in ast.walk(ast.parse(fichier.read_text(encoding="utf-8")))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


def test_qui_emprunte_rend():
    """Crible : un module de `src/` qui emprunte l'instance sync la rend."""
    emprunteurs = [
        f
        for f in SRC.rglob("*.py")
        if f.name != "playwright_manager.py" and "get_playwright" in _appels(f)
    ]
    assert emprunteurs, "le crible ne voit plus rien"
    assert [f.name for f in emprunteurs if "rendre_playwright" not in _appels(f)] == []
