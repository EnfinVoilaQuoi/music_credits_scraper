"""Un texte n'est pas toujours des paroles (2026-11-06) : le verdict `has_lyrics`, le
scrape et le rattrapage `scripts/retirer_paroles_factices.py`."""

import pytest
from sqlalchemy import text

from src.models import Artist
from src.models.track import SOURCE_NON_TRANSCRITES, Track
from src.scrapers.genius_scraper_v3 import GeniusScraperV3
from src.utils.paroles_genius import (
    INSTRUMENTAL,
    NON_TRANSCRITES,
    a_des_paroles,
    paroles_factices,
)


@pytest.mark.parametrize(
    "texte",
    [
        ".",
        "?",
        "...",
        "-",
        "/",
        "[]",
        "____",
        "‎",
        "X",
        "TBD",
        "Unreleased",
        "(Unreleased)",
        "Scribing",
        "Lyrics from Snippet",
        "(Lyrics from snippet)",
        "[TBD : Scotian]",
        "[? : Kanye West]",
        "[None : Eminem]",
        "[Unreleased : Eminem]",
        "[Intro : Kanye West]",
        '[Paroles de "Die" : Bakah L’avenue]',
        "[Paroles de Dolce Camara (Snight B Remix) : Booba]",
        "[Verse 1: Game]\n...\n\n[Verse 2: Obie Trice]\n...",
        "The lyrics for this song have yet to be transcribed",
        "Lyrics for this song have yet to be transcribed. Please check back once the song "
        "has been released",
        "Paroles rédigées et annotées par la communauté française de Rap Genius",
    ],
)
def test_ce_n_est_pas_un_texte(texte):
    assert paroles_factices(texte, "Un titre") == NON_TRANSCRITES
    assert not a_des_paroles(texte, "Un titre")


@pytest.mark.parametrize(
    "texte",
    [
        "Just beat",
        "Beat only",
        "Only beat",
        "Instrumentale",
        "[just beat : Logic]",
        "[JUST BEAT : Logic]",
        "[beat : Logic]",
        "[Morceau Instumental : Foulques Nera]",
        "[Morceau instrumental : Lucio Bukowski]",
        "This is a instrumental",
    ],
)
def test_un_morceau_sans_voix(texte):
    assert paroles_factices(texte, "Un titre") == INSTRUMENTAL


@pytest.mark.parametrize(
    ("titre", "texte"),
    [("Murderer", "Murderer"), ("Strain", "Strain"), ("BELT", "B e l t"), ("Shame", "SHAME")],
)
def test_le_seul_titre(titre, texte):
    assert paroles_factices(texte, titre) == NON_TRANSCRITES


@pytest.mark.parametrize(
    ("titre", "texte"),
    [
        # De vrais textes courts.
        ("Beautiful (relapse refill)", "Ken Kaniff sucks a fat dick"),
        ("Kid Cudi Geneseo Freestyle", "Kid Cudi:"),
        ("Bouge de là (Underground Mix)", "Bouge de là !"),
        ("Interlude : Lucien", "Et maintenant, voici : Lucien\nLe tchadien"),
        ("Love", "Love is all we need"),
        # Un seul mot, mais pas le titre : rien ne le prouve.
        ("Facts (Original)", "Love"),
        # Un titre cyrillique se normalise en vide : ça ne vaut pas le texte.
        ("Ярик и Лёня", "[Куплет 1, Ярик : PD]\nЭто Ярик, блять"),
        # Une didascalie décrit un skit.
        (
            "Curtains Up (Skit)",
            "[Curtains open, followed by a crowd's applause. Ominous piano music begins : Eminem]",
        ),
        # « Unreleased » de Kid Cudi est un titre qui a des paroles.
        ("Unreleased", "[Verse 1]\nUnreleased, I keep it to myself"),
    ],
)
def test_un_vrai_texte_reste(titre, texte):
    assert paroles_factices(texte, titre) is None
    assert a_des_paroles(texte, titre)


@pytest.mark.parametrize("texte", ["", "   ", None])
def test_pas_de_texte_pas_de_paroles(texte):
    assert paroles_factices(texte) is None and not a_des_paroles(texte)


def _page(paroles: str) -> str:
    return f'<div data-lyrics-container="true">{paroles}</div>'


