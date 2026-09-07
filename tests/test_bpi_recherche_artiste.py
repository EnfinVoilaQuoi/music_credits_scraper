"""Comportement du scraper BPI : résolution d'artiste, pagination, paliers.

Aucun réseau : `_get` est remplacé et on observe ce qui est demandé.

Trois constats y sont GELÉS, chacun pour une raison mesurée ailleurs :

1. l'annuaire du site match en SOUS-CHAÎNE NUE (`q=iam` → « ALYSON WILLIAMS ») ;
2. une entité BPI est une chaîne de crédit, donc un artiste = PLUSIEURS ids ;
3. la pagination ne s'arrête QUE sur une page vide — jamais sur une page courte.
   L'API Genius sert des pages courtes en plein milieu de la liste, et en déduire
   la fin y amputait une discographie de moitié, en silence.
"""

import asyncio

import pytest

from src.scrapers.bpi_scraper import ENTETES_ATTENDUES, BpiScraper


def run(coro):
    """Pilotage sync, comme `tests/test_async_loop.py` (pas de pytest-asyncio)."""
    return asyncio.run(coro)


# ── Fabriques de pages ────────────────────────────────────────────────────────
PAGE_VIDE = "<div>No certified awards found matching your criteria.</div>"


def ligne(artiste, titre, *, niveau="Silver", fmt="Single", fid=2, aid=1, tid=1):
    return (
        f'<tr hx-get="/format/{fid}/artist/{aid}/title/{tid}">'
        f"<td>{artiste}</td><td>{titre}</td>"
        f'<td><img alt="{niveau}"/><span>{niveau}</span></td>'
        f"<td>{fmt}</td><td>LABEL</td><td>01.01.2020</td><td>01.01.2019</td></tr>"
    )


def page_liste(*lignes):
    """PAGE 1 : un `<table>` complet, avec ses en-têtes."""
    entetes = "".join(f"<th>{nom}</th>" for nom in ENTETES_ATTENDUES)
    corps = "".join(lignes)
    return f"<table><thead><tr>{entetes}</tr></thead><tbody>{corps}</tbody></table>"


def page_suite(*lignes):
    """PAGES 2+ : des `<tr>` NUS, que htmx ajoute au tableau déjà affiché.

    Fabrique distincte de `page_liste` À DESSEIN. Les tests de pagination
    servaient auparavant des pages 2+ avec un `<thead>` — une forme que le site
    ne produit jamais — et laissaient donc passer le défaut qui a tronqué le
    balayage complet à 24 titres sur ~26 500.
    """
    return "".join(lignes)


def page_annuaire(*paires):
    return "".join(f'<li><input value="{i}"/><span>{n}</span></li>' for i, n in paires)


def page_detail(*paliers):
    entrees = "".join(f"<div><p>{d}</p><p>{n}</p><img alt='{n}'/></div>" for d, n in paliers)
    return f"<div><p>Certification history</p><div>{entrees}</div></div>"


@pytest.fixture
def scraper(monkeypatch):
    """Scraper dont le transport est simulé : il note les appels et sert du HTML.

    `reponses` associe un chemin à une liste de réponses servies dans l'ordre
    (une par appel), ce qui permet de simuler une pagination.
    """
    s = BpiScraper()
    s.appels = []
    s.reponses = {}

    async def faux_get(chemin, *, params=None, hx=True):
        s.appels.append((chemin, dict(params or {}), hx))
        file = s.reponses.get(chemin)
        if file is None:
            return PAGE_VIDE
        return file.pop(0) if len(file) > 1 else file[0]

    monkeypatch.setattr(s, "_get", faux_get)
    return s


def pages_demandees(scraper):
    return [p.get("page", 1) for c, p, _ in scraper.appels if c == "/"]


# ── L'annuaire ────────────────────────────────────────────────────────────────
class TestResolutionArtiste:
    def test_un_artiste_rend_plusieurs_entites(self, scraper):
        """« SIGALA » n'est pas un id mais une famille d'ids.

        Ne retenir que la graphie exacte amputerait la moitié de sa discographie
        certifiée : les featurings sont facturés sous une entité DISTINCTE.
        """
        scraper.reponses["/artists"] = [
            page_annuaire(
                (3345, "SIGALA"),
                (4257, "SIGALA & BECKY HILL"),
                (3973, "KATO/SIGALA/HAILEE STEINFELD"),
            ),
            "",
        ]
        assert [i for i, _ in run(scraper.resoudre_artistes("Sigala"))] == [3345, 4257, 3973]

    def test_la_sous_chaine_nue_du_site_est_filtree(self, scraper):
        """`q=iam` rend WILLIAMS : le piège maison, cette fois côté serveur.

        C'est exactement « IAM ⊂ Williams » — mesuré sur le site le 2026-09-07,
        et la raison pour laquelle la résolution passe par `contains_as_words`.
        """
        scraper.reponses["/artists"] = [
            page_annuaire(
                (2996, "ALYSON WILLIAMS"),
                (296, "ANDY WILLIAMS"),
                (2074, "DAFT PUNK FT PHARRELL WILLIAMS"),
                (5081, "IAMDDB"),
                (111, "IAM"),
                (112, "IAM & AKHENATON"),
            ),
            "",
        ]
        retenus = run(scraper.resoudre_artistes("IAM"))
        assert [i for i, _ in retenus] == [111, 112]

    def test_un_nom_vide_ne_declenche_aucun_appel(self, scraper):
        assert run(scraper.resoudre_artistes("  ")) == []
        assert scraper.appels == []


