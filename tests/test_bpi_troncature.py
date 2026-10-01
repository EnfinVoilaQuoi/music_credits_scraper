"""BPI : une collecte tronquée par le plafond ne doit pas s'annoncer terminée.

Audit des quatre organismes, 2026-09-09. RIAA venait d'être corrigé du même
défaut ; BPI en portait la variante silencieuse.

`_collecter` paginait jusqu'à `BPI_MAX_PAGES` puis, la clause `else` atteinte,
écrivait un `logger.error` et posait `obs.fail(PARSE, …)` — **mais rendait ses
lignes comme n'importe quelle collecte réussie**. Le scraper étant créé PUIS
FERMÉ à l'intérieur de `_collecte`, son constat mourait avec lui : `full_sweep`
imprimait « ✅ Balayage terminé » sur un corpus coupé, et `main()` rendait 0.

Le corollaire, lui, ne relevait d'aucun oubli d'ergonomie : la boucle jumelle de
l'annuaire (`/artists?q=`) n'avait **ni log, ni verdict, ni drapeau**. Un
plafond inatteignable en pratique n'en est pas moins muet le jour où le site
change de pagination — et c'est le propre de ce défaut de ne se voir qu'après.
"""

import pytest

import src.utils.update_bpi as u


@pytest.fixture
def bpi_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "_BPI_DIR", tmp_path)
    monkeypatch.setattr(u, "CERTIF_CSV", tmp_path / "certif_bpi.csv")
    monkeypatch.setattr(u, "BPI_RAW", tmp_path / "bpi_raw.csv")
    monkeypatch.setattr(u, "BPI_META", tmp_path / "metadata.json")
    return tmp_path


def _ligne(titre="UN TITRE"):
    return {
        "artist": "UN ARTISTE",
        "title": titre,
        "certification_level": "Gold",
        "certification_date": "2020-01-01",
        "format": "SINGLE",
        "detail_url": f"/format/1/artist/1/title/{titre}",
    }


class _FauxScraper:
    """Rend des lignes, et dit s'il a buté sur le plafond."""

    def __init__(self, lignes, tronque):
        self.lignes = lignes
        self.tronque = tronque

    async def scrape_by_date_range(self, *a, **k):
        return list(self.lignes)

    async def scrape_all(self, *a, vidage=None, **k):
        return list(self.lignes)

    async def scrape_by_artist(self, *a, **k):
        return list(self.lignes)

    async def aclose(self):
        pass


@pytest.fixture
def poser_scraper(monkeypatch):
    """Court-circuite la boucle asyncio : `_collecte` n'est pas ce qu'on teste."""

    def _install(lignes, tronque):
        faux = _FauxScraper(lignes, tronque)

        def _fausse_collecte(travail):
            import asyncio

            return u.Collecte(asyncio.run(travail(faux)), faux.tronque)

        monkeypatch.setattr(u, "_collecte", _fausse_collecte)
        return faux

    return _install


class TestUneCollecteTronqueeNeSeDitPasTerminee:
    def test_full_sweep_rend_False(self, bpi_tmp, poser_scraper, capsys):
        poser_scraper([_ligne()], tronque=True)

        assert u.full_sweep(get_details=False) is False
        sortie = capsys.readouterr().out
        assert "TRONQUÉ" in sortie
        assert "Balayage terminé" not in sortie, "un corpus coupé annoncé comme terminé"

    def test_fetch_periode_rend_False(self, bpi_tmp, poser_scraper, capsys):
        poser_scraper([_ligne()], tronque=True)

        assert u.fetch_periode("2020-01-01", "2020-12-31") is False
        assert "TRONQUÉ" in capsys.readouterr().out

    def test_fetch_artists_rend_False(self, bpi_tmp, poser_scraper, capsys):
        poser_scraper([_ligne()], tronque=True)

        assert u.fetch_artists(["UN ARTISTE"]) is False
        assert "TRONQUÉ" in capsys.readouterr().out

    def test_les_lignes_vues_sont_QUAND_MEME_gardees(self, bpi_tmp, poser_scraper):
        """Tronqué ne veut pas dire jetable : ce qui a été lu est bon.

        C'est la même règle que sur RIAA — on garde, on le DIT, on ne prétend
        pas avoir fini.
        """
        poser_scraper([_ligne("A"), _ligne("B")], tronque=True)

        u.fetch_periode("2020-01-01", "2020-12-31")

        assert u.CERTIF_CSV.exists()
        assert len(u._load_bpi_raw()) == 2


class _ScraperParNom:
    """Rend, par nom, des lignes, une absence constatée, un échec ou une exception."""

    tronque = False

    def __init__(self, reponses):
        self.reponses = reponses
        self.sans_resultat = self.lecture_echouee = False
        self.motif = ""

    async def scrape_by_artist(self, nom, **k):
        r = self.reponses[nom]
        if isinstance(r, Exception):
            raise r
        self.sans_resultat = r == "absent"
        self.lecture_echouee = r == "echec"
        motifs = {"absent": "absent de l'annuaire", "echec": "annuaire illisible"}
        self.motif = motifs.get(r, "") if isinstance(r, str) else ""
        return r if isinstance(r, list) else []

    async def aclose(self):
        pass


class TestRechercheParArtiste:
    """Absence ≠ échec (2026-10-01) : un rappeur jamais certifié au Royaume-Uni
    sortait en « ❌ Aucune certification BPI trouvée », code 1."""

    @pytest.fixture
    def lancer(self, bpi_tmp, monkeypatch):
        import asyncio

        def _lancer(reponses, noms):
            faux = _ScraperParNom(reponses)
            monkeypatch.setattr(
                u, "_collecte", lambda travail: u.Collecte(asyncio.run(travail(faux)), False)
            )
            return u.fetch_artists(noms)

        return _lancer

    def test_une_absence_constatee_est_un_succes(self, lancer, capsys):
        assert lancer({"Lucio Bukowski": "absent"}, ["Lucio Bukowski"]) is True
        derniere = capsys.readouterr().out.strip().splitlines()[-1]
        assert derniere.startswith("∅ Lucio Bukowski : aucune certification")

    def test_un_annuaire_illisible_est_un_echec(self, lancer, capsys):
        assert lancer({"X": "echec"}, ["X"]) is False
        assert "❌ X : annuaire illisible" in capsys.readouterr().out

    def test_une_erreur_reseau_n_emporte_pas_les_noms_suivants(self, lancer, capsys):
        import httpx

        reponses = {"A": httpx.ConnectError("coupé"), "B": [_ligne("B")]}
        assert lancer(reponses, ["A", "B"]) is False
        derniere = capsys.readouterr().out.strip().splitlines()[-1]
        assert "❌ A : réseau : ConnectError" in derniere and "✅ B : 1" in derniere
        assert len(u._load_bpi_raw()) == 1, "ce que B a rendu est gardé"


class TestUneCollecteEntiereReste_un_succes:
    """Le pendant : sans plafond atteint, rien ne change."""

    def test_full_sweep_rend_True(self, bpi_tmp, poser_scraper, capsys):
        poser_scraper([_ligne()], tronque=False)

        assert u.full_sweep(get_details=False) is True
        sortie = capsys.readouterr().out
        assert "Balayage terminé" in sortie
        assert "TRONQUÉ" not in sortie

    def test_fetch_periode_rend_True(self, bpi_tmp, poser_scraper):
        poser_scraper([_ligne()], tronque=False)
        assert u.fetch_periode("2020-01-01", "2020-12-31") is True
