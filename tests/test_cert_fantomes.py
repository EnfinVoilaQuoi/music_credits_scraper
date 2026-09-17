"""Lignes FANTÔMES : le même événement écrit deux fois, cassé et sain.

Ce n'est pas une ligne que le nettoyeur aurait ratée. C'est un défaut de la clé
de fusion du CSV canonique : `merge_canonical` passe par `normalize_text`, qui
SUPPRIME le « ? » et développe la ligature (« Œ » → « OE »). « AU C?UR D'IAM »
devient donc « AU CUR D'IAM » et « AU CŒUR D'IAM » devient « AU COEUR D'IAM » —
deux clés distinctes, deux lignes gardées, une seule certification.

Le clean accumulant d'un export à l'autre, il gardait le libellé d'hier même
après que la source a corrigé son encodage. Mesuré le 2026-09-06 : **130 lignes**
côté SNEP — dont « SHURIK?N », qui coupait en deux la discographie certifiée de
l'artiste — et **0** côté BRMA comme RIAA, dont les bruts sont de vraies
accumulations qui ne se corrigent jamais toutes seules.
"""

import pytest

from src.utils.cert_normalize import ecriture_cassee, reperer_fantomes, squelette_libelle
from src.utils.snep_build import purger_fantomes

# (artiste, titre, catégorie, niveau, date) — mêmes rangs que le CSV SNEP réduit
ARTISTE, TITRE = 0, 1
AUTRES = (2, 3, 4)


def ligne(artiste, titre, cat="Albums", niveau="Or", date="2004-08-24"):
    return [artiste, titre, cat, niveau, date]


def reperer(lignes):
    return reperer_fantomes(lignes, i_artiste=ARTISTE, i_titre=TITRE, autres=AUTRES)


class TestSqueletteLibelle:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("AU C?UR D'IAM", "AU CŒUR D'IAM"),
            ("L?empire du c", "L'empire du c"),
            ("DONT?SAY GOODBYE", "DON'T SAY GOODBYE"),
            ("SMILE?IT CONFUSES PEOPLE", "SMILE... IT CONFUSES PEOPLE"),
            ("SHURIK?N", "SHURIK'N"),
        ],
        ids=["ligature", "elision", "espace-mange", "points-de-suspension", "artiste"],
    )
    def test_deux_ecritures_du_meme_libelle(self, a, b):
        assert squelette_libelle(a) == squelette_libelle(b)

    def test_deux_libelles_differents_ne_se_confondent_pas(self):
        assert squelette_libelle("PETITE S?UR") != squelette_libelle("GRANDE SŒUR")

    @pytest.mark.parametrize("vide", ["", "   ", None])
    def test_un_libelle_vide_donne_un_squelette_vide(self, vide):
        assert squelette_libelle(vide) == ""

    def test_accents_retires(self):
        """« BEYĀH » en base, « BEYAH » sur le site SNEP (2026-09-17)."""
        assert squelette_libelle("BEYĀH") == squelette_libelle("BEYAH") == "beyah"

    @pytest.mark.parametrize(
        "libelle, casse",
        [
            ("C?UR", True),
            ("C" + chr(0x9C) + "ur", True),
            ("Bient" + chr(0x95) + "t", True),
            ("Cœur", False),
            ("WHY?", True),
            ("COEUR", False),
        ],
    )
    def test_ecriture_cassee(self, libelle, casse):
        """Le « ? » de substitution ET les contrôles C1 (octet cp1252 lu en
        latin-1) trahissent un encodage cassé. Un vrai point d'interrogation
        compte aussi, sans conséquence : un fantôme exige une jumelle saine."""
        assert ecriture_cassee(libelle) is casse


class TestReperage:
    def test_la_cassee_est_designee_pas_la_saine(self):
        lignes = [ligne("IAM", "AU C?UR D'IAM"), ligne("IAM", "AU CŒUR D'IAM")]

        assert reperer(lignes) == [0]

    def test_le_champ_ARTISTE_compte_aussi(self):
        """« SHURIK?N » coupait en deux la discographie certifiée de l'artiste."""
        lignes = [ligne("SHURIK'N", "OU JE VIS"), ligne("SHURIK?N", "OU JE VIS")]

        assert reperer(lignes) == [1]

    def test_les_deux_champs_cassés_a_la_fois(self):
        lignes = [
            ligne("ENFOIRES EN CH?UR", "ENFOIRES EN CH?UR"),
            ligne("ENFOIRES EN CHŒUR", "ENFOIRES EN CHŒUR"),
        ]

        assert reperer(lignes) == [0]

    def test_plusieurs_cassees_pour_une_saine(self):
        lignes = [
            ligne("IAM", "AU CŒUR D'IAM"),
            ligne("IAM", "AU C?UR D'IAM"),
            ligne("IAM", "AU C?UR D?IAM"),
        ]

        assert reperer(lignes) == [1, 2]


