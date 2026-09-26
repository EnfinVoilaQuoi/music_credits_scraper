"""Pages Genius qui ne sont pas des morceaux, et pages qui ne disent rien (2026-09-26).

Décision utilisateur : pas d'interviews en base (« on pourrait les confondre,
ça ne rajoute que du bruit »). Aucun oracle Genius — le tag « Non-Music » est
l'URL `-annotated` et couvre aussi les vrais skits (*Wake Up Mr. West*). La
règle retenue, mesurée sur 137 pages non musicales et 5 773 vrais morceaux :
TITRE ou NOM D'ALBUM Genius, 137/137 et aucun morceau perdu.

« 🕳️ sans info » est un STATUT calculé, jamais une suppression : une page lue
qui ne dit rien (*NICE THAT*). Avant lecture, les vrais inédits sont vides
aussi — le critère ne vaut qu'après scrape.
"""

from datetime import datetime

import pytest

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.services.discographie import _filtrer_non_musique
from src.services.tracklists_genius import piste_depuis_tracklist
from src.utils.pages_genius import page_non_morceau
from src.utils.track_validation import Verdict, evaluer, sans_info


class TestAlbumNonMusical:
    @pytest.mark.parametrize(
        "album",
        [
            "Kanye West’s Visionary Streams of Consciousness",
            "Zane Lowe BBC Radio Interviews (Kanye West)",
            "Nardwuar: The Greatest Interviews",
            "GQ Magazine",
            "Boys Don’t Cry (Magazine)",
            "Genius Artist Archives",
            ".WAV RADIO Tracklists",
            "Jeen-yuhs: A Kanye Trilogy Scripts",
        ],
    )
    def test_le_disque_trahit_la_page(self, album):
        """« On Politics » n'a rien de reconnaissable : c'est son album qui parle."""
        assert page_non_morceau("On Politics", album) == "non-musique"

    @pytest.mark.parametrize(
        "album",
        [
            "The College Dropout",
            "Les Archives",  # « Artist Archives » seulement
            "Thinking Above Dreaming Beyond Gravity",
            "Kon the Louis Vuitton Don",
            None,
        ],
    )
    def test_un_vrai_disque_ne_change_rien(self, album):
        assert page_non_morceau("On Sight", album) is None


def _t(titre, album=None):
    t = Track(title=titre)
    t.album = album
    return t


class TestFiltreDuRun:
    def test_ecarte_par_l_album_une_fois_la_fiche_lue(self):
        gardes, ecartes = _filtrer_non_musique(
            [
                _t("On Politics", "Kanye West’s Visionary Streams of Consciousness"),
                _t("Dreaming (Outro)", "Thinking Above Dreaming Beyond Gravity"),
                _t("Interview", "Ombre est lumière"),
            ]
        )
        assert [t.title for t in gardes] == ["Dreaming (Outro)", "Interview"]
        assert ecartes == ["On Politics (Kanye West’s Visionary Streams of Consciousness)"]

    def test_tracklist_d_un_faux_album(self):
        artiste = Artist(name="Kanye West", genius_id=72)
        entree = {"number": 3, "song": {"id": 9, "title": "On Politics", "primary_artist": {}}}
        faux = {"name": "Kanye West’s Visionary Streams of Consciousness"}
        assert piste_depuis_tracklist(entree, faux, artiste) is None


def _page_lue(titre="NICE THAT", **kw):
    t = Track(title=titre, artist=Artist(name="Kanye West"))
    t.lyrics.scraped_at = datetime(2026, 9, 26)
    t.last_scraped = datetime(2026, 9, 26)
    t.lyrics.text = kw.pop("paroles", "(...)")
    for k, v in kw.items():
        setattr(t, k, v)
    return t


class TestSansInfo:
    def test_page_lue_qui_ne_dit_rien(self):
        t = _page_lue()
        assert sans_info(t)
        c = evaluer(t)
        assert c.verdict is Verdict.SANS_INFO and c.icone == "🕳️"
        assert not c.compte_a_valider

    def test_jamais_lue_on_ne_conclut_pas(self):
        """*Real shit*, *BLEED IT* : vides tant qu'ils n'étaient pas scrapés."""
        t = _page_lue()
        t.lyrics.scraped_at = None
        assert not sans_info(t)

    def test_un_crédit_d_un_tiers_porte_une_info(self):
        """« All Night » : Roddy Ricch et Travis Scott, un feat non sorti."""
        t = _page_lue(credits=[Credit("Roddy Ricch", CreditRole.FEATURED)])
        assert not sans_info(t)
        assert sans_info(_page_lue(credits=[Credit("Kanye West", CreditRole.PRODUCER)]))

    @pytest.mark.parametrize(
        "info",
        [
            {"anecdotes": "Supposed lost song from Prynce Geno’s lost album"},
            {"paroles": "[Sample : Prynce Geno] Love Love Love Love Love"},
            {"spotify_id": "abc"},
            {"isrc": "FR123"},
            {"deezer_id": 12},
        ],
    )
    def test_la_moindre_info_suffit(self, info):
        assert not sans_info(_page_lue(**info))

    def test_un_instrumental_constate_n_est_pas_vide(self):
        t = _page_lue(paroles="")
        t.lyrics.instrumental = True
        assert not sans_info(t)

    def test_un_desactive_reste_desactive(self):
        t = _page_lue()
        t.id = 7
        from src.utils.track_validation import Contexte

        assert evaluer(t, Contexte(desactives=frozenset({7}))).verdict is Verdict.DESACTIVE