# ── La pagination ─────────────────────────────────────────────────────────────
class TestPagination:
    def test_une_page_courte_n_arrete_PAS_la_pagination(self, scraper):
        """Le piège Genius, transposé : seule une page VIDE signifie la fin.

        Ici la page 2 ne rend que 2 lignes sur 24 possibles ; s'arrêter là
        perdrait tout ce qui suit, sans le moindre signal.
        """
        scraper.reponses["/"] = [
            page_liste(*[ligne(f"A{i}", f"T{i}", tid=i) for i in range(24)]),
            page_suite(ligne("B1", "U1", tid=101), ligne("B2", "U2", tid=102)),
            page_suite(*[ligne(f"C{i}", f"V{i}", tid=200 + i) for i in range(5)]),
            PAGE_VIDE,
        ]
        lignes = run(scraper.scrape_by_date_range("2020-01-01", "2020-12-31"))
        assert len(lignes) == 31
        assert pages_demandees(scraper) == [1, 2, 3, 4]

    def test_l_arret_se_fait_sur_une_page_vide(self, scraper):
        scraper.reponses["/"] = [page_liste(ligne("A", "T")), PAGE_VIDE]
        run(scraper.scrape_by_date_range("2020-01-01", "2020-12-31"))
        assert pages_demandees(scraper) == [1, 2]

    def test_les_pages_2_et_suivantes_sont_des_TR_NUS(self, scraper):
        """Le cas qui a tronqué le balayage complet.

        Sans `<thead>` ni `<tbody>`, un sélecteur `tbody tr` ne trouve rien et la
        collecte s'arrête à la page 1. L'ordre des colonnes vient de la page 1 et
        doit être transmis aux suivantes.
        """
        scraper.reponses["/"] = [
            page_liste(ligne("A", "T", tid=1)),
            page_suite(ligne("B", "U", tid=2), ligne("C", "V", tid=3)),
            PAGE_VIDE,
        ]
        lignes = run(scraper.scrape_by_date_range("2020-01-01", "2020-12-31"))
        assert [r["title"] for r in lignes] == ["T", "U", "V"]
        assert pages_demandees(scraper) == [1, 2, 3]

    def test_le_plafond_de_pagination_est_signale(self, scraper, monkeypatch):
        """Un plafond atteint n'est pas la normale : il vaut `parse`, pas silence."""
        monkeypatch.setattr("src.scrapers.bpi_scraper.BPI_MAX_PAGES", 3)
        scraper.reponses["/"] = [page_liste(ligne("A", "T"))]  # ne se tarit jamais
        lignes = run(scraper.scrape_by_date_range("2020-01-01", "2020-12-31"))
        assert len(lignes) == 3
        assert pages_demandees(scraper) == [1, 2, 3]


# ── Les paliers ───────────────────────────────────────────────────────────────
class TestPaliers:
    def test_un_silver_ne_coute_aucune_requete_de_detail(self, scraper):
        """Silver est le plancher du barème : rien à raconter de plus.

        C'est cette économie qui rend le balayage complet praticable.
        """
        scraper.reponses["/"] = [page_liste(ligne("A", "T", niveau="Silver")), PAGE_VIDE]
        run(scraper.scrape_by_date_range("2020-01-01", "2020-12-31", get_details=True))
        assert [c for c, _, _ in scraper.appels if c.startswith("/format/")] == []

    def test_un_multi_platine_est_eclate_en_un_palier_par_ligne(self, scraper):
        """La dédup a le niveau dans sa clé : les paliers sont donc préservés."""
        scraper.reponses["/"] = [
            page_liste(ligne("SIGALA", "EASY LOVE", niveau="2x Platinum", tid=16392)),
            PAGE_VIDE,
        ]
        scraper.reponses["/format/2/artist/1/title/16392"] = [
            page_detail(
                ("28 August 2026", "2x Platinum"),
                ("24 June 2016", "Platinum"),
                ("04 December 2015", "Gold"),
            )
        ]
        lignes = run(scraper.scrape_by_date_range("2020-01-01", "2030-12-31", get_details=True))
        assert [(x["certification_level"], x["certification_date"]) for x in lignes] == [
            ("2x Platinum", "2026-08-28"),
            ("Platinum", "2016-06-24"),
            ("Gold", "2015-12-04"),
        ]
        assert all(x["artist"] == "SIGALA" and x["title"] == "EASY LOVE" for x in lignes)

    def test_un_historique_illisible_conserve_le_palier_courant(self, scraper):
        """Perdre la ligne serait pire que de n'avoir qu'un palier."""
        scraper.reponses["/"] = [page_liste(ligne("A", "T", niveau="Gold")), PAGE_VIDE]
        scraper.reponses["/format/2/artist/1/title/1"] = ["<div>gabarit inconnu</div>"]
        lignes = run(scraper.scrape_by_date_range("2020-01-01", "2020-12-31", get_details=True))
        assert len(lignes) == 1
        assert lignes[0]["certification_level"] == "Gold"


