"""Parseurs BPI rejoués sur des pages RÉELLES, hors ligne.

Ces tests assertent ce que le site fournit VRAIMENT, pas ce qu'on aimerait qu'il
fournisse : une fixture périmée dont le test reste vert a laissé passer deux mois
de panne RIAA. Quand une assertion tombe ici, la question n'est pas « quel test
faut-il assouplir » mais « qu'est-ce qui a changé chez eux ».

Capture : `scripts/capture_fixtures.py --only bpi`.
"""

import pytest

from src.scrapers.bpi_scraper import (
    ENTETES_ATTENDUES,
    PAR_PAGE,
    entetes,
    est_vide,
    lignes_de_donnees,
    parse_annuaire,
    parse_historique,
    parse_liste,
    parse_verifie,
    to_iso,
)
from src.utils.cert_normalize import bpi_level_connu
from tests.conftest import load_fixture


class ObsFactice:
    """Double d'observation : retient les verdicts au lieu de les enregistrer."""

    def __init__(self):
        self.echecs = []
        self.tentatives = []

    def fail(self, kind, detail=""):
        self.echecs.append((str(kind), detail))

    def note_attempt(self, kind, detail="", **_):
        self.tentatives.append((str(kind), detail))

    def absent(self, detail=""):
        self.echecs.append(("absent", detail))

    def skipped(self, detail=""):
        self.echecs.append(("skipped", detail))


@pytest.fixture
def liste():
    return load_fixture("bpi/bpi_list.html")


@pytest.fixture
def page2():
    return load_fixture("bpi/bpi_list_page2.html")


@pytest.fixture
def detail():
    return load_fixture("bpi/bpi_detail.html")


@pytest.fixture
def annuaire():
    return load_fixture("bpi/bpi_artists.html")


# ── Le tableau de résultats ───────────────────────────────────────────────────
class TestListe:
    def test_les_entetes_sont_ceux_attendus(self, liste):
        """G1 : c'est ce contrat-là qui détecte une refonte du site."""
        assert entetes(liste) == ENTETES_ATTENDUES

    def test_une_page_pleine_rend_24_lignes(self, liste):
        """Constante du site, et base du recoupement de complétude."""
        assert len(parse_liste(liste)) == PAR_PAGE

    def test_la_ligne_sentinelle_n_est_pas_comptee(self, liste):
        """Le `<tbody>` porte une ligne d'une seule cellule, sans `hx-get`.

        La compter ferait passer une page vide pour une page pleine.
        """
        from bs4 import BeautifulSoup

        toutes = BeautifulSoup(liste, "html.parser").select("tbody tr")
        assert len(toutes) == PAR_PAGE + 1
        assert len(lignes_de_donnees(liste)) == PAR_PAGE

    def test_les_champs_d_une_ligne(self, liste):
        """Ligne repérée par son IDENTITÉ, pas par son rang : le site trie par
        date de dernière certification, donc un simple réhaussement réordonne la
        page sans que rien n'ait cassé."""
        ligne = next(r for r in parse_liste(liste) if r["title_id"] == 15210)
        assert ligne == {
            "artist": "ORIGINAL SOUNDTRACK",
            "title": "MARY POPPINS",
            "certification_level": "Gold",
            "category": "Album",
            "label": "WALT DISNEY",
            "certification_date": "2020-01-31",
            "release_date": "2018-05-11",
            "format_id": 3,
            "artist_id": 916,
            "title_id": 15210,
            "detail_url": "https://certified-awards.bpi.co.uk/format/3/artist/916/title/15210",
        }

    def test_les_trois_formats_existent(self, liste):
        """« Music DVDs » est un vrai format, pas une curiosité : son barème
        n'a pas de Silver, ce qui se répercute dans `bpi_units`."""
        assert {r["category"] for r in parse_liste(liste)} == {"Album", "Single", "Music DVDs"}

    def test_tous_les_niveaux_sont_du_vocabulaire(self, liste):
        niveaux = {r["certification_level"] for r in parse_liste(liste)}
        assert niveaux <= {"Silver", "Gold", "Platinum", "2x Platinum"}
        assert all(bpi_level_connu(n) for n in niveaux)

    def test_chaque_ligne_porte_son_identite(self, liste):
        """Le triplet (format, artiste, titre) est la clé de dédup : sans lui,
        on retomberait sur des clés textuelles comme les trois autres sources."""
        for r in parse_liste(liste):
            assert r["format_id"] and r["artist_id"] and r["title_id"]


