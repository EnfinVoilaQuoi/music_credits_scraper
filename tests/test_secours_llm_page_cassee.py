"""Les secours LLM de Kworb et de BRMA se DÉCLENCHENT sur une vraie page cassée.

Demande utilisateur (2026-10-12) : ces deux replis n'ont jamais tourné (0 ligne
de journal) — sont-ils seulement branchés ? Chaque test prend la VRAIE page
capturée (`tests/fixtures/`), la casse comme le ferait une refonte du site, la
fait passer par le chemin réel du scraper, et vérifie l'ordre « signaler PUIS
sauver » : la panne est déclarée à l'observation, le secours est appelé avec le
texte de la page, et seules ses lignes vérifiables entrent. Le LLM est simulé
(aucun test ne parle à Ollama) ; l'essai réel est consigné dans CORRECTIFS.
"""

from contextlib import contextmanager

import pytest
from bs4 import BeautifulSoup

from tests.conftest import load_fixture


class ObsEspion:
    """Observation de source qui note tout ce qu'on lui déclare."""

    def __init__(self):
        self.journal = []

    def __getattr__(self, nom):
        def noter(*args, **kwargs):
            self.journal.append((nom, args))

        return noter


class FauxLLM:
    def __init__(self, reponse):
        self.reponse = reponse
        self.prompts = []

    def extract_json(self, prompt, max_tokens=512):
        self.prompts.append(prompt)
        return self.reponse


# ── Kworb ─────────────────────────────────────────────────────────────────────


class _Reponse:
    status_code = 200
    encoding = None

    def __init__(self, texte):
        self.text = texte

    def raise_for_status(self):
        pass


def _kworb(monkeypatch, html, faux):
    from src.scrapers import kworb_scraper
    from src.scrapers.kworb_scraper import KworbScraper

    monkeypatch.setattr(kworb_scraper.source_usage, "requests_get", lambda *a, **k: _Reponse(html))
    monkeypatch.setattr(kworb_scraper, "get_shared_extractor", lambda *a, **k: faux)
    obs = ObsEspion()
    page = KworbScraper()._fetch_and_parse_body("https://kworb.net/spotify/artist/x.html", obs)
    return page, obs


def test_kworb_page_saine_le_secours_ne_part_pas(monkeypatch):
    faux = FauxLLM({"tracks": []})
    page, obs = _kworb(monkeypatch, load_fixture("kworb/artist_songs.html"), faux)
    assert len(page["entries"]) > 100
    assert faux.prompts == []
    assert not [n for n, _ in obs.journal if n == "parse_error"]


def test_kworb_page_cassee_signale_puis_sauve(monkeypatch):
    html = load_fixture("kworb/artist_songs.html")
    vraie = BeautifulSoup(html, "html.parser")
    from src.scrapers.kworb_scraper import KworbScraper

    premiere = KworbScraper()._parse_entries(vraie)[0]
    # Une refonte : les cellules ne sont plus des <td>. 0 ligne lue.
    cassee = html.replace("<td", "<span").replace("</td>", "</span>")
    faux = FauxLLM(
        {
            "tracks": [
                {"title": premiere["title"], "streams": premiere["streams"], "daily": 0},
                # Inventé : ce nombre n'est pas sur la page — rejeté.
                {"title": "Morceau inventé", "streams": 987654321, "daily": 0},
            ]
        }
    )
    page, obs = _kworb(monkeypatch, cassee, faux)

    assert [n for n, _ in obs.journal][:1] == ["parse_error"]  # signalé D'ABORD
    assert len(faux.prompts) == 1 and premiere["title"] in faux.prompts[0]
    assert [e["title"] for e in page["entries"]] == [premiere["title"]]
    assert page["entries"][0]["streams"] == premiere["streams"]


# ── BRMA (Ultratop) ───────────────────────────────────────────────────────────


@pytest.fixture
def updater(tmp_path):
    from src.utils.update_brma import UltratopUpdater

    return UltratopUpdater(
        database_path=str(tmp_path / "certif_brma.csv"), output_dir=str(tmp_path)
    )


def _brma(monkeypatch, updater, html, faux):
    from src.utils import llm_extractor, update_brma

    obs = ObsEspion()

    @contextmanager
    def observer(*a, **k):
        yield obs

    monkeypatch.setattr(update_brma.source_usage, "observe", observer)
    monkeypatch.setattr(
        updater, "fetch_page", lambda year, category: BeautifulSoup(html, "html.parser")
    )
    monkeypatch.setattr(llm_extractor, "get_shared_extractor", lambda *a, **k: faux)
    return updater._lire_page(2021, "singles"), obs


def test_brma_page_saine_le_secours_ne_part_pas(monkeypatch, updater):
    faux = FauxLLM({"certifications": []})
    certifs, obs = _brma(
        monkeypatch, updater, load_fixture("brma/ultratop_2021_singles.html"), faux
    )
    assert certifs
    assert faux.prompts == []
    assert updater.pages_muettes == 0


def test_brma_page_cassee_signale_puis_sauve(monkeypatch, updater):
    html = load_fixture("brma/ultratop_2021_singles.html")
    # Une refonte : la classe des titres change. 0 ligne candidate sur 2021.
    cassee = html.replace('class="chart_title"', 'class="titre_renomme"')
    faux = FauxLLM(
        {
            "certifications": [
                {
                    "artist": "24kGoldn feat. Iann Dior",
                    "title": "Mood",
                    "certification": "2x Platine",
                    "date": "2021-03-26",
                },
                # Inventé : cet artiste n'est pas sur la page — rejeté.
                {
                    "artist": "Artiste Inventé",
                    "title": "Rien",
                    "certification": "Or",
                    "date": "2021-01-01",
                },
            ]
        }
    )
    certifs, obs = _brma(monkeypatch, updater, cassee, faux)

    assert [n for n, _ in obs.journal][:1] == ["parse_error"]  # signalé D'ABORD
    assert len(faux.prompts) == 1 and "Mood" in faux.prompts[0]
    assert [(c["artist"], c["title"]) for c in certifs] == [("24kGoldn feat. Iann Dior", "Mood")]
    # Sauvée ou non, la page reste comptée MUETTE : les sélecteurs sont cassés.
    assert updater.pages_muettes == 1
