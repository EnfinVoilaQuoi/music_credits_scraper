"""Gardes-fous d'EXÉCUTION de BRMA (posés le 2026-09-07).

BRMA avait un filet de fixture sur son parseur de production, mais rien qui
parle EN PRODUCTION : une page devenue illisible se lisait « 0 certifications,
0 erreurs », la fraîcheur s'horodatait quand même, et `main()` sortait en 0 en
toutes circonstances. La GUI ne pouvait donc pas savoir qu'un run avait échoué.

Le piège central, et la raison pour laquelle ces gardes-fous ne sont pas la copie
de ceux de la RIAA : chez Ultratop, **« aucune nouveauté » est le cas NORMAL**.
`extract_certifications` ne rend que ce qui manque à la base, et entre deux runs
il ne manque presque jamais rien. Le signal de santé ne peut donc pas être le
nombre de nouveautés — c'est le nombre de LIGNES LUES.
"""

import logging

import pytest
from bs4 import BeautifulSoup

import src.utils.update_brma as m
from src.utils.update_brma import UltratopUpdater, lignes_de_page, verdict_page
from tests.conftest import load_fixture

FIXTURE = "brma/ultratop_2021_singles.html"


def _ligne_html(artiste="A", titre="T", certifs="01/01/2021: Or"):
    return (
        '<div style="display:table-row">'
        f'<div class="chart_title"><a href="/fr/song/x">{artiste}<br>{titre}</a></div>'
        f'<div class="company">{certifs}</div>'
        "</div>"
    )


def _page(*lignes):
    return BeautifulSoup(f"<div>{''.join(lignes)}</div>", "html.parser")


@pytest.fixture(autouse=True)
def _sans_llm(monkeypatch):
    """Aucun test ne parle à Ollama.

    Sans ça, le verdict `parse` déclenche le repli LLM, qui tente de joindre le
    service local : 34 s de timeout sur une machine sans Ollama, et une VRAIE
    inférence sur une machine où il tourne. Un test qui sort de la machine n'est
    plus un test unitaire — même règle que « aucun test ne lit `data/` réel ».
    """
    monkeypatch.setattr("src.utils.llm_extractor.get_shared_extractor", lambda *a, **k: None)


class FauxLLM:
    """Extracteur simulé : rend le JSON qu'on lui donne, sans réseau."""

    def __init__(self, reponse):
        self.reponse = reponse
        self.prompts = []

    def extract_json(self, prompt, max_tokens=512):
        self.prompts.append(prompt)
        return self.reponse


@pytest.fixture
def updater(tmp_path):
    return UltratopUpdater(
        database_path=str(tmp_path / "certif_brma.csv"), output_dir=str(tmp_path)
    )


# ── Le verdict, fonction pure ─────────────────────────────────────────────────
class TestVerdictPage:
    def test_une_page_lue_sans_nouveaute_est_SAINE(self):
        """LE cas à ne pas confondre avec une panne.

        Ultratop rend les mêmes certifications à chaque run ; « 0 nouvelle » est
        donc la normale absolue et ne doit JAMAIS valoir échec.
        """
        bilan = {"candidates": 103, "certifs_lues": 116, "nouvelles": 0}
        assert verdict_page(bilan, annee_revolue=True) == ("", "")

    def test_aucune_ligne_sur_une_annee_revolue_est_une_PANNE(self):
        """Toute année d'Ultratop depuis 1995 contient des certifications."""
        kind, detail = verdict_page({"candidates": 0, "certifs_lues": 0}, annee_revolue=True)
        assert kind == "parse"
        assert "révolue" in detail

    def test_aucune_ligne_sur_l_annee_en_cours_est_plausible(self):
        """En janvier, la page de l'année courante peut légitimement être vide."""
        kind, _ = verdict_page({"candidates": 0, "certifs_lues": 0}, annee_revolue=False)
        assert kind == "absent"

    def test_des_lignes_mais_aucune_certification_lue_est_une_PANNE(self):
        kind, detail = verdict_page({"candidates": 103, "certifs_lues": 0}, annee_revolue=False)
        assert kind == "parse"
        assert "103" in detail


