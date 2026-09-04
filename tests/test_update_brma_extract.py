"""Extraction des certifications BRMA depuis une page Ultratop (`update_brma`).

`parse_certification_date` porte une régression coûteuse : l'ancien motif
`[A-Za-zÀ-ÿ\\s]+` ne capturait pas les MULTIPLICATEURS (« 2x Platine »), ce qui
a produit **819 lignes au niveau VIDE** en base. Le motif actuel capture tout
entre « : » et la date suivante ; ce fichier verrouille les deux propriétés —
les multiplicateurs passent, et un niveau vide n'est jamais émis.

L'extraction est rejouée sur la page réelle enregistrée
(`tests/fixtures/brma/`), en plus de HTML minimal construit en ligne.
"""

import json
from datetime import datetime

import pandas as pd
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
        updater.existing_keys.add("Angèle|Titre|Or|2019-05-01|singles")
        soup = _soup(_ligne_html("Angèle", "Titre", "01/05/2019: Or"))
        assert updater.extract_certifications(soup, 2019, "singles") == []

    def test_lautre_categorie_reste_collectable(self, updater):
        """La clé porte la CATÉGORIE depuis le 2026-09-04 : avoir le single en
        base ne doit pas empêcher de collecter l'album du même nom."""
        updater.existing_keys.add("Angèle|Titre|Or|2019-05-01|singles")
        soup = _soup(_ligne_html("Angèle", "Titre", "01/05/2019: Or"))

        certs = updater.extract_certifications(soup, 2019, "albums")
        assert len(certs) == 1 and certs[0]["category"] == "albums"

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


# ─────────────────────── cycle brut → clean → métadonnées (sans réseau)


def _cert(artist="ISHA", title="Titre", date="2021-05-01", level="Or", category="singles"):
    return {
        "artist": artist,
        "title": title,
        "certification_date": date,
        "certification_level": level,
        "category": category,
        "year_page": 2021,
    }


class TestSauvegardeBrutEtClean:
    """Convention « brut + clean » : `brma_raw.csv` accumule tout ce qui a été
    scrapé (dédup EXACTE, aucune perte), `certif_brma.csv` en est DÉRIVÉ par la
    dédup métier. Le clean ne doit jamais être la seule source."""

    def test_le_brut_et_le_clean_sont_ecrits(self, updater, tmp_path):
        updater.save_updated_database([_cert()])

        assert updater.raw_path.exists()
        assert updater.database_path.exists()

    def test_le_brut_accumule_entre_deux_runs(self, updater):
        updater.save_updated_database([_cert(title="A")])
        updater.save_updated_database([_cert(title="B")])

        brut = pd.read_csv(updater.raw_path, encoding="utf-8-sig")
        assert set(brut["title"]) == {"A", "B"}

    def test_le_brut_dedoublonne_a_l_identique(self, updater):
        updater.save_updated_database([_cert(), _cert()])

        brut = pd.read_csv(updater.raw_path, encoding="utf-8-sig")
        assert len(brut) == 1

    def test_sans_nouveaute_la_fraicheur_est_quand_meme_horodatee(self, updater):
        """Ultratop n'a quasi jamais de nouveauté entre deux runs : sans cet
        horodatage la GUI afficherait éternellement une MàJ périmée."""
        updater.save_updated_database([_cert()])
        meta = json.loads((updater.output_dir / "metadata.json").read_text(encoding="utf-8"))
        premiere = meta["last_update"]

        updater.existing_db = pd.read_csv(updater.database_path, encoding="utf-8-sig")
        updater.save_updated_database([])

        meta = json.loads((updater.output_dir / "metadata.json").read_text(encoding="utf-8"))
        assert meta["last_update"] >= premiere
        assert meta["new_records_added"] == 0

    def test_sans_nouveaute_ni_brut_rien_n_est_ecrit(self, updater):
        """Premier run à vide : pas de métadonnées inventées sur une base absente."""
        updater.save_updated_database([])
        assert not (updater.output_dir / "metadata.json").exists()

    def test_metadonnees_portent_la_source_globale(self, updater):
        """`cert_source.read_freshness` distingue MàJ globale et récup artiste :
        un scrape Ultratop est toujours GLOBAL."""
        updater.save_updated_database([_cert(artist="A"), _cert(artist="B", title="T2")])

        meta = json.loads((updater.output_dir / "metadata.json").read_text(encoding="utf-8"))
        assert meta["last_source"] == "GLOBAL"
        assert "GLOBAL" in meta["updates"]
        assert meta["unique_artists"] == 2
        assert meta["new_records_added"] == 2

    def test_rapport_genere_avec_le_detail(self, updater):
        updater.save_updated_database([_cert(title="Mon Titre")])

        rapports = list((updater.output_dir / "reports").glob("update_report_*.txt"))
        assert len(rapports) == 1
        assert "Mon Titre" in rapports[0].read_text(encoding="utf-8")

    def test_pas_de_rapport_sans_nouveaute(self, updater):
        updater.generate_update_report([])
        assert not (updater.output_dir / "reports").exists()


