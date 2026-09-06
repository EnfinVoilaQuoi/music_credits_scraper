"""Recherche de certifications par ARTISTE, sur plusieurs noms à la fois.

Un membre de groupe est crédité sous son nom ET sous celui de sa formation.
Chercher un seul des deux ampute la moitié de sa discographie certifiée — d'où
une liste de noms partout, et non un nom.

Ces tests portent sur la partie PURE : composer la liste et lire le magasin.
Aucune requête réseau n'y intervient (le magasin est injecté).
"""

import pytest

from src.utils.cert_artist import (
    MAX_TITRES_AFFICHES,
    _paliers,
    bilan_local,
    certifications,
    evolution,
    noms_de_recherche,
    nouveautes,
    recap,
    repartition,
    resume_bilan,
)


class MagasinFactice:
    """Tient lieu de `CertMatcher` : rend ce qu'on lui a dit, par nom."""

    def __init__(self, par_nom):
        self.par_nom = par_nom
        self.demandes = []

    def get_artist_certifications(self, nom):
        self.demandes.append(nom)
        return self.par_nom.get(nom, [])


def cert(body, title, certification, *, date="2020-01-01", artiste="X", categorie="single"):
    """Une certification au format de `cert_matcher._format`."""
    return {
        "body": body,
        "title": title,
        "certification": certification,
        "certification_date": date,
        "artist_name": artiste,
        "category": categorie,
    }


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


class TestDedoublonnage:
    """Une certification ne compte qu'UNE fois même si deux noms la ramènent.

    « IAM feat. Shurik'N » répond aux deux recherches. Sans dédoublonnage, le
    total d'un artiste enflerait précisément là où il collabore avec son groupe
    — l'endroit le plus intéressant du rapport serait le plus faux.
    """

    def test_la_ligne_commune_n_est_comptee_qu_une_fois(self):
        commune = cert("SNEP", "Petit frere", "Or", artiste="IAM FEAT. SHURIK'N")
        m = MagasinFactice({"Shurik'N": [commune], "IAM": [commune]})

        assert len(certifications(["Shurik'N", "IAM"], matcher=m)) == 1

    def test_elle_sait_de_quels_noms_elle_vient(self):
        commune = cert("SNEP", "Petit frere", "Or", artiste="IAM FEAT. SHURIK'N")
        m = MagasinFactice({"Shurik'N": [commune], "IAM": [commune]})

        [trouvee] = certifications(["Shurik'N", "IAM"], matcher=m)

        assert trouvee["noms_correspondants"] == ["Shurik'N", "IAM"]

    def test_le_bilan_ne_double_plus_les_lignes_communes(self):
        commune = cert("SNEP", "Petit frere", "Or", artiste="IAM FEAT. SHURIK'N")
        m = MagasinFactice({"Shurik'N": [commune], "IAM": [commune]})

        assert bilan_local(["Shurik'N", "IAM"], matcher=m)["SNEP"]["certifications"] == 1

    def test_deux_paliers_du_meme_titre_restent_deux_lignes(self):
        """Le niveau est dans la clé : dédoublonner ne doit pas manger l'échelle."""
        m = MagasinFactice(
            {
                "X": [
                    cert("SNEP", "Bang", "Or", date="2020-01-01"),
                    cert("SNEP", "Bang", "Platine", date="2021-01-01"),
                ]
            }
        )

        assert len(certifications(["X"], matcher=m)) == 2


class TestNouveautes:
    def test_ce_qui_n_etait_pas_la_avant(self):
        vieille = cert("SNEP", "Bang", "Or")
        neuve = cert("SNEP", "Bang", "Platine", date="2021-01-01")

        assert nouveautes([vieille], [vieille, neuve]) == [neuve]

    def test_sans_apport_la_liste_est_vide(self):
        c = cert("SNEP", "Bang", "Or")

        assert nouveautes([c], [c]) == []


class TestRepartition:
    def test_un_seul_nom_ne_merite_pas_de_repartition(self):
        assert repartition([cert("SNEP", "A", "Or")], ["Damso"]) == ""

    def test_chaque_nom_a_son_compte(self):
        certs = [
            {**cert("SNEP", "A", "Or"), "noms_correspondants": ["Shurik'N"]},
            {**cert("SNEP", "B", "Or"), "noms_correspondants": ["IAM"]},
            {**cert("SNEP", "C", "Or"), "noms_correspondants": ["IAM"]},
        ]

        assert repartition(certs, ["Shurik'N", "IAM"]) == "Répartition : Shurik'N 1 · IAM 2"

    def test_le_partage_est_annonce_pour_que_la_somme_ne_paraisse_pas_fausse(self):
        certs = [{**cert("SNEP", "A", "Or"), "noms_correspondants": ["Shurik'N", "IAM"]}]

        ligne = repartition(certs, ["Shurik'N", "IAM"])

        assert "Shurik'N 1 · IAM 1" in ligne
        assert "dont 1 créditée(s) à plusieurs" in ligne


