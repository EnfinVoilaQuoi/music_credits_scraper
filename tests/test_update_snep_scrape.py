"""Boucles de collecte SNEP : pagination, dédup, fusion, idempotence.

Les parsers sont couverts par `test_update_snep_parsing.py`. Ici on teste ce qui
les ENTOURE — les boucles qui décident quand s'arrêter et ce qui est nouveau.
C'est la famille de bugs qui a amputé les discographies Genius (une heuristique
d'arrêt prise pour une fin de liste), et celle du doublon SNEP ci-dessous :
la clé de dédup n'était pas construite de la même façon pour la ligne ENTRANTE
et pour la ligne DÉJÀ EN BASE.
"""

import re as _re
from datetime import datetime

import pytest
import requests

import src.utils.update_snep as us
from src.utils.update_snep import _load_existing, _row_key, scrape_year

HEADER = (
    "Interprete;Titre;Éditeur / Distributeur;Catégorie;Certification;Date de sortie;Date de constat"
)


def _bloc(artiste, titre, label, categorie, certif, sortie, constat):
    """Un bloc `div.certification` tel que le rend snepmusique.com."""
    return f"""
    <div class="certification">
      <div class="description">
        <div class="categorie">{categorie}</div>
        <div class="titre">{titre}</div>
        <div class="artiste">{artiste}</div>
        <div class="editeur">{label}</div>
      </div>
      <div class="certif icon-or">{certif}</div>
      <div class="block_dates">
        <div class="date">{sortie} <span>Date de sortie</span></div>
        <div class="date">{constat} <span>Date de constat</span></div>
      </div>
    </div>
    """


def _page(certifs, last_page=1):
    """Page HTML : les blocs + les liens de pagination que lit `_discover_last_page`."""
    blocs = "".join(_bloc(*c) for c in certifs)
    liens = "".join(f'<a href="/page/{n}">{n}</a>' for n in range(2, last_page + 1))
    return f"<html><body>{blocs}{liens}</body></html>"


def _certif(n, artiste="ISHA", label="Label X"):
    """Une certification distincte, identifiée par son titre."""
    return (artiste, f"Titre {n}", label, "Singles", "Or", "01/01/2020", f"{n:02d}/06/2021")


def _ligne(certif):
    """La ligne CSV que `_parse_certifications_page` produit pour ce bloc."""
    artiste, titre, label, categorie, cert, sortie, constat = certif
    return ";".join([artiste, titre, label, categorie, cert, sortie, constat])


@pytest.fixture(autouse=True)
def _pas_dattente(monkeypatch):
    """Les boucles temporisent entre deux pages : inutile en test."""
    monkeypatch.setattr("src.utils.update_snep.time.sleep", lambda *_: None)


@pytest.fixture
def csv_vide(tmp_path):
    dest = tmp_path / "certif-.csv"
    dest.write_text("﻿" + HEADER + "\n", encoding="utf-8")
    return dest