class TestBouclesDAnnees:
    """`update_current_year` / `update_recent_years` : ce qui est demandé au site,
    et ce qui arrive quand une page manque."""

    def _pages(self, updater, monkeypatch, reponses):
        """Stub de `fetch_page` : `reponses[(annee, categorie)]` ou None."""
        vues = []

        def fake_fetch(year, category):
            vues.append((year, category))
            return reponses.get((year, category))

        monkeypatch.setattr(updater, "fetch_page", fake_fetch)
        monkeypatch.setattr(updater, "random_delay", lambda: None)
        return vues

    def test_annee_courante_couvre_albums_et_singles(self, updater, monkeypatch):
        annee = datetime.now().year
        vues = self._pages(
            updater,
            monkeypatch,
            {
                (annee, "albums"): _soup(_ligne_html("ISHA", "Un Album", "01/05/2021: Or")),
                (annee, "singles"): _soup(_ligne_html("ISHA", "Un Single", "01/05/2021: Or")),
            },
        )

        certifs = updater.update_current_year()

        assert vues == [(annee, "albums"), (annee, "singles")]
        assert {c["category"] for c in certifs} == {"albums", "singles"}

    def test_un_meme_titre_album_ET_single_donne_DEUX_certifications(self, updater, monkeypatch):
        """CORRIGÉ le 2026-09-04. `existing_keys` ignorait la CATÉGORIE : un
        album et son morceau-titre certifiés le même jour au même palier n'en
        faisaient qu'un, et le second était perdu. Or ce sont deux œuvres
        distinctes chez Ultratop, avec deux parcours de classement et deux listes
        « Or & Platine » séparées — cas type « This Is The Life » d'Amy Macdonald.

        Le pire n'était pas la perte intra-run : la clé étant amorcée depuis le
        CSV clean, la première des deux enregistrée bloquait DÉFINITIVEMENT la
        collecte de l'autre."""
        annee = datetime.now().year
        soup = _soup(_ligne_html("ISHA", "Titre", "01/05/2021: Or"))
        self._pages(updater, monkeypatch, {(annee, "albums"): soup, (annee, "singles"): soup})

        certifs = updater.update_current_year()

        assert len(certifs) == 2
        assert {c["category"] for c in certifs} == {"albums", "singles"}

    def test_page_absente_n_interrompt_pas_la_collecte(self, updater, monkeypatch):
        """Un 500 sur `albums` ne doit pas faire perdre `singles`."""
        annee = datetime.now().year
        soup = _soup(_ligne_html("ISHA", "Titre", "01/05/2021: Or"))
        self._pages(updater, monkeypatch, {(annee, "singles"): soup})  # albums → None

        assert len(updater.update_current_year()) == 1

    def test_annees_recentes_remonte_le_nombre_demande(self, updater, monkeypatch):
        annee = datetime.now().year
        vues = self._pages(updater, monkeypatch, {})

        updater.update_recent_years(years_back=2)

        annees_vues = sorted({y for y, _ in vues})
        assert annees_vues == [annee - 2, annee - 1, annee]
        assert len(vues) == 6  # 3 années × 2 catégories
