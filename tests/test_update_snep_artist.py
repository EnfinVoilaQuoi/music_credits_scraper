"""Récupération SNEP par ARTISTE et fusion dans le CSV maître (`update_snep`).

Deux règles y sont vérifiées, toutes deux issues de bugs réels :

1. **Filtre mot entier.** Le site SNEP fait un `contains` sur `?interprete=` :
   chercher « IAM » ramène WILLIAMS, LIAM, DIAM'S, IAMCHINO. Sans le filtre,
   ces certifications atterrissent dans le CSV maître et deviennent
   indiscernables des vraies.

2. **Une recherche ARTISTE n'est PAS une MàJ globale** (JOURNAL 2026-06-25).
   La fraîcheur est tenue PAR SOURCE dans `certif_snep.meta.json` : si une
   récupération ciblée écrivait `GLOBAL`, la base paraîtrait à jour alors que
   seul un artiste a été rafraîchi.

3. **Les PAGES, pas l'export** (2026-09-17). L'export CSV que le site génère
   pour `?interprete=` est plafonné aux blocs de la page qui l'a produit (30
   lignes pour NINHO, 147 en base) et son fichier est partagé entre
   chargements : la récupération par artiste était TRONQUÉE à 30 depuis
   toujours pour un artiste prolifique, et la confirmation des retraits
   « confirmait » tout ce qui vivait au-delà de la page 1.

Le réseau est remplacé par un faux `_fetch` servant des pages HTML ; tout le
reste (filtrage, fusion, régénération du clean, méta) tourne pour de vrai sur
`tmp_path`.
"""

import json
import re as _re

import pytest
import requests

import tests.test_update_snep_scrape as _tus  # `_page` réexposée par attribut (F811)
from src.utils import update_snep as us

RAW_HEADER = (
    "Interprete;Titre;Éditeur / Distributeur;Catégorie;Certification;"
    "Date de sortie;Date de constat"
)


def _ligne(artiste, titre="TITRE", lvl="Or", constat="02/10/2025"):
    return f"{artiste};{titre};LABEL;Singles;{lvl};01/01/2019;{constat}"


def _html(lignes, last_page=1):
    """La page HTML du site rendant ces lignes CSV (blocs `div.certification`)."""
    return _tus._page([tuple(ligne.split(";")) for ligne in lignes], last_page=last_page)


@pytest.fixture
def snep_dir(tmp_path, monkeypatch):
    """Arborescence de données isolée + matcher neutralisé."""
    monkeypatch.setattr(us, "DATA_PATH", str(tmp_path))
    d = tmp_path / "certifications" / "snep"
    d.mkdir(parents=True)
    monkeypatch.setattr("src.utils.cert_matcher.reset_cert_matcher", lambda: None)
    return d


@pytest.fixture
def site(monkeypatch):
    """Faux site : `pages[N]` = HTML (ou exception) servi pour /page/N ; les
    URL demandées sont mémorisées. Une page non déclarée = coupure réseau."""
    pages: dict[int, object] = {}
    appels = []

    def fake_fetch(session, url, timeout=None):
        appels.append(url)
        m = _re.search(r"/page/(\d+)", url)
        page = pages.get(int(m.group(1)) if m else 1)
        if page is None:
            raise requests.ConnectionError(f"page absente : {url}")
        if isinstance(page, Exception):
            raise page
        return page

    monkeypatch.setattr(us, "_fetch", fake_fetch)
    monkeypatch.setattr(us.time, "sleep", lambda *_: None)
    return pages, appels


class TestFiltreMotEntier:
    """Le SNEP fait un `contains` : à nous de resserrer."""

    @pytest.mark.parametrize("bruit", ["WILLIAMS", "LIAM", "DIAM'S", "IAMCHINO"])
    def test_bruit_de_sous_chaine_ecarte(self, bruit):
        assert us._artist_matches(bruit, "IAM") is False

    @pytest.mark.parametrize("vrai", ["IAM", "iam", "IAM & AKHENATON", "AKHENATON, IAM"])
    def test_vrai_artiste_garde(self, vrai):
        assert us._artist_matches(vrai, "IAM") is True

    def test_accents_indifferents(self):
        assert us._artist_matches("ANGELE", "Angèle") is True

    def test_requete_vide_ne_filtre_rien(self):
        assert us._artist_matches("N'IMPORTE QUI", "") is True