class TestScrapeYear:
    """`scrape_year` parcourt TOUTE l'année (brique de backfill) : contrairement
    au rattrapage incrémental, elle ne doit pas s'arrêter à la première page
    déjà connue."""

    def _fetch_sequence(self, monkeypatch, pages):
        """Stub de `_fetch` servant `pages[N-1]` pour /page/N ; mémorise les URL vues.

        Adressé par NUMÉRO DE PAGE, pas par ordre d'appel : `scrape_year` sonde
        d'abord la page cumulative (cf. `_page_cumulative_de_lannee`), et un stub
        servant dans l'ordre décalerait tout le reste.
        """
        vues = []

        def fake_fetch(session, url, timeout=None):
            vues.append(url)
            m = _re.search(r"/page/(\d+)", url)
            n = int(m.group(1)) if m else 1
            page = pages[n - 1] if n - 1 < len(pages) else pages[-1]
            if isinstance(page, Exception):
                raise page
            return page

        monkeypatch.setattr(us, "_fetch", fake_fetch)
        return vues

    @staticmethod
    def _pages_vues(vues) -> list[int]:
        """Numéros de page demandés, dans l'ordre (1 pour l'URL sans /page/N)."""
        return [int(m.group(1)) if (m := _re.search(r"/page/(\d+)", u)) else 1 for u in vues]

    def test_parcourt_toutes_les_pages_annoncees(self, csv_vide, monkeypatch):
        pages = [
            _page([_certif(1), _certif(2)], last_page=3),
            _page([_certif(3), _certif(4)]),
            _page([_certif(5), _certif(6)]),
        ]
        vues = self._fetch_sequence(monkeypatch, pages)

        assert scrape_year(csv_vide, 2020).ajoutees == 6
        # p2 est demandée deux fois : la sonde de page cumulative, puis le
        # parcours. L'invariant l'a rejetée (2 blocs, hors de ]2, 4]) → repli.
        assert self._pages_vues(vues) == [1, 2, 2, 3]

    def test_page_deja_connue_ne_stoppe_pas_le_backfill(self, csv_vide, monkeypatch):
        """La page 2 est intégralement connue : la 3 doit quand même être lue."""
        csv_vide.write_text(
            "﻿" + HEADER + "\n" + _ligne(_certif(3)) + "\n" + _ligne(_certif(4)) + "\n",
            encoding="utf-8",
        )
        pages = [
            _page([_certif(1), _certif(2)], last_page=3),
            _page([_certif(3), _certif(4)]),  # 0 nouveauté
            _page([_certif(5)]),
        ]
        vues = self._fetch_sequence(monkeypatch, pages)

        assert scrape_year(csv_vide, 2020).ajoutees == 3  # 1, 2 et 5
        assert self._pages_vues(vues) == [1, 2, 2, 3]  # sonde + parcours complet

    def test_page_sans_bloc_arrete_la_boucle(self, csv_vide, monkeypatch):
        pages = [_page([_certif(1)], last_page=4), "<html><body></body></html>"]
        vues = self._fetch_sequence(monkeypatch, pages)

        assert scrape_year(csv_vide, 2020).ajoutees == 1
        # p2 vide : la couverture géométrique abandonne aussitôt (une page ≤ P
        # rend au moins une tranche sous le modèle mesuré), puis le parcours
        # s'arrête sur la même page vide. Ni p3 ni p4 ne sont demandées.
        assert self._pages_vues(vues) == [1, 2, 2]

    def test_erreur_reseau_en_cours_conserve_le_deja_collecte(self, csv_vide, monkeypatch):
        pages = [
            _page([_certif(1)], last_page=3),
            requests.RequestException("coupure"),
        ]
        self._fetch_sequence(monkeypatch, pages)

        bilan = scrape_year(csv_vide, 2020)

        assert bilan.ajoutees == 1
        assert _ligne(_certif(1)) in csv_vide.read_text(encoding="utf-8-sig")
        # Ce qui a été collecté est BON — mais l'année n'a pas été finie, et
        # l'appelant doit pouvoir le savoir pour ne pas horodater sa fraîcheur.
        assert not bilan.complete
        assert "coupure" in bilan.motif

    def test_page_1_inaccessible_ne_touche_pas_le_csv(self, csv_vide, monkeypatch):
        avant = csv_vide.read_text(encoding="utf-8-sig")
        self._fetch_sequence(monkeypatch, [requests.RequestException("503")])

        bilan = scrape_year(csv_vide, 2020)

        assert bilan.ajoutees == 0
        assert csv_vide.read_text(encoding="utf-8-sig") == avant
        # « 0 ajoutée » ne doit pas se lire comme « rien de neuf cette année ».
        assert not bilan.complete

    def test_max_pages_tronque_mais_le_DIT(self, csv_vide, monkeypatch):
        """Le plafond rabotait l'année EN SILENCE (`min(nb_pages, max_pages)`).

        Ce test gelait ce silence : il vérifiait qu'on lit 2 pages sur 10 et
        n'exigeait rien de plus. Or 8 pages d'une année perdues sans un mot,
        c'est exactement le défaut RIAA du 2026-09-09 — un corpus tronqué
        annoncé comme complet, et une fraîcheur horodatée par-dessus.
        """
        pages = [_page([_certif(i)], last_page=10) for i in range(1, 11)]
        vues = self._fetch_sequence(monkeypatch, pages)

        bilan = scrape_year(csv_vide, 2020, max_pages=2)

        assert bilan.ajoutees == 2
        assert len(vues) == 2
        assert not bilan.complete, "l'année est amputée de 8 pages sans le dire"
        assert "plafond" in bilan.motif

    def test_dedup_contre_lexistant(self, csv_vide, monkeypatch):
        csv_vide.write_text("﻿" + HEADER + "\n" + _ligne(_certif(1)) + "\n", encoding="utf-8")
        self._fetch_sequence(monkeypatch, [_page([_certif(1), _certif(2)])])

        assert scrape_year(csv_vide, 2020).ajoutees == 1
        contenu = csv_vide.read_text(encoding="utf-8-sig")
        assert contenu.count(_ligne(_certif(1))) == 1

    def test_ligne_tronquee_ignoree(self, csv_vide, monkeypatch):
        """Un bloc dont il manque un champ n'est pas parsé : rien à fusionner."""
        bloc_partiel = """
        <div class="certification">
          <div class="description"><div class="titre">Sans artiste</div></div>
          <div class="certif">Or</div>
          <div class="block_dates"></div>
        </div>
        """
        self._fetch_sequence(monkeypatch, [f"<html><body>{bloc_partiel}</body></html>"])
        assert scrape_year(csv_vide, 2020).ajoutees == 0