# ── Reprise et vidage (balayage complet) ──────────────────────────────────────
class TestReprise:
    """Un balayage de quatre heures doit survivre à une coupure.

    Deux mécanismes, tous deux nécessaires : ne pas redemander les détails déjà
    connus (sinon reprendre coûte aussi cher que recommencer), et écrire en cours
    de route (sinon une coupure à la troisième heure perd les trois heures).
    """

    def test_un_palier_deja_connu_ne_coute_aucune_requete(self, scraper):
        scraper.reponses["/"] = [page_liste(ligne("A", "T", niveau="Gold", tid=7)), PAGE_VIDE]
        lignes = run(scraper.scrape_all(get_details=True, deja_connu=lambda r: r["title_id"] == 7))
        assert [c for c, _, _ in scraper.appels if c.startswith("/format/")] == []
        assert len(lignes) == 1

    def test_un_titre_REHAUSSE_est_bien_redemande(self, scraper):
        """Le palier fait partie de la clé de reprise : un titre passé de
        Platinum à 2x Platinum a un palier de plus à raconter."""
        scraper.reponses["/"] = [
            page_liste(ligne("A", "T", niveau="2x Platinum", tid=7)),
            PAGE_VIDE,
        ]
        scraper.reponses["/format/2/artist/1/title/7"] = [
            page_detail(("28 August 2026", "2x Platinum"), ("24 June 2016", "Platinum"))
        ]
        connus = {("2", "1", "7", "PLATINUM", "2016-06-24")}

        def deja_connu(r):
            cle = (
                str(r["format_id"]),
                str(r["artist_id"]),
                str(r["title_id"]),
                r["certification_level"].upper(),
                r["certification_date"],
            )
            return cle in connus

        lignes = run(scraper.scrape_all(get_details=True, deja_connu=deja_connu))
        assert [c for c, _, _ in scraper.appels if c.startswith("/format/")] == [
            "/format/2/artist/1/title/7"
        ]
        assert len(lignes) == 2

    def test_le_vidage_ecrit_en_cours_de_route(self, scraper, monkeypatch):
        monkeypatch.setattr("src.scrapers.bpi_scraper._LOT_DE_VIDAGE", 2)
        scraper.reponses["/"] = [
            page_liste(*[ligne(f"A{i}", f"T{i}", tid=i) for i in range(5)]),
            PAGE_VIDE,
        ]
        lots = []
        reste = run(scraper.scrape_all(get_details=True, vidage=lots.append))
        # 5 titres, vidage tous les 2 → deux lots de 2, et 1 qui reste à l'appelant
        assert [len(x) for x in lots] == [2, 2]
        assert len(reste) == 1
        assert len([r for lot in lots for r in lot]) + len(reste) == 5


# ── La requête ────────────────────────────────────────────────────────────────
class TestRequete:
    def test_la_vue_liste_et_le_skin_sont_constants(self, scraper):
        scraper.reponses["/"] = [PAGE_VIDE]
        run(scraper.scrape_by_date_range("2020-01-01", "2020-01-31"))
        _, params, hx = scraper.appels[0]
        assert params["view"] == "list" and params["skin"] == "bpi"
        assert hx is True, "sans l'en-tête HX-Request le site rend une coquille vide"

    def test_les_ids_d_artiste_partent_ensemble(self, scraper):
        """Le filtre `artists` est cumulatif : une seule requête pour N entités."""
        scraper.reponses["/artists"] = [page_annuaire((1, "SIGALA"), (2, "SIGALA & X")), ""]
        scraper.reponses["/"] = [PAGE_VIDE]
        run(scraper.scrape_by_artist("Sigala"))
        requetes = [p for c, p, _ in scraper.appels if c == "/"]
        assert len(requetes) == 1
        assert requetes[0]["artists"] == [1, 2]

    def test_le_balayage_complet_ne_trie_pas_par_date(self, scraper):
        """Une re-certification déplace une ligne vers la page 1 en cours de
        balayage : trier par date ferait manquer ce qu'elle a poussé."""
        scraper.reponses["/"] = [PAGE_VIDE]
        run(scraper.scrape_all())
        assert scraper.appels[0][1]["sort"] == "artist.name asc"
