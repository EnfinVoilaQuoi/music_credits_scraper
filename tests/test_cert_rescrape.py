"""Des trous signalés vers les commandes qui les comblent (`cert_rescrape`).

Les validateurs savaient DIRE quels mois manquent ; rien ne savait aller les
chercher. Le chaînon est une traduction, et les trois sources ne se visent pas à
la même maille — c'est le site qui l'impose, pas nous.

Module PUR : il construit des commandes sans rien lancer, donc ces tests
vérifient la traduction sans une seule requête.
"""

from datetime import date

import pytest

from src.utils.cert_rescrape import annees, commandes, resume


class TestAnnees:
    def test_annees_distinctes_et_triees(self):
        assert annees(["2015-02", "2006-01", "2006-03"]) == [2006, 2015]

    @pytest.mark.parametrize("bruit", [[], ["n'importe quoi"], ["--01"], [""]])
    def test_entrees_illisibles_ignorees(self, bruit):
        assert annees(bruit) == []


class TestSnep:
    """L'export SNEP est ANNUEL : on recharge l'année entière."""

    def test_une_seule_invocation_repetable(self):
        cmds = commandes("SNEP", ["2006-01", "2006-03", "2015-02"], "u.py", "py")

        assert cmds == [["py", "u.py", "--year", "2006", "--year", "2015"]]

    def test_resume_annonce_les_annees(self):
        assert "2006, 2015" in resume("SNEP", ["2006-01", "2015-02"])


class TestRiaa:
    """La recherche RIAA prend deux dates : on vise le MOIS exact."""

    def test_un_mois_par_commande_borne_haute_exclusive(self):
        cmds = commandes("RIAA", ["1962-04"], "u.py", "py")

        assert cmds == [["py", "u.py", "--from", "01-04-1962", "--to", "01-05-1962"]]

    def test_decembre_bascule_sur_lannee_suivante(self):
        cmds = commandes("RIAA", ["2020-12"], "u.py", "py")

        assert cmds[0][-1] == "01-01-2021"

    def test_les_mois_sont_dedoublonnes_et_ordonnes(self):
        cmds = commandes("RIAA", ["1962-05", "1962-04", "1962-04"], "u.py", "py")

        assert [c[3] for c in cmds] == ["01-04-1962", "01-05-1962"]

    @pytest.mark.parametrize("invalide", ["1962-13", "1962-00", "1962"])
    def test_un_mois_hors_bornes_est_ecarte(self, invalide):
        assert commandes("RIAA", [invalide], "u.py", "py") == []


class TestBrma:
    """Ultratop n'expose pas d'année isolée : on remonte jusqu'à la plus ancienne."""

    def test_recul_calcule_depuis_la_plus_ancienne(self):
        cible = date.today().year - 3
        cmds = commandes("BRMA", [f"{cible}-05"], "u.py", "py")

        assert cmds == [["py", "u.py", "--mode", "once", "--years-back", "4"]]

    def test_le_resume_previent_du_rescrape_intermediaire(self):
        assert "ne cible pas une année" in resume("BRMA", [f"{date.today().year - 2}-01"])


class TestRienAFaire:
    @pytest.mark.parametrize("source", ["SNEP", "RIAA", "BRMA"])
    def test_sans_trou_aucune_commande(self, source):
        assert commandes(source, [], "u.py", "py") == []

    def test_source_inconnue(self):
        assert commandes("IFPI", ["2020-01"], "u.py", "py") == []
        assert resume("IFPI", ["2020-01"]) == "Source non ciblable."