# ── Les pages de continuation ─────────────────────────────────────────────────
class TestPageDeContinuation:
    """La page 1 rend un `<table>`, les suivantes des `<tr>` NUS.

    Ce n'est pas un détail de gabarit : la première version du scraper
    sélectionnait `tbody tr` et ne trouvait donc RIEN dès la page 2. Le balayage
    complet s'est arrêté à 24 titres sur ~26 500. Ce que le test de pagination
    existant ne pouvait pas voir : sa fabrique de pages mettait toujours un
    `<thead>` — une fixture trop propre pour le défaut qu'elle devait attraper.
    """

    def test_une_page_2_n_a_ni_table_ni_thead(self, page2):
        assert "<table" not in page2[:200]
        assert entetes(page2) == ()
        assert page2.lstrip().startswith("<tr")

    def test_ses_lignes_sont_quand_meme_comptees(self, page2):
        assert len(lignes_de_donnees(page2)) == PAR_PAGE

    def test_elle_se_parse_avec_les_entetes_de_la_page_1(self, page2):
        lignes = parse_liste(page2, ENTETES_ATTENDUES)
        assert len(lignes) == PAR_PAGE
        assert all(r["artist"] and r["title"] and r["title_id"] for r in lignes)
        assert all(r["certification_date"] for r in lignes)

    def test_sans_entetes_transmis_elle_ne_rend_rien(self, page2):
        """Le parseur ne DEVINE pas l'ordre des colonnes : sans en-têtes, rien.

        C'est voulu — inventer un ordre par défaut, c'est accepter de lire un
        label en guise de date le jour où le site réordonne ses colonnes.
        """
        assert parse_liste(page2) == []

    def test_g1_ne_crie_PAS_sur_une_page_de_continuation(self, page2):
        obs = ObsFactice()
        assert len(parse_verifie(page2, obs, ENTETES_ATTENDUES)) == PAR_PAGE
        assert obs.echecs == []

    def test_mais_g1_crie_toujours_sur_une_PREMIERE_page_sans_entetes(self, page2):
        """L'absence de `<thead>` reste une anomalie quand rien ne précède."""
        obs = ObsFactice()
        assert parse_verifie(page2, obs) == []
        assert [kind for kind, _ in obs.echecs] == ["parse"]

    def test_les_pages_1_et_2_ne_se_recouvrent_pas(self, liste, page2):
        """Vérifié aussi contre le site réel : 4 pages, 96 lignes, 0 doublon."""
        cle = lambda r: (r["format_id"], r["artist_id"], r["title_id"])  # noqa: E731
        p1 = {cle(r) for r in parse_liste(liste)}
        p2 = {cle(r) for r in parse_liste(page2, ENTETES_ATTENDUES)}
        assert len(p1) == len(p2) == PAR_PAGE
        assert p1 & p2 == set()


# ── L'historique des paliers ──────────────────────────────────────────────────
class TestHistorique:
    def test_les_paliers_masques_sont_lus(self, detail):
        """Le « Show 1 more » n'est qu'un `hidden` CSS.

        Le Gold de 2015 est replié dans `div#middle-awards.hidden` : se fier au
        texte visible perdrait un palier sur trois sur cette page.
        """
        assert parse_historique(detail) == [
            {"certification_date": "2026-08-28", "certification_level": "2x Platinum"},
            {"certification_date": "2016-06-24", "certification_level": "Platinum"},
            {"certification_date": "2015-12-04", "certification_level": "Gold"},
        ]

    def test_page_sans_historique(self):
        assert parse_historique("<html><body><p>rien</p></body></html>") == []


# ── L'annuaire d'artistes ─────────────────────────────────────────────────────
class TestAnnuaire:
    def test_une_entite_est_une_chaine_de_credit(self, annuaire):
        """« SIGALA » n'est pas UN artiste chez la BPI mais 18 entités.

        Chercher la seule graphie exacte amputerait la discographie certifiée —
        même problème que `cert_artist.noms_de_recherche`, résolu par la source.
        """
        entites = parse_annuaire(annuaire)
        libelles = {libelle for _, libelle in entites}
        assert len(entites) == 18
        assert "SIGALA" in libelles
        assert "SIGALA & BECKY HILL" in libelles
        assert "KATO/SIGALA/HAILEE STEINFELD" in libelles

    def test_les_ids_sont_numeriques(self, annuaire):
        assert all(isinstance(i, int) and i > 0 for i, _ in parse_annuaire(annuaire))


