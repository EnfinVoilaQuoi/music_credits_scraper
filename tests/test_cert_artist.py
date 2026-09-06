"""Recherche de certifications par ARTISTE, sur plusieurs noms à la fois.

Un membre de groupe est crédité sous son nom ET sous celui de sa formation.
Chercher un seul des deux ampute la moitié de sa discographie certifiée — d'où
une liste de noms partout, et non un nom.

Ces tests portent sur la partie PURE : composer la liste et lire le magasin.
Aucune requête réseau n'y intervient (le magasin est injecté).
"""

import pytest

from src.utils.cert_artist import bilan_local, evolution, noms_de_recherche, resume_bilan


class MagasinFactice:
    """Tient lieu de `CertMatcher` : rend ce qu'on lui a dit, par nom."""

    def __init__(self, par_nom):
        self.par_nom = par_nom
        self.demandes = []

    def get_artist_certifications(self, nom):
        self.demandes.append(nom)
        return self.par_nom.get(nom, [])


def cert(body, title, certification):
    return {"body": body, "title": title, "certification": certification}


class TestNomsDeRecherche:
    def test_le_principal_vient_toujours_en_tete(self):
        assert noms_de_recherche("Shurik'N", ["IAM"]) == ["Shurik'N", "IAM"]

    def test_doublons_ecartes_sans_egard_a_la_casse_ni_aux_bords(self):
        assert noms_de_recherche("IAM", [" iam ", "IAm", "Akhenaton"]) == ["IAM", "Akhenaton"]

    def test_la_forme_conservee_est_la_premiere_vue(self):
        """C'est elle qui part dans l'URL : le SNEP cherche sur le libellé."""
        assert noms_de_recherche("iam", ["IAM"]) == ["iam"]

    @pytest.mark.parametrize("vide", ["", "   ", None])
    def test_les_entrees_vides_ne_font_pas_de_nom(self, vide):
        assert noms_de_recherche("Damso", [vide, "PNL"]) == ["Damso", "PNL"]

    def test_un_principal_vide_ne_condamne_pas_les_alias(self):
        assert noms_de_recherche("", ["IAM"]) == ["IAM"]

    def test_aucun_nom_du_tout(self):
        assert noms_de_recherche("  ", []) == []


class TestBilanLocal:
    def test_chaque_nom_est_demande_au_magasin(self):
        m = MagasinFactice({})
        bilan_local(["Shurik'N", "IAM"], matcher=m)
        assert m.demandes == ["Shurik'N", "IAM"]

    def test_les_corps_sont_comptes_separement(self):
        m = MagasinFactice(
            {"Angele": [cert("SNEP", "Balance ton quoi", "Or"), cert("BRMA", "Tout oublier", "Or")]}
        )
        bilan = bilan_local(["Angele"], matcher=m)
        assert bilan["SNEP"]["certifications"] == 1
        assert bilan["BRMA"]["certifications"] == 1

    def test_les_paliers_d_un_meme_titre_ne_font_qu_un_titre(self):
        """C'est la colonne qui dit si on a l'HISTORIQUE ou le seul sommet."""
        m = MagasinFactice({"X": [cert("SNEP", "Bang", "Or"), cert("SNEP", "Bang", "Platine")]})
        bilan = bilan_local(["X"], matcher=m)
        assert bilan["SNEP"] == {
            "certifications": 2,
            "titres": 1,
            "titres_multi_paliers": 1,
        }

    def test_un_titre_a_palier_unique_n_est_pas_compte_multi(self):
        m = MagasinFactice({"X": [cert("SNEP", "Bang", "Platine")]})
        assert bilan_local(["X"], matcher=m)["SNEP"]["titres_multi_paliers"] == 0

    def test_le_titre_est_rapproche_sans_egard_a_la_casse(self):
        m = MagasinFactice({"X": [cert("SNEP", "Bang", "Or"), cert("SNEP", "BANG", "Platine")]})
        assert bilan_local(["X"], matcher=m)["SNEP"]["titres"] == 1

    def test_solo_et_groupe_s_additionnent(self):
        m = MagasinFactice(
            {
                "Shurik'N": [cert("SNEP", "Samurai", "Or")],
                "IAM": [cert("SNEP", "Petit frere", "Or")],
            }
        )
        assert bilan_local(["Shurik'N", "IAM"], matcher=m)["SNEP"]["certifications"] == 2

    def test_magasin_muet(self):
        assert bilan_local(["Inconnu"], matcher=MagasinFactice({})) == {}


class TestResumeBilan:
    def test_un_bilan_vide_le_dit_franchement(self):
        assert "Aucune certification" in resume_bilan({})

    def test_l_absence_d_historique_est_nommee(self):
        m = MagasinFactice({"X": [cert("SNEP", "Bang", "Platine")]})
        assert "aucun historique de paliers" in resume_bilan(bilan_local(["X"], matcher=m))

    def test_l_historique_est_chiffre(self):
        m = MagasinFactice({"X": [cert("SNEP", "Bang", "Or"), cert("SNEP", "Bang", "Platine")]})
        assert "1 avec plusieurs paliers" in resume_bilan(bilan_local(["X"], matcher=m))


class TestEvolution:
    """Un total ne dit pas si le run a servi ; l'écart, lui, informe."""

    def test_sans_apport_le_dit(self):
        b = {"SNEP": {"certifications": 12, "titres": 12, "titres_multi_paliers": 0}}
        assert evolution(b, b) == "aucune certification nouvelle"

    def test_un_gain_est_signe(self):
        avant = {"SNEP": {"certifications": 12, "titres": 12, "titres_multi_paliers": 0}}
        apres = {"SNEP": {"certifications": 15, "titres": 14, "titres_multi_paliers": 1}}
        assert evolution(avant, apres) == "SNEP 12 → 15 (+3)"

    def test_un_corps_apparu_part_de_zero(self):
        apres = {"RIAA": {"certifications": 4, "titres": 4, "titres_multi_paliers": 0}}
        assert evolution({}, apres) == "RIAA 0 → 4 (+4)"