# ── Les deux voies de sélection ───────────────────────────────────────────────
class TestSelecteurEtRepli:
    def test_la_voie_primaire_reste_le_style_en_ligne(self):
        soup = _page(_ligne_html())
        lignes, voie = lignes_de_page(soup)
        assert voie == "style"
        assert len(lignes) == 1

    def test_le_repli_semantique_prend_le_relais(self):
        """Le jour où Ultratop déplace `display:table-row` vers une classe."""
        html = (
            '<div class="row">'
            '<div class="chart_title"><a href="/x">A<br>T</a></div>'
            '<div class="company">01/01/2021: Or</div>'
            "</div>"
        )
        lignes, voie = lignes_de_page(BeautifulSoup(html, "html.parser"))
        assert voie == "semantique"
        assert len(lignes) == 1

    def test_une_page_sans_rien_rend_zero_ligne(self):
        lignes, _ = lignes_de_page(BeautifulSoup("<div>rien</div>", "html.parser"))
        assert lignes == []

    def test_les_deux_voies_rendent_les_MEMES_certifications(self, updater, monkeypatch):
        """L'ORACLE : la voie de repli est vérifiée contre la voie qui tourne.

        Un repli qu'on n'a pas comparé à l'existant est un repli qui produira
        silencieusement autre chose le jour où il servira. Mesuré sur la page
        réelle : 116 certifications des deux côtés, et la voie sémantique ne
        visite que 103 conteneurs au lieu de 207 — les 104 autres étaient
        écartées par un `continue` muet.
        """
        html = load_fixture(FIXTURE)

        def extraire(forcer_repli):
            u = UltratopUpdater.__new__(UltratopUpdater)
            u.existing_keys = set()
            u.logger = logging.getLogger("test")
            if forcer_repli:
                vraie = m.lignes_de_page
                monkeypatch.setattr(m, "lignes_de_page", lambda s: (_semantique(s), "semantique"))
            bilan = {}
            certs = u.extract_certifications(
                BeautifulSoup(html, "html.parser"), 2021, "singles", bilan
            )
            if forcer_repli:
                monkeypatch.setattr(m, "lignes_de_page", vraie)
            return certs, bilan

        def _semantique(soup):
            out = []
            for titre in soup.select("div.chart_title"):
                noeud = titre.parent
                while noeud is not None:
                    if noeud.find("div", class_="company"):
                        out.append(noeud)
                        break
                    noeud = noeud.parent
            return out

        cle = lambda c: (  # noqa: E731
            c["artist"],
            c["title"],
            c["certification_level"],
            c["certification_date"],
        )
        par_style, bilan_style = extraire(False)
        par_semantique, bilan_sem = extraire(True)

        assert [cle(c) for c in par_style] == [cle(c) for c in par_semantique]
        assert bilan_style["certifs_lues"] == bilan_sem["certifs_lues"] == 116
        assert bilan_style["candidates"] == 207
        assert bilan_sem["candidates"] == 103


# ── Le bilan de page ──────────────────────────────────────────────────────────
class TestBilan:
    def test_le_bilan_distingue_LUES_et_NOUVELLES(self, updater):
        """Sans cette distinction, le code ne pouvait rien conclure d'une page."""
        soup = _page(_ligne_html())
        bilan = {}
        updater.extract_certifications(soup, 2021, "singles", bilan)
        assert bilan["certifs_lues"] == 1
        assert bilan["nouvelles"] == 1

        # Deuxième passage : la certif est connue, donc 0 nouvelle… mais 1 LUE.
        bilan2 = {}
        assert updater.extract_certifications(soup, 2021, "singles", bilan2) == []
        assert bilan2["certifs_lues"] == 1
        assert bilan2["nouvelles"] == 0
        assert verdict_page(bilan2, annee_revolue=True) == ("", "")

    def test_les_candidates_ignorees_sont_COMPTEES(self, updater):
        """Elles partaient dans un `continue` muet — 104 sur la page réelle."""
        html = '<div style="display:table-row"><div class="autre">x</div></div>'
        bilan = {}
        updater.extract_certifications(BeautifulSoup(html, "html.parser"), 2021, "singles", bilan)
        assert bilan["candidates"] == 1
        assert bilan["candidates_ignorees"] == 1
        assert bilan["certifs_lues"] == 0