class TestSansInfoAffine:
    """Retours utilisateur sur les 32 premières fiches (2026-09-26)."""

    def test_paroles_lues_mais_credits_jamais(self):
        """*Tu voulais du rap* : paroles lues, crédits jamais (Népal, Doums,
        Lomepal sur la page) — vide en base ne veut pas dire vide sur Genius."""
        t = _page_lue()
        t.last_scraped = None
        assert not sans_info(t)
        t.credits = [Credit("Kanye West", CreditRole.WRITER, source="genius")]
        assert sans_info(t)  # un crédit Genius prouve que les crédits ont été lus

    @pytest.mark.parametrize(
        "info",
        [
            {"album": "La folie des glandeurs"},
            {"youtube_url": "https://www.youtube.com/watch?v=kOAEmvaxCgw"},
        ],
    )
    def test_album_et_video_sont_des_infos(self, info):
        assert not sans_info(_page_lue(**info))

    @pytest.mark.parametrize(
        "texte", ["Lyrics from Snippet", "(Lyrics from snippet)", "*Lyrics from Snippets*"]
    )
    def test_snippet_seul_n_est_pas_un_texte(self, texte):
        assert sans_info(_page_lue(paroles=texte))

    def test_snippet_avec_les_paroles_fuitees(self):
        t = _page_lue(
            paroles="Lyrics from Snippet\n\n[Verse: Kanye West]\nYou want a problem, I got it"
        )
        assert not sans_info(t)

    def test_placeholder_d_inedit_est_un_constat(self):
        assert not sans_info(_page_lue(paroles="Unreleased"))


class TestTexteGenius:
    @pytest.mark.parametrize(
        "texte",
        [
            "[Morceau instrumental : Lucio Bukowski]",
            "[Instrumentale : 2Fingz]",
            "[Intro instrumentale : Lucio Bukowski]",
            "[Instrumental suivi d'un gunshot : Booba]",
        ],
    )
    def test_instrumental_par_le_texte(self, texte):
        from src.utils.paroles_genius import instrumental_par_le_texte

        assert instrumental_par_le_texte(texte)

    @pytest.mark.parametrize(
        "texte",
        [
            "[Intro : Booba]\nOuais ouais",  # des paroles après l'en-tête
            "[Couplet 1 : Isha]",  # un en-tête sans instrumental
            "",
            None,
        ],
    )
    def test_pas_instrumental(self, texte):
        from src.utils.paroles_genius import instrumental_par_le_texte

        assert not instrumental_par_le_texte(texte)

    def test_placeholder_d_inedit(self):
        from src.utils.paroles_genius import inedit_par_le_texte

        assert inedit_par_le_texte("Unreleased")
        assert inedit_par_le_texte(
            "Lyrics for this song have yet to be released. "
            "Please check back once the song has been released."
        )
        # « Unreleased » de Kid Cudi est un TITRE avec des paroles.
        assert not inedit_par_le_texte("[Verse: Kid Cudi]\nCould this be heaven, unreleased")


class TestScraperTexte:
    def _scraper(self):
        from src.scrapers.genius_scraper_v3 import GeniusScraperV3

        return GeniusScraperV3.__new__(GeniusScraperV3)

    def _page(self, texte):
        return (
            '<html><body><div data-lyrics-container="true">'
            + texte.replace("\n", "<br/>")
            + "</div></body></html>"
        )

    def test_un_en_tete_instrumental_est_un_constat(self):
        t = Track(title="Tu voulais du rap (Interlude)")
        self._scraper()._apply_lyrics_from_html(self._page("[Instrumentale : 2Fingz]"), t)
        assert t.lyrics.instrumental is True and t.lyrics.text is None

    def test_le_placeholder_constate_l_inedit(self):
        t = Track(title="SNOW SLIDE")
        self._scraper()._apply_lyrics_from_html(self._page("Unreleased"), t)
        assert t.unreleased is True


class TestEcrivainInstrumental:
    def test_pose_le_constat_chez_les_soeurs(self, data_manager):
        from sqlalchemy import text

        lignes = []
        for nom in ("Swing", "L'Or du Commun"):
            a = Artist(name=nom)
            a.id = data_manager.save_artist(a)
            t = Track(title="Lambda (Interlude)", artist=a)
            t.genius_id = 42
            t.lyrics.text = "[Instrumentale : L’Or du Commun]"
            t.lyrics.synced = "[00:01.00]un LRC d'un autre morceau"
            lignes.append(data_manager.save_track(t))
        assert data_manager.constater_instrumental(lignes[0])
        with data_manager.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT instrumental, lyrics, lyrics_synced FROM tracks WHERE genius_id = 42")
            ).all()
        assert rows == [(1, None, None), (1, None, None)]