class TestCeQuOnNeTouchePas:
    """La différence entre nettoyer et EFFACER."""

    def test_sans_version_saine_la_cassee_reste(self):
        """Elle est la seule trace de la certification : la retirer perdrait tout."""
        assert reperer([ligne("IAM", "AU C?UR D'IAM")]) == []

    def test_un_palier_different_n_est_pas_le_meme_evenement(self):
        lignes = [
            ligne("IAM", "AU C?UR D'IAM", niveau="Or"),
            ligne("IAM", "AU CŒUR D'IAM", niveau="Platine"),
        ]

        assert reperer(lignes) == []

    def test_une_date_differente_n_est_pas_le_meme_evenement(self):
        lignes = [
            ligne("IAM", "AU C?UR D'IAM", date="2004-08-24"),
            ligne("IAM", "AU CŒUR D'IAM", date="2005-01-01"),
        ]

        assert reperer(lignes) == []

    def test_une_categorie_differente_non_plus(self):
        lignes = [
            ligne("IAM", "AU C?UR D'IAM", cat="Albums"),
            ligne("IAM", "AU CŒUR D'IAM", cat="Vidéos"),
        ]

        assert reperer(lignes) == []

    def test_un_vrai_point_d_interrogation_survit(self):
        """Mesuré : aucune des 130 lignes retirées n'en portait un."""
        lignes = [ligne("SHAKIRA", "QUI SAIT ?"), ligne("SHAKIRA", "AUTRE CHOSE")]

        assert reperer(lignes) == []

    def test_deux_lignes_saines_ne_sont_pas_des_fantomes(self):
        lignes = [ligne("IAM", "AU CŒUR D'IAM"), ligne("IAM", "AU CŒUR D'IAM")]

        assert reperer(lignes) == []

    def test_une_ligne_malformee_est_ignoree_pas_plantee(self):
        """Les nettoyeurs conservent telles quelles les lignes qu'ils ne savent pas lire."""
        lignes = [["tronquee"], ligne("IAM", "AU C?UR D'IAM"), ligne("IAM", "AU CŒUR D'IAM")]

        assert reperer(lignes) == [1]

    def test_aucune_ligne(self):
        assert reperer([]) == []


class TestPurgeDuCsvCanonique:
    """`purger_fantomes` sur les dictionnaires du CSV canonique SNEP."""

    @staticmethod
    def rangee(artist, title, certification="Or", date="2004-08-24", category="Albums"):
        return {
            "artist": artist,
            "title": title,
            "publisher": "",
            "category": category,
            "certification": certification,
            "release_date": "",
            "certification_date": date,
        }

    def test_la_saine_est_gardee_la_cassee_rendue(self):
        saine = self.rangee("IAM", "AU CŒUR D'IAM")
        cassee = self.rangee("IAM", "AU C?UR D'IAM")

        gardees, retirees = purger_fantomes([cassee, saine])

        assert gardees == [saine]
        assert retirees == [cassee]

    def test_l_ordre_du_fichier_est_preserve(self):
        """`merge_canonical` promet de préserver l'ordre : la purge ne le casse pas."""
        rangees = [
            self.rangee("A", "UN"),
            self.rangee("IAM", "AU C?UR D'IAM"),
            self.rangee("IAM", "AU CŒUR D'IAM"),
            self.rangee("Z", "DEUX"),
        ]

        gardees, _ = purger_fantomes(rangees)

        assert [g["artist"] for g in gardees] == ["A", "IAM", "Z"]

    def test_sans_fantome_rien_ne_bouge(self):
        rangees = [self.rangee("A", "UN"), self.rangee("B", "DEUX")]

        gardees, retirees = purger_fantomes(rangees)

        assert gardees == rangees
        assert retirees == []

    def test_liste_vide(self):
        assert purger_fantomes([]) == ([], [])