# ── La lecture d'une page, avec observation ───────────────────────────────────
class TestLirePage:
    def test_une_page_saine_compte_comme_lue(self, updater, monkeypatch):
        monkeypatch.setattr(updater, "fetch_page", lambda y, c: _page(_ligne_html()))
        certs = updater._lire_page(2021, "singles")
        assert len(certs) == 1
        assert updater.bilan_run == {
            "demandees": 1,
            "lues": 1,
            "muettes": 0,
            "echouees": 0,
            "repli": False,
        }

    def test_une_page_inaccessible_est_comptee_comme_echec(self, updater, monkeypatch):
        monkeypatch.setattr(updater, "fetch_page", lambda y, c: None)
        assert updater._lire_page(2021, "singles") == []
        assert updater.bilan_run["echouees"] == 1
        assert updater.bilan_run["lues"] == 0

    def test_une_page_illisible_est_MUETTE_et_non_vide(self, updater, monkeypatch):
        """Le cas qu'Ultratop produira le jour d'une refonte."""
        monkeypatch.setattr(updater, "fetch_page", lambda y, c: _page())
        assert updater._lire_page(2020, "singles") == []
        assert updater.bilan_run["muettes"] == 1
        assert updater.bilan_run["lues"] == 0

    def test_l_usage_du_repli_est_SIGNALE(self, updater, monkeypatch):
        html = (
            '<div class="row">'
            '<div class="chart_title"><a href="/x">A<br>T</a></div>'
            '<div class="company">01/01/2021: Or</div>'
            "</div>"
        )
        monkeypatch.setattr(updater, "fetch_page", lambda y, c: BeautifulSoup(html, "html.parser"))
        updater._lire_page(2021, "singles")
        assert updater.bilan_run["repli"] is True


# ── La fraîcheur ──────────────────────────────────────────────────────────────
class TestFraicheur:
    def test_sans_scrape_le_comportement_d_origine_est_INTACT(self, updater):
        """Appel direct (ce que font les tests existants) : on horodate.

        La garde ne doit mordre que sur un scrape réellement tenté — sinon elle
        casserait la décision, délibérée, d'horodater la dernière VÉRIFICATION.
        """
        updater.save_updated_database([_cert()])
        (updater.output_dir / "metadata.json").unlink()
        updater.save_updated_database([])
        assert (updater.output_dir / "metadata.json").exists()

    def test_un_scrape_qui_n_a_RIEN_LU_n_horodate_pas(self, updater, monkeypatch):
        """Le défaut fermé : une panne totale se lisait comme une vérification."""
        updater.save_updated_database([_cert()])
        (updater.output_dir / "metadata.json").unlink()

        monkeypatch.setattr(updater, "fetch_page", lambda y, c: None)
        updater._demarrer_run()
        updater._lire_page(2020, "singles")
        updater.save_updated_database([])

        assert not (updater.output_dir / "metadata.json").exists()

    def test_un_scrape_LU_mais_sans_nouveaute_horodate_bien(self, updater, monkeypatch):
        """Le cas normal d'Ultratop : tout va bien, rien de neuf."""
        updater.save_updated_database([_cert()])
        (updater.output_dir / "metadata.json").unlink()

        monkeypatch.setattr(updater, "fetch_page", lambda y, c: _page(_ligne_html()))
        updater._demarrer_run()
        updater._lire_page(2021, "singles")  # lue
        updater._lire_page(2021, "singles")  # relue : 0 nouvelle
        updater.save_updated_database([])

        assert (updater.output_dir / "metadata.json").exists()