# ── Les dates ─────────────────────────────────────────────────────────────────
class TestDates:
    @pytest.mark.parametrize(
        "brut,attendu",
        [
            ("31.01.2020", "2020-01-31"),  # tableau
            ("04 December 2015", "2015-12-04"),  # page de détail
            ("4 July 1999", "1999-07-04"),  # jour sur un chiffre
            ("", ""),
        ],
    )
    def test_formats_reconnus(self, brut, attendu):
        assert to_iso(brut) == attendu

    def test_une_date_incomprise_est_rendue_telle_quelle(self):
        """Recopier la source vaut mieux qu'inventer une date."""
        assert to_iso("le 4 du mois") == "le 4 du mois"


# ── Les gardes-fous ───────────────────────────────────────────────────────────
class TestGardeFous:
    def test_g3_une_recherche_vide_n_est_pas_une_panne(self):
        """L'état vide ne rend AUCUN tableau : sans ce test, G1 crierait au faux.

        C'est le piège de ce site — l'absence de `<thead>` y signifie tantôt
        « rien trouvé », tantôt « gabarit changé ».
        """
        vide = "<div>No certified awards found matching your criteria.</div>"
        obs = ObsFactice()
        assert est_vide(vide)
        assert parse_verifie(vide, obs) == []
        assert obs.echecs == []

    def test_g1_un_entete_manquant_est_une_panne(self, liste):
        """Le détecteur de refonte le plus direct, et le moins cher."""
        casse = liste.replace("Latest Certification", "Certified On")
        obs = ObsFactice()
        assert parse_verifie(casse, obs) == []
        assert [kind for kind, _ in obs.echecs] == ["parse"]
        assert "Latest Certification" in obs.echecs[0][1]

    def test_g2_des_lignes_mais_rien_d_extrait(self):
        """Des lignes porteuses présentes et zéro extraite = on ne sait plus lire.

        Jamais `absent` : c'est le seul verdict exclu du numérateur des échecs,
        donc le seul capable de rendre une panne muette.

        Le cas se produit si le site garde son tableau mais déplace l'artiste et
        le titre hors des cellules — un `<td>` vide se lit sans erreur.
        """
        entetes_html = "".join(f"<th>{nom}</th>" for nom in ENTETES_ATTENDUES)
        ligne_creuse = "<tr>" + "<td></td>" * 7 + "</tr>"
        casse = f"<table><thead><tr>{entetes_html}</tr></thead><tbody>{ligne_creuse * 3}</tbody></table>"

        obs = ObsFactice()
        assert len(lignes_de_donnees(casse)) == 3
        assert parse_verifie(casse, obs) == []
        assert [kind for kind, _ in obs.echecs] == ["parse"]
        assert "3 lignes" in obs.echecs[0][1]

    def test_g5_un_niveau_inconnu_n_est_pas_avale(self, liste):
        """Il est conservé verbatim ET signalé — inventer serait pire."""
        casse = liste.replace(">Gold<", ">Bronze<")
        obs = ObsFactice()
        lignes = parse_verifie(casse, obs)
        niveaux = {r["certification_level"] for r in lignes}
        assert "Bronze" in niveaux
        assert not bpi_level_connu("Bronze")

    def test_g4_une_fenetre_non_respectee(self):
        """L'invariant qui manquait à la RIAA : relier la requête au résultat."""
        from src.scrapers.bpi_scraper import verifier_fenetre

        obs = ObsFactice()
        hors = [{"certification_date": "2015-06-01"}, {"certification_date": "2015-07-01"}]
        verifier_fenetre(hors, "2020-01-01", "2020-01-31", obs)
        assert obs.echecs, "toutes les lignes hors fenêtre doit être un échec"

    def test_g4_un_debordement_partiel_ne_fait_que_prevenir(self):
        """Bornes incluses ou non : on ne tranche pas, on signale."""
        from src.scrapers.bpi_scraper import verifier_fenetre

        obs = ObsFactice()
        mixte = [{"certification_date": "2020-01-15"}, {"certification_date": "2020-02-01"}]
        verifier_fenetre(mixte, "2020-01-01", "2020-01-31", obs)
        assert obs.echecs == []