class TestMiseAJourNominale:
    """`update_snep_database` parcourt désormais l'ANNÉE COMPLÈTE.

    `scrape_recent_certifications`, qui s'arrêtait à la première page
    intégralement connue, a été retirée le 2026-09-04 : mesurée sur le site
    réel elle rendait 0 là où le parcours exhaustif des mêmes années trouvait
    159 certifications. Le SNEP antidate, il n'y a donc pas de préfixe « récent »
    sur lequel s'arrêter.
    """

    def test_annee_courante_seule_hors_debut_dannee(self):
        assert us._years_to_scrape(datetime(2026, 9, 4)) == [2026]

    @pytest.mark.parametrize("mois", [1, 2])
    def test_annee_precedente_incluse_en_debut_dannee(self, mois):
        """Une certification publiée en janvier peut porter une date de constat
        de décembre : elle n'apparaît alors que dans le classement de l'an passé."""
        assert us._years_to_scrape(datetime(2026, mois, 15)) == [2026, 2025]

    def test_lancienne_fonction_a_bien_disparu(self):
        """Garde-fou : rebrancher un passage incrémental doit être un choix
        conscient, pas un retour en arrière silencieux."""
        assert not hasattr(us, "scrape_recent_certifications")


#: Le libellé d'éditeur contient un point-virgule — c'est le séparateur du CSV.
#: Cas RÉEL, unique sur les 12 808 lignes de `data/certifications/snep/certif-.csv` :
#: « AYA NAKAMURA;DNK;"REC; 118 / WARNER MUSIC FRANCE";Albums;Platine;… »
LABEL_AVEC_SEPARATEUR = (
    "AYA NAKAMURA",
    "DNK",
    "REC; 118 / WARNER",
    "Albums",
    "Platine",
    "27/01/2023",
    "31/08/2023",
)


class TestCleDeDedupAvecSeparateurDansLeLabel:
    """Une ligne dont le LABEL contient un `;` se découpe en 8 champs au lieu de 7.

    La clé entrante était construite sur des indices POSITIFS (`f[3..6]`) et la
    clé de l'existant sur des indices NÉGATIFS (`f[-4..-1]`) : sur 8 champs les
    deux cessent de coïncider, la ligne n'est jamais reconnue et se ré-ajoute à
    chaque passage. Les indices négatifs sont les bons — le label est le seul
    champ de longueur variable, et les 4 derniers champs sont fixes.
    """

    def test_meme_cle_pour_lentrante_et_lexistante(self, tmp_path):
        ligne = _ligne(LABEL_AVEC_SEPARATEUR)
        assert len(ligne.split(";")) == 8  # le prérequis du test

        dest = tmp_path / "certif-.csv"
        dest.write_text("﻿" + HEADER + "\n" + ligne + "\n", encoding="utf-8")
        _, _, cles_existantes = _load_existing(dest)

        assert _row_key(ligne.split(";")) in cles_existantes

    def test_pas_de_doublon_au_rescrape(self, tmp_path, monkeypatch):
        """Le bout en bout : la ligne est déjà en base, la page la re-sert."""
        ligne = _ligne(LABEL_AVEC_SEPARATEUR)
        dest = tmp_path / "certif-.csv"
        dest.write_text("﻿" + HEADER + "\n" + ligne + "\n", encoding="utf-8")

        monkeypatch.setattr(us, "_fetch", lambda *a, **k: _page([LABEL_AVEC_SEPARATEUR]))

        assert scrape_year(dest, 2023).ajoutees == 0
        assert dest.read_text(encoding="utf-8-sig").count(ligne) == 1