class TestRecuperationParArtiste:
    def _lancer(self, site, snep_dir, lignes, nom_artiste="IAM"):
        pages, _ = site
        pages[1] = _html(lignes)
        return us.fetch_artist_certifications(nom_artiste)

    def test_nominal(self, site, snep_dir):
        ok = self._lancer(site, snep_dir, [_ligne("IAM", "DEMAIN C'EST LOIN")])
        assert ok is True
        brut = (snep_dir / "certif-.csv").read_text(encoding="utf-8-sig")
        assert "DEMAIN C'EST LOIN" in brut

    def test_bruit_ecarte_du_csv_maitre(self, site, snep_dir):
        """Le cœur du garde-fou : WILLIAMS ne doit pas entrer dans la base."""
        ok = self._lancer(
            site,
            snep_dir,
            [_ligne("IAM", "VRAI TITRE"), _ligne("WILLIAMS", "FAUX TITRE")],
        )
        assert ok is True
        brut = (snep_dir / "certif-.csv").read_text(encoding="utf-8-sig")
        assert "VRAI TITRE" in brut
        assert "FAUX TITRE" not in brut

    def test_que_du_bruit_abandonne(self, site, snep_dir):
        """Aucune ligne ne correspond : on n'écrit RIEN plutôt que d'écrire du
        bruit (artiste mal orthographié ou inconnu)."""
        ok = self._lancer(site, snep_dir, [_ligne("WILLIAMS"), _ligne("LIAM")])
        assert ok is False
        assert not (snep_dir / "certif-.csv").exists()

    def test_fusion_avec_l_existant(self, site, snep_dir):
        """Le CSV maître ACCUMULE : une récup par artiste ne l'écrase pas."""
        (snep_dir / "certif-.csv").write_text(
            "﻿" + RAW_HEADER + "\n" + _ligne("JUL", "ANCIEN") + "\n", encoding="utf-8"
        )
        self._lancer(site, snep_dir, [_ligne("IAM", "NOUVEAU")])
        brut = (snep_dir / "certif-.csv").read_text(encoding="utf-8-sig")
        assert "ANCIEN" in brut
        assert "NOUVEAU" in brut

    def test_source_artist_dans_la_meta(self, site, snep_dir):
        """Règle JOURNAL 2026-06-25 : une récup ciblée est tracée ARTIST, pas
        GLOBAL — sinon la base paraît globalement à jour."""
        self._lancer(site, snep_dir, [_ligne("IAM", "TITRE")])
        meta = json.loads((snep_dir / "certif_snep.meta.json").read_text(encoding="utf-8"))
        assert meta["last_source"] == "ARTIST"
        assert "ARTIST" in meta["updates"]
        assert "GLOBAL" not in meta["updates"]

    def test_fraicheur_globale_preservee(self, site, snep_dir):
        """Une MàJ globale antérieure garde SA date : la récup artiste ajoute une
        entrée à côté, elle ne repeint pas l'historique global."""
        (snep_dir / "certif_snep.meta.json").write_text(
            json.dumps({"updates": {"GLOBAL": "2020-01-01T00:00:00"}}), encoding="utf-8"
        )
        self._lancer(site, snep_dir, [_ligne("IAM", "TITRE")])
        meta = json.loads((snep_dir / "certif_snep.meta.json").read_text(encoding="utf-8"))
        assert meta["updates"]["GLOBAL"] == "2020-01-01T00:00:00"
        assert meta["updates"]["ARTIST"] != "2020-01-01T00:00:00"

    def test_clean_regenere(self, site, snep_dir):
        self._lancer(site, snep_dir, [_ligne("IAM", "TITRE")])
        clean = (snep_dir / "certif_snep.csv").read_text(encoding="utf-8-sig")
        assert "TITRE" in clean


class TestEchecsReseau:
    def test_page_artiste_inaccessible(self, site, snep_dir):
        pages, _ = site
        pages[1] = requests.ConnectionError("HTTP 503")
        assert us.fetch_artist_certifications("IAM") is False

    def test_aucun_bloc_abandonne(self, site, snep_dir):
        """Page servie mais vide : artiste inconnu du SNEP, on n'écrit rien."""
        pages, _ = site
        pages[1] = "<html>rien ici</html>"
        assert us.fetch_artist_certifications("Inconnu") is False
        assert not (snep_dir / "certif-.csv").exists()

    def test_page_suivante_inaccessible_abandonne(self, site, snep_dir):
        """Un listing partiel n'est pas un listing : la 2ᵉ page tombe, rien
        n'est écrit (une récupération tronquée passerait pour complète)."""
        pages, _ = site
        pages[1] = _html([_ligne("IAM", f"T{i}") for i in range(30)], last_page=3)
        assert us.fetch_artist_certifications("IAM") is False
        assert not (snep_dir / "certif-.csv").exists()