class TestScrape:
    def _lire(self, html, track):
        scraper = GeniusScraperV3.__new__(GeniusScraperV3)
        return scraper._apply_lyrics_from_html(html, track)

    def test_un_placeholder_est_le_constat_non_transcrit(self):
        t = Track(title="Real 911", artist=Artist(name="The Game"))
        self._lire(_page("TBD"), t)
        assert t.lyrics.text is None and t.lyrics.source == SOURCE_NON_TRANSCRITES
        assert t.lyrics.scraped_at is not None and not t.lyrics.instrumental

    def test_le_seul_titre_aussi(self):
        t = Track(title="Murderer", artist=Artist(name="X"))
        self._lire(_page("Murderer"), t)
        assert t.lyrics.text is None and t.lyrics.source == SOURCE_NON_TRANSCRITES

    def test_just_beat_est_le_constat_instrumental(self):
        t = Track(title="Ocean View", artist=Artist(name="Logic"))
        self._lire(_page("[just beat : Logic]"), t)
        assert t.lyrics.text is None and t.lyrics.instrumental is True

    def test_un_titre_instrumental_le_reste_malgre_un_placeholder(self):
        t = Track(title="Boulbi (Instrumental)", artist=Artist(name="Booba"))
        self._lire(_page("TBD"), t)
        assert t.lyrics.text is None and t.lyrics.instrumental is True

    def test_le_placeholder_d_inedit_reste_un_constat(self):
        t = Track(title="Snow Slide", artist=Artist(name="X"))
        self._lire(_page("Unreleased"), t)
        assert t.unreleased is True and t.lyrics.text is None

    def test_un_placeholder_ne_vole_pas_la_provenance_de_vraies_paroles(self):
        t = Track(title="Real 911", artist=Artist(name="The Game"))
        t.lyrics.text = "Des paroles venues d'ailleurs"
        t.lyrics.source = "ytm"
        self._lire(_page("TBD"), t)
        assert t.lyrics.text == "Des paroles venues d'ailleurs" and t.lyrics.source == "ytm"

    def test_un_vrai_texte_court_passe(self):
        t = Track(title="Beautiful", artist=Artist(name="Eminem"))
        self._lire(_page("Ken Kaniff sucks a fat dick"), t)
        assert t.lyrics.text and t.lyrics.source != SOURCE_NON_TRANSCRITES


class TestVerdictEtRattrapage:
    @pytest.fixture
    def artiste(self, data_manager):
        a = Artist(name="The Game")
        a.id = data_manager.save_artist(a)
        return a

    def _fiche(self, data_manager, artiste, titre, paroles, genius_id):
        t = Track(title=titre, artist=artiste, genius_id=genius_id)
        t.lyrics.text = paroles
        t.lyrics.source = "genius"
        data_manager.save_track(t)
        return next(x for x in data_manager.get_artist_tracks(artiste.id) if x.title == titre)

    def _ligne(self, data_manager, tid):
        with data_manager.engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT lyrics, has_lyrics, lyrics_source, instrumental FROM tracks "
                    "WHERE id = :i"
                ),
                {"i": tid},
            ).one()

    def test_le_verdict_de_la_colonne(self, data_manager, artiste):
        faux = self._fiche(data_manager, artiste, "Real 911", "TBD", 1)
        vrai = self._fiche(data_manager, artiste, "Beautiful", "Ken Kaniff sucks a fat dick", 2)
        assert not self._ligne(data_manager, faux.id).has_lyrics
        assert self._ligne(data_manager, vrai.id).has_lyrics

    def _script(self, monkeypatch, data_manager, *args):
        import src.utils.data_manager as module_dm
        from scripts import retirer_paroles_factices as script

        monkeypatch.setattr(module_dm, "DataManager", lambda: data_manager)
        monkeypatch.setattr("sys.argv", ["retirer_paroles_factices.py", *args])

        class _Sauvegarde:
            def create_backup(self, nom):
                return nom

        monkeypatch.setattr("src.utils.database_backup.get_backup_manager", lambda: _Sauvegarde())
        return script.main()

    def test_a_blanc_rien_ne_part(self, data_manager, artiste, monkeypatch, capsys):
        faux = self._fiche(data_manager, artiste, "Real 911", "TBD", 1)
        assert self._script(monkeypatch, data_manager) == 0
        assert "1 fiche(s)" in capsys.readouterr().out
        assert self._ligne(data_manager, faux.id).lyrics == "TBD"

    def test_apply(self, data_manager, artiste, monkeypatch):
        faux = self._fiche(data_manager, artiste, "Real 911", "TBD", 1)
        beat = self._fiche(data_manager, artiste, "Ocean View", "[just beat : Logic]", 2)
        vrai = self._fiche(data_manager, artiste, "Beautiful", "Ken Kaniff sucks a fat dick", 3)
        assert self._script(monkeypatch, data_manager, "--apply") == 0
        ligne = self._ligne(data_manager, faux.id)
        assert ligne.lyrics is None and ligne.lyrics_source == SOURCE_NON_TRANSCRITES
        assert not ligne.has_lyrics
        ligne = self._ligne(data_manager, beat.id)
        assert ligne.lyrics is None and ligne.instrumental
        assert self._ligne(data_manager, vrai.id).lyrics == "Ken Kaniff sucks a fat dick"
        assert data_manager.paroles_factices_de_la_base() == []