# ── Le verdict de run ─────────────────────────────────────────────────────────
class TestRunReussi:
    def test_aucune_page_lue_vaut_ECHEC(self, updater, monkeypatch):
        monkeypatch.setattr(updater, "fetch_page", lambda y, c: None)
        monkeypatch.setattr(updater, "retry_missing_pages", lambda: [])
        monkeypatch.setattr(updater, "random_delay", lambda: None)
        assert updater.run_manual_update(years_back=0) is False

    def test_une_page_lue_suffit(self, updater, monkeypatch):
        monkeypatch.setattr(updater, "fetch_page", lambda y, c: _page(_ligne_html()))
        monkeypatch.setattr(updater, "retry_missing_pages", lambda: [])
        monkeypatch.setattr(updater, "random_delay", lambda: None)
        assert updater.run_manual_update(years_back=0) is True

    def test_sans_page_demandee_le_run_n_est_pas_declare_en_echec(self, updater, monkeypatch):
        """Un `--dedup` ou un run sans scrape ne doit pas sortir en erreur."""
        monkeypatch.setattr(updater, "retry_missing_pages", lambda: [])
        monkeypatch.setattr(updater, "update_recent_years", lambda y: [])
        assert updater.run_manual_update(years_back=0) is True


def _cert(artist="A", title="T", level="Or", date="2021-01-01", category="singles"):
    return {
        "artist": artist,
        "title": title,
        "category": category,
        "certification_level": level,
        "certification_date": date,
        "year_page": 2021,
        "detail_url": "https://www.ultratop.be/x",
        "scraped_at": "2026-09-07 00:00:00",
    }


class TestCodeDeSortie:
    """Ce que la GUI lit pour juger d'un run — et qui valait 0 en toutes
    circonstances, y compris scrape entièrement bloqué."""

    def _lancer(self, monkeypatch, tmp_path, reussi):
        monkeypatch.setattr(
            m.UltratopUpdater, "run_manual_update", lambda self, years_back=2: reussi
        )
        monkeypatch.setattr(
            "sys.argv",
            [
                "update_brma.py",
                "--mode",
                "once",
                "--database",
                str(tmp_path / "c.csv"),
                "--output-dir",
                str(tmp_path),
            ],
        )
        with pytest.raises(SystemExit) as sortie:
            m.main()
        return sortie.value.code

    def test_un_run_reussi_sort_en_0(self, monkeypatch, tmp_path):
        assert self._lancer(monkeypatch, tmp_path, True) == 0

    def test_un_run_echoue_sort_en_1(self, monkeypatch, tmp_path):
        """Sans ça, la GUI annonce « ✅ Mise à jour BRMA réussie » sur une panne."""
        assert self._lancer(monkeypatch, tmp_path, False) == 1


