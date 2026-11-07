"""Les têtes de section des paroles Genius : un nom n'y entre que s'il est celui de
l'interprète réel, et seulement sur une section (2026-11-07).

Le scraper ajoutait « : <artiste de la ligne> » à toute tête sans attribution :
« [Couplet 1 : Booba] » sur « Fenwick » de Niro, « [Produced by Dr. Dre, Scott
Storch and Mel-Man : Eminem] » sur « Still D.R.E. ».
"""

import pytest

from src.models.artist import Artist
from src.models.track import Track
from src.scrapers.genius_scraper_v3 import GeniusScraperV3
from src.utils.paroles_genius import (
    attribuer_sections,
    est_tete_de_section,
    interprete_des_sections,
)


def _page(*lignes: str) -> str:
    corps = "<br>".join(lignes)
    return f"<div data-lyrics-container='true'>{corps}</div>"


@pytest.fixture
def scraper():
    return GeniusScraperV3.__new__(GeniusScraperV3)


class TestInterprete:
    def test_le_principal_vaut_quelle_que_soit_la_ligne(self):
        assert interprete_des_sections("Booba", "Niro", False) == "Niro"
        assert interprete_des_sections("Booba", "Niro", True) == "Niro"
        assert interprete_des_sections("Booba", "Niro", None) == "Niro"

    def test_la_ligne_ne_vaut_que_si_elle_est_le_principal(self):
        assert interprete_des_sections("Isha", None, False) == "Isha"

    @pytest.mark.parametrize(
        ("est_feat", "role"),
        [(True, None), (None, None), (False, "Additional Voices")],
    )
    def test_sans_principal_connu_aucun_nom(self, est_feat, role):
        assert interprete_des_sections("Booba", None, est_feat, role) is None


class TestTeteDeSection:
    @pytest.mark.parametrize(
        "etiquette",
        [
            "Couplet 1",
            "Couplet unique",
            "Verse Two",
            "Refrain x2",
            "Refrain (x2)",
            "Pré-refrain",
            "Pre-Chorus",
            "Post-Chorus",
            "Intro",
            "Outro",
            "Pont",
            "Transition",
            "Куплет 1",
            "Припев",
        ],
    )
    def test_une_section(self, etiquette):
        assert est_tete_de_section(etiquette)

    @pytest.mark.parametrize(
        "etiquette",
        [
            "Produced by Dr. Dre, Scott Storch and Mel-Man",
            'Paroles de "Genèse"',
            "Rest of the lyrics unavailable",
            "Curtains open, followed by a crowd's applause",
            "?",
            "Eminem",
            "Couplet 1 - Tunisiano",
            "Refrain - Blacko",
            "Couplet 1 : Isha",
            "Verse 1: Dr. Dre",
            "Instrumental",
            "Sample",
            "Part II",
        ],
    )
    def test_pas_une_section_a_nommer(self, etiquette):
        assert not est_tete_de_section(etiquette)


class TestAttribuerSections:
    def test_seules_les_sections_sans_attribution_recoivent_le_nom(self):
        paroles = (
            "[Produced by Scott Storch]\n[Couplet 1]\nun\n"
            "[Refrain : Akhenaton]\ndeux\n[Outro]\ntrois [Hook] dedans"
        )
        assert attribuer_sections(paroles, "Isha") == (
            "[Produced by Scott Storch]\n[Couplet 1 : Isha]\nun\n"
            "[Refrain : Akhenaton]\ndeux\n[Outro : Isha]\ntrois [Hook] dedans"
        )

    def test_sans_interprete_le_texte_reste_celui_de_genius(self):
        assert attribuer_sections("[Couplet 1]\nun", None) == "[Couplet 1]\nun"


class TestAuScraper:
    """Le texte stocké, de la page au `Track`."""

    def test_fenwick_sur_la_ligne_de_booba_nomme_niro(self, scraper):
        t = Track(
            title="Fenwick",
            artist=Artist(name="Booba"),
            primary_artist_name="Niro",
            is_featuring=False,
        )
        scraper._apply_lyrics_from_html(_page("[Couplet 1]", "des paroles ici"), t)
        assert t.lyrics.text == "[Couplet 1 : Niro]\ndes paroles ici"

    def test_still_dre_la_tete_de_production_ne_recoit_aucun_nom(self, scraper):
        t = Track(
            title="Still D.R.E.",
            artist=Artist(name="Eminem"),
            primary_artist_name="Dr. Dre",
            is_featuring=False,
        )
        scraper._apply_lyrics_from_html(
            _page(
                "[Produced by Dr. Dre, Scott Storch and Mel-Man]",
                "[Verse 1: Dr. Dre &amp; Snoop Dogg]",
                "des paroles ici",
            ),
            t,
        )
        assert t.lyrics.text == (
            "[Produced by Dr. Dre, Scott Storch and Mel-Man]\n"
            "[Verse 1: Dr. Dre & Snoop Dogg]\ndes paroles ici"
        )

    def test_le_morceau_de_l_artiste_garde_son_nom(self, scraper):
        t = Track(title="Solo", artist=Artist(name="Isha"), is_featuring=False)
        scraper._apply_lyrics_from_html(_page("[Couplet 1]", "des paroles ici"), t)
        assert t.lyrics.text == "[Couplet 1 : Isha]\ndes paroles ici"

    def test_deux_lignes_soeurs_lisent_le_meme_texte(self, scraper):
        """Les paroles sont une donnée de PAGE : la lecture de n'importe quelle
        ligne s'aligne chez les sœurs, elle ne dépend donc pas de la ligne."""
        niro = Track(title="Fenwick", artist=Artist(name="Niro"), is_featuring=False)
        booba = Track(
            title="Fenwick",
            artist=Artist(name="Booba"),
            primary_artist_name="Niro",
            is_featuring=True,
        )
        page = _page("[Couplet 1]", "des paroles ici")
        scraper._apply_lyrics_from_html(page, niro)
        scraper._apply_lyrics_from_html(page, booba)
        assert niro.lyrics.text == booba.lyrics.text
