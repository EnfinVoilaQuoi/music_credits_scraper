"""Textes Genius abîmés par un encodage : réparés à l'entrée, et rattrapés par
`scripts/reparer_encodage_genius.py` (2026-11-06)."""

import pytest
from bs4 import BeautifulSoup

from src.models import Artist
from src.models.track import Track
from src.utils.encodage import reparer_c1
from src.utils.title_matching import clean_stored_title


@pytest.mark.parametrize(
    ("abime", "attendu"),
    [
        # Mac Roman dans un mot : les trois titres d'origine.
        ("Le myst\x8fre feminin", "Le mystère feminin"),
        ("Terre m\x8fre n’est pas a vendre", "Terre mère n’est pas a vendre"),
        ("Arr\x90te-moi si tu peux", "Arrête-moi si tu peux"),
        ("caf\x8e", "café"),
        # Windows-1252 : des paroles réelles.
        ("ulant nous l\x92héro au sommet", "ulant nous l’héro au sommet"),
        ("Ta s\x9cur a de la peine", "Ta sœur a de la peine"),
        ("un autre \x9cil sur la route", "un autre œil sur la route"),
        ("(Je veux\x85) Je veux", "(Je veux…) Je veux"),
        ("depuis \x93la gaine 24 heures\x94", "depuis “la gaine 24 heures”"),
        # À côté de ce qu'il remplaçait : un seul caractère reste.
        ("du c\x9coeur et des principes", "du cœur et des principes"),
        ("son coe\x9cur est pris", "son cœur est pris"),
        ("L\x92'aube s\x92est levée", "L'aube s’est levée"),
        ('comme Kennedy \x93"Ich bin ein Berliner\x94"', 'comme Kennedy "Ich bin ein Berliner"'),
        ("C'est\x85. Fils d'immigré", "C'est. Fils d'immigré"),
        ("là\x85...", "là..."),
        ("s'\x80\x99exprime", "s’exprime"),
        ("non plus d\x80'une Algérie", "non plus d'une Algérie"),
        ("haâ€\x9d", "ha”"),
        # Hors d'un mot, un octet sans signification disparaît.
        ("quatre ans\n\x90Évadé de", "quatre ans\nÉvadé de"),
        # Rien à réparer : le texte revient tel quel.
        ("Ça m’a plu… “oui”", "Ça m’a plu… “oui”"),
        ("", ""),
    ],
)
def test_reparer_c1(abime, attendu):
    assert reparer_c1(abime) == attendu


def test_le_titre_enregistre_est_repare():
    assert clean_stored_title("Le myst\x8fre feminin") == "Le mystère feminin"


@pytest.fixture
def artist(data_manager):
    a = Artist(name="Sniper")
    data_manager.save_artist(a)
    return a


def test_save_track_repare_le_titre(data_manager, artist):
    track = Track(title="Arr\x90te-moi si tu peux", artist=artist, genius_id=77)
    data_manager.save_track(track)
    assert [t.title for t in data_manager.get_artist_tracks(artist.id)] == ["Arrête-moi si tu peux"]


def test_le_scrape_repare_paroles_et_anecdotes():
    from src.scrapers.genius_scraper_v3 import GeniusScraperV3

    scraper = GeniusScraperV3.__new__(GeniusScraperV3)
    soup = BeautifulSoup(
        '<div data-lyrics-container="true">Ta s\x9cur a de la peine</div>'
        '<div class="SongDescription__Content">'
        "Un texte assez long sur ce morceau, dont le c\x9coeur est sincère et vrai.</div>",
        "html.parser",
    )
    assert scraper._extract_lyrics_bs4(soup) == "Ta sœur a de la peine"
    assert "le cœur est" in scraper._extract_anecdotes_bs4(soup)


class TestRattrapage:
    def _script(self, monkeypatch, *args):
        from scripts import reparer_encodage_genius as script

        monkeypatch.setattr("sys.argv", ["reparer_encodage_genius.py", *args])
        return script.main()

    def _fiches(self, data_manager, artist):
        # Écrits en direct : `save_track` répare désormais le titre.
        from sqlalchemy import text

        data_manager.save_track(Track(title="Le mystere", artist=artist, genius_id=1))
        fiche = data_manager.get_artist_tracks(artist.id)[0]
        with data_manager.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE tracks SET title = :t, lyrics = :l, anecdotes = :a, "
                    "lyrics_source = 'genius' WHERE id = :i"
                ),
                {"t": "Le myst\x8fre", "l": "Ta s\x9cur", "a": "du c\x9coeur", "i": fiche.id},
            )
        return fiche.id

    def _lire(self, data_manager, fiche_id):
        from sqlalchemy import text

        with data_manager.engine.connect() as conn:
            return conn.execute(
                text("SELECT title, lyrics, anecdotes, lyrics_source FROM tracks WHERE id = :i"),
                {"i": fiche_id},
            ).one()

    def _module(self, monkeypatch, data_manager):
        import src.utils.data_manager as module_dm

        monkeypatch.setattr(module_dm, "DataManager", lambda: data_manager)

    def test_a_blanc_rien_n_est_ecrit(self, data_manager, artist, monkeypatch, capsys):
        fiche = self._fiches(data_manager, artist)
        self._module(monkeypatch, data_manager)
        assert self._script(monkeypatch) == 0
        assert "3 texte(s) à réparer" in capsys.readouterr().out
        assert self._lire(data_manager, fiche).title == "Le myst\x8fre"

    def test_apply_repare_et_garde_la_provenance(self, data_manager, artist, monkeypatch, capsys):
        fiche = self._fiches(data_manager, artist)
        self._module(monkeypatch, data_manager)

        class _Sauvegarde:
            def create_backup(self, nom):
                return nom

        monkeypatch.setattr("src.utils.database_backup.get_backup_manager", lambda: _Sauvegarde())
        assert self._script(monkeypatch, "--apply") == 0
        ligne = self._lire(data_manager, fiche)
        assert tuple(ligne) == ("Le mystère", "Ta sœur", "du cœur", "genius")
        assert data_manager.textes_a_caracteres_c1() == []
