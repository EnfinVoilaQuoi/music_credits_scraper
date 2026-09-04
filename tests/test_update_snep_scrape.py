"""Boucles de collecte SNEP : pagination, dédup, fusion, idempotence.

Les parsers sont couverts par `test_update_snep_parsing.py`. Ici on teste ce qui
les ENTOURE — les boucles qui décident quand s'arrêter et ce qui est nouveau.
C'est la famille de bugs qui a amputé les discographies Genius (une heuristique
d'arrêt prise pour une fin de liste), et celle du doublon SNEP ci-dessous :
la clé de dédup n'était pas construite de la même façon pour la ligne ENTRANTE
et pour la ligne DÉJÀ EN BASE.
"""

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
        """Stub de `_fetch` servant `pages` dans l'ordre ; mémorise les URL vues."""
        vues = []

        def fake_fetch(session, url, timeout=None):
            vues.append(url)
            idx = len(vues) - 1
            page = pages[idx] if idx < len(pages) else pages[-1]
            if isinstance(page, Exception):
                raise page
            return page

        monkeypatch.setattr(us, "_fetch", fake_fetch)
        return vues

    def test_parcourt_toutes_les_pages_annoncees(self, csv_vide, monkeypatch):
        pages = [
            _page([_certif(1), _certif(2)], last_page=3),
            _page([_certif(3), _certif(4)]),
            _page([_certif(5), _certif(6)]),
        ]
        vues = self._fetch_sequence(monkeypatch, pages)

        assert scrape_year(csv_vide, 2020) == 6
        assert len(vues) == 3
        assert "?annee=2020" in vues[0]
        assert "page/2" in vues[1] and "page/3" in vues[2]

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

        assert scrape_year(csv_vide, 2020) == 3  # 1, 2 et 5
        assert len(vues) == 3

    def test_page_sans_bloc_arrete_la_boucle(self, csv_vide, monkeypatch):
        pages = [_page([_certif(1)], last_page=4), "<html><body></body></html>"]
        vues = self._fetch_sequence(monkeypatch, pages)

        assert scrape_year(csv_vide, 2020) == 1
        assert len(vues) == 2  # la page 3 n'est pas demandée

    def test_erreur_reseau_en_cours_conserve_le_deja_collecte(self, csv_vide, monkeypatch):
        pages = [
            _page([_certif(1)], last_page=3),
            requests.RequestException("coupure"),
        ]
        self._fetch_sequence(monkeypatch, pages)

        assert scrape_year(csv_vide, 2020) == 1
        assert _ligne(_certif(1)) in csv_vide.read_text(encoding="utf-8-sig")

    def test_page_1_inaccessible_ne_touche_pas_le_csv(self, csv_vide, monkeypatch):
        avant = csv_vide.read_text(encoding="utf-8-sig")
        self._fetch_sequence(monkeypatch, [requests.RequestException("503")])

        assert scrape_year(csv_vide, 2020) == 0
        assert csv_vide.read_text(encoding="utf-8-sig") == avant

    def test_max_pages_plafonne_la_pagination(self, csv_vide, monkeypatch):
        pages = [_page([_certif(i)], last_page=10) for i in range(1, 11)]
        vues = self._fetch_sequence(monkeypatch, pages)

        assert scrape_year(csv_vide, 2020, max_pages=2) == 2
        assert len(vues) == 2

    def test_dedup_contre_lexistant(self, csv_vide, monkeypatch):
        csv_vide.write_text("﻿" + HEADER + "\n" + _ligne(_certif(1)) + "\n", encoding="utf-8")
        self._fetch_sequence(monkeypatch, [_page([_certif(1), _certif(2)])])

        assert scrape_year(csv_vide, 2020) == 1
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
        assert scrape_year(csv_vide, 2020) == 0


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

        assert scrape_year(dest, 2023) == 0
        assert dest.read_text(encoding="utf-8-sig").count(ligne) == 1