class TestPaliers:
    """L'échelle d'un titre : ordonnée, et lisible même quand elle est énorme."""

    def test_le_multiplicateur_est_lu_comme_un_nombre(self):
        """Trié en TEXTE, « 10x » se rangerait avant « 2x »."""
        certs = [cert("RIAA", "D", f"{n}x Platino", date="2017-04-12") for n in (10, 2, 11, 3)]

        assert _paliers(certs) == ["2x Platino → 11x Platino (4 paliers) 2017-04-12"]

    def test_l_echelle_monte_de_l_or_au_diamant(self):
        certs = [
            cert("SNEP", "A", "Diamant", date="2022-01-01"),
            cert("SNEP", "A", "Or", date="2020-01-01"),
            cert("SNEP", "A", "Platine", date="2021-01-01"),
        ]

        assert _paliers(certs) == ["Or 2020-01-01  →  Platine 2021-01-01  →  Diamant 2022-01-01"]

    def test_les_paliers_d_une_meme_date_sont_resumes(self):
        """Le corpus RIAA déroule l'échelle d'un seul coup : 34 paliers, une date."""
        certs = [cert("RIAA", "D", f"{n}x Platino", date="2017-04-12") for n in range(2, 12)]

        [ligne] = _paliers(certs)

        assert ligne == "2x Platino → 11x Platino (10 paliers) 2017-04-12"

    def test_deux_paliers_d_une_meme_date_sont_listes_tels_quels(self):
        certs = [cert("RIAA", "D", n, date="2017-03-29") for n in ("Platino", "Oro")]

        assert _paliers(certs) == ["Oro, Platino 2017-03-29"]

    def test_une_longue_echelle_est_repliee_sur_plusieurs_lignes(self):
        certs = [cert("BRMA", "D", f"{n}x Platine", date=f"20{n:02d}-01-01") for n in range(2, 9)]

        lignes = _paliers(certs)

        assert len(lignes) > 1
        assert all(len(ligne) <= 90 for ligne in lignes)


class TestRecap:
    def test_sans_certification_il_le_dit(self):
        assert "Aucune certification" in recap([])

    def test_le_titre_et_l_artiste_credite_apparaissent(self):
        certs = [cert("SNEP", "On avait dit", "Or", artiste="47TER")]

        assert "On avait dit — 47TER [single]" in recap(certs)

    def test_l_echelle_du_titre_est_montree(self):
        certs = [
            cert("SNEP", "On avait dit", "Or", date="2020-09-18", artiste="47TER"),
            cert("SNEP", "On avait dit", "Platine", date="2021-11-11", artiste="47TER"),
        ]

        assert "Or 2020-09-18  →  Platine 2021-11-11" in recap(certs)

    def test_les_corps_sont_des_blocs_distincts(self):
        certs = [cert("SNEP", "A", "Or"), cert("BRMA", "B", "Or")]

        rendu = recap(certs)

        assert "SNEP — 1 certification(s)" in rendu
        assert "BRMA — 1 certification(s)" in rendu

    def test_l_entete_annonce_les_titres_a_historique(self):
        certs = [
            cert("SNEP", "A", "Or", date="2020-01-01"),
            cert("SNEP", "A", "Platine", date="2021-01-01"),
        ]

        assert "1 avec historique" in recap(certs)

    def test_les_nouveautes_sont_marquees_et_remontees(self):
        vieille = cert("SNEP", "Ancien", "Or", date="2019-01-01")
        neuve = cert("SNEP", "Nouveau", "Or", date="2018-01-01")

        lignes = recap([vieille, neuve], nouvelles=[neuve]).splitlines()
        # Les lignes de TITRE, pas l'en-tête du corps (qui porte le même tiret).
        titres = [ligne for ligne in lignes if "[single]" in ligne]

        assert titres[0].startswith("  + Nouveau")
        assert titres[1].startswith("    Ancien")

    def test_sans_nouveaute_aucun_titre_n_est_marque(self):
        rendu = recap([cert("SNEP", "A", "Or")])

        assert "+ A" not in rendu

    def test_au_dela_du_plafond_le_reste_est_annonce(self):
        """Un rapport qu'on ne lit pas ne vaut pas mieux qu'un compteur."""
        surplus = 7
        certs = [
            cert("SNEP", f"T{i}", "Or", date=f"{2000 + i}-01-01")
            for i in range(MAX_TITRES_AFFICHES + surplus)
        ]

        rendu = recap(certs)

        assert f"… et {surplus} autre(s) titre(s)" in rendu