class TestCouvertureGeometrique:
    """Le SNEP applique `LIMIT 30×N OFFSET 30×(N−1)` : la page N ne rend pas la
    Nᵉ tranche de 30 mais les tranches **N à 2N−1**.

    Mesuré sur le site réel le 2026-09-06 (2025 : 51 tranches, 1 528 certifs) —
    le modèle `min(30N, total − 30(N−1))` colle à l'unité près sur p1, p2, p3,
    p19, p25, p26, p27, p50, p51, et le CONTENU le confirme (p2 disjointe de p1,
    p27 ⊂ p26, p25 ∩ p26 = 720).

    Conséquence : les puissances de deux couvrent tout en lisant chaque
    certification UNE fois. Vérifié contre le parcours des 51 pages, ensembles
    IDENTIQUES : **6 requêtes et 1 528 blocs au lieu de 51 et 20 228** (15 s
    contre 172 s).
    """

    PAR_PAGE = 30

    def _site_snep(self, monkeypatch, total: int):
        """Rejoue le bug mesuré : /page/N rend les tranches N..min(2N−1, P)."""
        certifs = [_certif(i) for i in range(1, total + 1)]
        P = (total + self.PAR_PAGE - 1) // self.PAR_PAGE
        vues = []

        def fake_fetch(session, url, timeout=None):
            vues.append(url)
            m = _re.search(r"/page/(\d+)", url)
            n = int(m.group(1)) if m else 1
            debut, fin = n, min(2 * n - 1, P)
            tranche = certifs[(debut - 1) * self.PAR_PAGE : fin * self.PAR_PAGE]
            return _page(tranche, last_page=P)

        monkeypatch.setattr(us, "_fetch", fake_fetch)
        return vues, P

    def test_le_modele_de_page_est_bien_celui_mesure(self, monkeypatch, csv_vide):
        """Garde-fou du simulateur lui-même : sans lui, les tests ci-dessous
        vérifieraient un site imaginaire."""
        vues, P = self._site_snep(monkeypatch, total=1528)
        assert P == 51
        for n, attendu in ((1, 30), (2, 60), (25, 750), (26, 778), (27, 748), (51, 28)):
            html = us._fetch(None, f"/page/{n}" if n > 1 else "?annee=2025")
            assert len(us._parse_certifications_page(html)) == attendu, f"page {n}"

    def test_couverture_complete_en_log2_requetes(self, csv_vide, monkeypatch):
        vues, P = self._site_snep(monkeypatch, total=1528)

        bilan = scrape_year(csv_vide, 2025)
        assert bilan.ajoutees == 1528  # TOUTE l'année
        assert bilan.complete and not bilan.motif
        assert self._pages_demandees(vues) == [1, 2, 4, 8, 16, 32]

    def test_les_puissances_de_deux_pavent_lintervalle(self):
        """Propriété structurelle : page 2^k couvre 2^k..2^(k+1)−1."""
        for nb in (1, 2, 3, 7, 21, 51, 400):
            couvert = set()
            for p in us._pages_couvrantes(nb):
                couvert |= set(range(p, min(2 * p - 1, nb) + 1))
            assert couvert == set(range(1, nb + 1)), f"trou pour P={nb}"

    def test_annee_tenant_sur_une_page(self, csv_vide, monkeypatch):
        vues, P = self._site_snep(monkeypatch, total=12)
        assert P == 1
        assert scrape_year(csv_vide, 2020).ajoutees == 12
        assert self._pages_demandees(vues) == [1]  # page 1 déjà en main

    def test_site_pagine_normalement_retombe_sur_le_parcours(self, csv_vide, monkeypatch):
        """L'invariant porte sur le TOTAL, pas sur une page : un site où chaque
        page rend au plus `par_page` éléments ne peut pas l'atteindre dès P ≥ 2.

        C'est la leçon de la première version, qui vérifiait UNE page et acceptait
        un résultat amputé de moitié sans que rien ne le signale."""
        pages = [
            _page([_certif(1), _certif(2)], last_page=3),
            _page([_certif(3), _certif(4)]),
            _page([_certif(5), _certif(6)]),
        ]
        vues = []

        def fake_fetch(session, url, timeout=None):
            vues.append(url)
            m = _re.search(r"/page/(\d+)", url)
            n = int(m.group(1)) if m else 1
            return pages[n - 1] if n - 1 < len(pages) else pages[-1]

        monkeypatch.setattr(us, "_fetch", fake_fetch)
        assert scrape_year(csv_vide, 2020).ajoutees == 6  # rien de perdu : le repli lit tout
        assert 3 in self._pages_demandees(vues)

    def test_page_inaccessible_pendant_la_couverture_bascule_sur_le_parcours(
        self, csv_vide, monkeypatch
    ):
        # Corpus réduit : ce test exerce le REPLI, qui parcourt toutes les pages
        # (coût quadratique du bug du site). À l'échelle réelle il passerait 22 s
        # à revérifier ce que `test_couverture_complete_en_log2_requetes` établit
        # déjà — la propriété testée ici est la BASCULE, pas le volume.
        self._site_snep(monkeypatch, total=200)
        vrai_fetch = us._fetch
        echecs = {"restants": 1}

        def fetch_capricieux(session, url, timeout=None):
            """Échoue UNE fois sur /page/4 — pendant la passe géométrique — puis
            se rétablit, pour que le repli puisse aller au bout."""
            if "/page/4?" in url and echecs["restants"]:
                echecs["restants"] -= 1
                raise requests.RequestException("503")
            return vrai_fetch(session, url, timeout)

        monkeypatch.setattr(us, "_fetch", fetch_capricieux)
        # Le repli parcourt toutes les pages : le résultat reste COMPLET.
        assert scrape_year(csv_vide, 2025).ajoutees == 200

    @staticmethod
    def _pages_demandees(vues) -> list[int]:
        return [int(m.group(1)) if (m := _re.search(r"/page/(\d+)", u)) else 1 for u in vues]