# ── Le repli LLM (famille A : les sélecteurs sont cassés) ─────────────────────
class TestReplitLLM:
    """Récupéré de `scraper_brma`, où il était orphelin.

    BRMA est la SEULE source de certifs où ce repli a un sens, et c'est mesuré :
    le texte d'une ligne Ultratop porte tout (artiste, titre, date, palier). Chez
    la RIAA le palier n'est pas dans le texte mais dans l'`alt` du badge ; chez
    BPI l'identité est dans `hx-get`. Les y porter ferait inventer une donnée ou
    fabriquer des doublons.
    """

    PAGE_CASSEE = '<div class="autre">24kGoldn feat. Iann Dior Mood 26/03/2021 2x Platine</div>'

    def _avec_llm(self, monkeypatch, reponse):
        faux = FauxLLM(reponse)
        monkeypatch.setattr("src.utils.llm_extractor.get_shared_extractor", lambda *a, **k: faux)
        return faux

    def _certif(self, **kw):
        base = {
            "artist": "24kGoldn feat. Iann Dior",
            "title": "Mood",
            "certification": "2x Platine",
            "date": "2021-03-26",
        }
        return {**base, **kw}

    def test_il_sauve_ce_que_les_selecteurs_ont_perdu(self, updater, monkeypatch):
        self._avec_llm(monkeypatch, {"certifications": [self._certif()]})
        soup = BeautifulSoup(self.PAGE_CASSEE, "html.parser")
        certs = updater._extract_with_llm(soup, 2021, "singles")
        assert len(certs) == 1
        assert certs[0]["certification_level"] == "2x Platine"
        assert certs[0]["certification_date"] == "2021-03-26"

    def test_garde_1_un_artiste_absent_de_la_page_est_rejete(self, updater, monkeypatch):
        """L'anti-hallucination d'origine : le LLM ne peut pas inventer un nom."""
        self._avec_llm(monkeypatch, {"certifications": [self._certif(artist="Johnny Hallyday")]})
        soup = BeautifulSoup(self.PAGE_CASSEE, "html.parser")
        assert updater._extract_with_llm(soup, 2021, "singles") == []

    def test_garde_2_un_palier_hors_vocabulaire_est_rejete(self, updater, monkeypatch):
        """Garde AJOUTÉE : on écrit dans un magasin de certifications.

        Le référentiel est demandé à `brma_validator`, jamais recopié — deux
        outils qui parlent du même fichier ne peuvent pas avoir chacun le leur.
        """
        self._avec_llm(monkeypatch, {"certifications": [self._certif(certification="Bronze")]})
        soup = BeautifulSoup(self.PAGE_CASSEE, "html.parser")
        assert updater._extract_with_llm(soup, 2021, "singles") == []

    @pytest.mark.parametrize("niveau", ["Or", "Platine", "Diamant", "2x Platine", "3x Or"])
    def test_garde_2_accepte_le_vrai_vocabulaire_belge(self, updater, monkeypatch, niveau):
        self._avec_llm(monkeypatch, {"certifications": [self._certif(certification=niveau)]})
        soup = BeautifulSoup(self.PAGE_CASSEE, "html.parser")
        assert len(updater._extract_with_llm(soup, 2021, "singles")) == 1

    def test_garde_3_une_date_hors_de_l_annee_de_la_page_est_rejetee(self, updater, monkeypatch):
        """Garde AJOUTÉE : une certif de 2019 sur la page 2021 est inventée."""
        self._avec_llm(monkeypatch, {"certifications": [self._certif(date="2019-03-26")]})
        soup = BeautifulSoup(self.PAGE_CASSEE, "html.parser")
        assert updater._extract_with_llm(soup, 2021, "singles") == []

    def test_garde_3_une_date_informe_est_rejetee(self, updater, monkeypatch):
        self._avec_llm(monkeypatch, {"certifications": [self._certif(date="26/03/2021")]})
        soup = BeautifulSoup(self.PAGE_CASSEE, "html.parser")
        assert updater._extract_with_llm(soup, 2021, "singles") == []

    def test_une_reponse_llm_informe_ne_casse_rien(self, updater, monkeypatch):
        self._avec_llm(monkeypatch, {"certifications": "pas une liste"})
        soup = BeautifulSoup(self.PAGE_CASSEE, "html.parser")
        assert updater._extract_with_llm(soup, 2021, "singles") == []

    def test_le_sauvetage_ne_rend_PAS_la_page_saine(self, updater, monkeypatch):
        """LE point de conception : signaler ET sauver, jamais sauver AU LIEU DE.

        Si le sauvetage verdissait la page, un LLM tiendrait lieu de parseur à
        notre insu et le site resterait cassé indéfiniment.
        """
        self._avec_llm(monkeypatch, {"certifications": [self._certif()]})
        monkeypatch.setattr(
            updater, "fetch_page", lambda y, c: BeautifulSoup(self.PAGE_CASSEE, "html.parser")
        )
        certs = updater._lire_page(2021, "singles")

        assert len(certs) == 1, "le sauvetage a bien eu lieu"
        assert updater.bilan_run["muettes"] == 1, "la page reste comptée MUETTE"
        assert updater.bilan_run["lues"] == 0, "elle ne compte pas comme lue"

    def test_sans_ollama_le_repli_est_un_no_op(self, updater, monkeypatch):
        """Ollama absent ne doit jamais faire échouer un scrape."""
        soup = BeautifulSoup(self.PAGE_CASSEE, "html.parser")
        assert updater._extract_with_llm(soup, 2021, "singles") == []
