"""Extraction des certifications BRMA depuis une page Ultratop (`update_brma`).

`parse_certification_date` porte une régression coûteuse : l'ancien motif
`[A-Za-zÀ-ÿ\\s]+` ne capturait pas les MULTIPLICATEURS (« 2x Platine »), ce qui
a produit **819 lignes au niveau VIDE** en base. Le motif actuel capture tout
entre « : » et la date suivante ; ce fichier verrouille les deux propriétés —
les multiplicateurs passent, et un niveau vide n'est jamais émis.

L'extraction est rejouée sur la page réelle enregistrée
(`tests/fixtures/brma/`), en plus de HTML minimal construit en ligne.
"""

import pytest
from bs4 import BeautifulSoup

from src.utils.update_brma import UltratopUpdater
from tests.conftest import load_fixture

FIXTURE = "brma/ultratop_2021_singles.html"


@pytest.fixture
def updater(tmp_path):
    """Updater branché sur un répertoire jetable (il écrit des logs au setup)."""
    return UltratopUpdater(
        database_path=str(tmp_path / "certif_brma.csv"), output_dir=str(tmp_path)
    )


def _ligne_html(artiste, titre, certifs, href="/fr/song/abc/Test"):
    return f"""
    <div style="display:table-row">
      <div class="chart_title"><a href="{href}">{artiste}<br>{titre}</a></div>
      <div class="company">{certifs}</div>
    </div>
    """


def _soup(*lignes):
    return BeautifulSoup("<html>" + "".join(lignes) + "</html>", "html.parser")


class TestParseDatesEtNiveaux:
    def test_certification_simple(self, updater):
        assert updater.parse_certification_date("01/05/2019: Or") == [("2019-05-01", "Or")]

    @pytest.mark.parametrize(
        "niveau", ["2x Platine", "12x Platine", "3x Or", "Double Platine", "Diamant"]
    )
    def test_multiplicateurs_et_paliers(self, updater, niveau):
        """Régression des 819 lignes vides : ces libellés doivent survivre."""
        resultat = updater.parse_certification_date(f"01/05/2019: {niveau}")
        assert resultat == [("2019-05-01", niveau)]

    def test_plusieurs_certifications(self, updater):
        """Un morceau accumule ses paliers sur la même ligne Ultratop."""
        texte = "01/05/2019: Or 15/09/2020: Platine 03/01/2022: 2x Platine"
        assert updater.parse_certification_date(texte) == [
            ("2019-05-01", "Or"),
            ("2020-09-15", "Platine"),
            ("2022-01-03", "2x Platine"),
        ]

    def test_niveau_vide_ecarte(self, updater):
        """Garde-fou explicite : jamais de niveau vide en sortie."""
        assert updater.parse_certification_date("01/05/2019:   ") == []

    def test_texte_sans_certification(self, updater):
        assert updater.parse_certification_date("Universal Music") == []
        assert updater.parse_certification_date("") == []

    def test_date_invalide_ignoree(self, updater):
        """Le 31 février n'existe pas : la ligne est sautée, pas fatale."""
        assert updater.parse_certification_date("31/02/2019: Or") == []


class TestExtraction:
    def test_ligne_complete(self, updater):
        soup = _soup(_ligne_html("Angèle", "Balance ton quoi", "01/05/2019: Or"))
        certs = updater.extract_certifications(soup, 2019, "singles")
        assert len(certs) == 1
        c = certs[0]
        assert c["artist"] == "Angèle"
        assert c["title"] == "Balance ton quoi"
        assert c["category"] == "singles"
        assert c["certification_level"] == "Or"
        assert c["certification_date"] == "2019-05-01"
        assert c["year_page"] == 2019
        assert c["detail_url"].startswith("https://www.ultratop.be")

    def test_paliers_multiples_donnent_plusieurs_lignes(self, updater):
        """Dédup ADDITIVE : chaque palier est sa propre certification."""
        soup = _soup(_ligne_html("Angèle", "Titre", "01/05/2019: Or 15/09/2020: Platine"))
        certs = updater.extract_certifications(soup, 2019, "singles")
        assert [c["certification_level"] for c in certs] == ["Or", "Platine"]

    def test_doublon_intra_run_ecarte(self, updater):
        """La même certification listée deux fois sur la page n'entre qu'une fois."""
        ligne = _ligne_html("Angèle", "Titre", "01/05/2019: Or")
        certs = updater.extract_certifications(_soup(ligne, ligne), 2019, "singles")
        assert len(certs) == 1

    def test_deja_en_base_ecarte(self, updater):
        """`existing_keys` vient du CSV : on ne réimporte pas l'existant."""
        updater.existing_keys.add("Angèle|Titre|Or|2019-05-01")
        soup = _soup(_ligne_html("Angèle", "Titre", "01/05/2019: Or"))
        assert updater.extract_certifications(soup, 2019, "singles") == []

    def test_compilation_sans_titre(self, updater):
        """Ultratop liste des compilations : le nom est seul, sans titre."""
        html = """
        <div style="display:table-row">
          <div class="chart_title"><a href="/fr/album/x">Compilation Été 2019</a></div>
          <div class="company">01/05/2019: Or</div>
        </div>
        """
        certs = updater.extract_certifications(_soup(html), 2019, "albums")
        assert certs[0]["artist"] == "Compilation Été 2019"
        assert certs[0]["title"] == ""

    def test_lignes_incompletes_sautees(self, updater):
        """Sans titre, sans lien ou sans bloc `company` : rien à extraire."""
        sans_titre = '<div style="display:table-row"><div class="autre">x</div></div>'
        sans_lien = '<div style="display:table-row"><div class="chart_title">x</div></div>'
        sans_company = """
        <div style="display:table-row">
          <div class="chart_title"><a href="/x">A<br>B</a></div>
        </div>
        """
        soup = _soup(sans_titre, sans_lien, sans_company)
        assert updater.extract_certifications(soup, 2019, "singles") == []

    def test_page_vide(self, updater):
        assert updater.extract_certifications(_soup(), 2019, "singles") == []


@pytest.fixture(scope="module")
def soup():
    """Page Ultratop réelle enregistrée (skip propre si non capturée)."""
    return BeautifulSoup(load_fixture(FIXTURE), "html.parser")


class TestSurPageReelle:
    """Rejoue l'extraction sur la page Ultratop enregistrée."""

    def test_extraction_non_vide(self, updater, soup):
        certs = updater.extract_certifications(soup, 2021, "singles")
        assert len(certs) > 10

    def test_aucun_niveau_vide(self, updater, soup):
        """LA régression à ne jamais revoir, vérifiée sur données réelles."""
        certs = updater.extract_certifications(soup, 2021, "singles")
        assert all(c["certification_level"].strip() for c in certs)

    def test_dates_iso(self, updater, soup):
        certs = updater.extract_certifications(soup, 2021, "singles")
        assert all(len(c["certification_date"]) == 10 for c in certs)
        assert all(c["certification_date"][4] == "-" for c in certs)

    def test_artistes_renseignes(self, updater, soup):
        certs = updater.extract_certifications(soup, 2021, "singles")
        assert all(c["artist"].strip() for c in certs)