class TestListingComplet:
    """La page N rend les tranches N..2N−1 (`_lignes_du_filtre`, mécanique du
    SITE) : 65 certifications = 3 tranches, lues en 2 requêtes (pages 1 et 2),
    là où l'export — et la lecture de la seule page 1 — s'arrêtaient à 30."""

    def _site_de_65(self, site):
        pages, appels = site
        lignes = [_ligne("CELINE DION", f"TITRE {i:02d}") for i in range(65)]
        pages[1] = _html(lignes[:30], last_page=3)
        pages[2] = _html(lignes[30:65])  # tranches 2 et 3
        pages[3] = _html(lignes[60:65])
        return lignes, appels

    def test_recuperation_par_artiste_n_est_plus_tronquee_a_30(self, site, snep_dir):
        lignes, appels = self._site_de_65(site)
        assert us.fetch_artist_certifications("CELINE DION") is True
        brut = (snep_dir / "certif-.csv").read_text(encoding="utf-8-sig")
        assert sum(1 for ligne in lignes if ligne in brut) == 65
        assert _tus.TestScrapeYear._pages_vues(appels) == [1, 2]

    def test_confirmation_des_retraits_voit_toute_la_page_artiste(self, site):
        lignes, _ = self._site_de_65(site)
        assert us._page_artiste("CELINE DION") == lignes

    def test_page_inaccessible_laisse_indecise(self, site):
        pages, _ = site
        pages[1] = requests.ConnectionError("HTTP 503")
        assert us._page_artiste("CELINE DION") is None

    def test_listing_partiel_laisse_indecise(self, site):
        """Une absence qu'on n'a pas pu vérifier n'est pas un retrait."""
        pages, _ = site
        pages[1] = _html([_ligne("CELINE DION", f"T{i}") for i in range(30)], last_page=3)
        assert us._page_artiste("CELINE DION") is None


class TestCsvMaitre:
    def test_chargement_d_un_fichier_absent(self, tmp_path):
        header, lignes, cles = us._load_existing(tmp_path / "rien.csv")
        assert header == us._CSV_HEADER
        assert lignes == []
        assert cles == set()

    def test_cles_extraites(self, tmp_path):
        """La clé sert à ne pas réécrire une ligne déjà présente."""
        p = tmp_path / "c.csv"
        p.write_text("﻿" + RAW_HEADER + "\n" + _ligne("JUL", "TITRE") + "\n", encoding="utf-8")
        header, lignes, cles = us._load_existing(p)
        assert header == RAW_HEADER
        assert len(lignes) == 1
        assert len(cles) == 1

    def test_lignes_vides_ignorees(self, tmp_path):
        p = tmp_path / "c.csv"
        p.write_text(RAW_HEADER + "\n" + _ligne("JUL") + "\n\n   \n", encoding="utf-8")
        _, lignes, _ = us._load_existing(p)
        assert len(lignes) == 1

    def test_ligne_trop_courte_sans_cle(self, tmp_path):
        """Une ligne malformée ne produit pas de clé : elle est conservée mais
        ne sert pas de référence de déduplication."""
        p = tmp_path / "c.csv"
        p.write_text(RAW_HEADER + "\nJUL;INCOMPLET\n", encoding="utf-8")
        _, lignes, cles = us._load_existing(p)
        assert len(lignes) == 1
        assert cles == set()

    def test_ecriture_de_l_union(self, tmp_path):
        p = tmp_path / "c.csv"
        us._write_merged(p, RAW_HEADER, [_ligne("JUL")], [_ligne("IAM")])
        contenu = p.read_text(encoding="utf-8-sig")
        assert contenu.startswith(RAW_HEADER)
        assert "JUL" in contenu and "IAM" in contenu
        assert p.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM conservé

    def test_aucune_nouveaute_aucune_ecriture(self, tmp_path):
        """Pas de nouveauté = pas de réécriture : on ne touche pas au fichier
        maître pour rien (et on ne casse pas sa date de modification)."""
        p = tmp_path / "c.csv"
        us._write_merged(p, RAW_HEADER, [_ligne("JUL")], [])
        assert not p.exists()


class TestPagination:
    def test_derniere_page_deduite(self):
        html = '<a href="/page/2">2</a><a href="/page/7">7</a><a href="/page/3">3</a>'
        assert us._discover_last_page(html) == 7

    def test_page_unique(self):
        assert us._discover_last_page("<html>aucun lien</html>") == 1


def test_affichage_resiste_a_un_stdout_ferme(monkeypatch, caplog):
    """L'app rewrappe stdout à plusieurs endroits : un print sur un flux fermé
    ne doit pas interrompre une mise à jour de certifs (cf. règle projet)."""

    def _ferme(*a, **k):
        raise ValueError("I/O operation on closed file")

    monkeypatch.setattr("builtins.print", _ferme)
    us.safe_print("message important")
    assert "message important" in caplog.text

    def test_page_vide_laisse_indecise(self, site):
        """Le nom vient du crédit LOCAL et le SNEP réécrit ses crédits
        (« MAITRE GIMS » → « GIMS ») : une page vide ne prouve pas un retrait."""
        pages, _ = site
        pages[1] = "<html>rien</html>"
        assert us._page_artiste("MAITRE GIMS") is None
