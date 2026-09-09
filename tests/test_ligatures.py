"""Les ligatures ne sont plus AVALÉES par la normalisation.

`unicodedata.normalize("NFKD")` ne décompose ni `œ` ni `æ` — elles n'ont pas de
décomposition de compatibilité — et `ß`, `ø`, `ł`, `đ`, `þ`, `ð` sont des lettres
à part entière, pas des lettres accentuées. Le passage en ASCII qui suivait les
SUPPRIMAIT donc en silence.

Les deux normaliseurs cassaient le même mot de deux façons différentes :
`normalize_title('cœur')` rendait « cur » (lettre avalée) et
`normalize_name('Sœur')` rendait « s ur » (coupure de mot). Constaté le
2026-09-09 dans les logs — un identifiant Spotify JUSTE refusé parce que
« Peine de coeur » ne rejoignait pas « Peine de cœur ».
"""

import pytest

from src.utils.title_matching import (
    developper_ligatures,
    either_contains_as_words,
    normalize_name,
    normalize_title,
)


class TestDevelopperLigatures:
    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("cœur", "coeur"),
            ("CŒUR", "COEUR"),
            ("Blæst", "Blaest"),
            ("Straße", "Strasse"),
            ("Sølvi", "Solvi"),
            # Un seul étage : les ligatures. Les lettres ACCENTUÉES (ó, ź) sont
            # l'affaire de la décomposition Unicode, en aval — les mélanger ici
            # ferait de cette fonction un second normaliseur.
            ("Łódź", "Lódź"),
            ("Þór", "THór"),
            ("Ðjango", "Django"),
        ],
    )
    def test_les_lettres_sont_developpees(self, entree, attendu):
        assert developper_ligatures(entree) == attendu

    def test_les_accents_ne_sont_PAS_son_affaire(self):
        """Elle ne fait qu'une chose. Ce sont `normalize_title` et
        `normalize_name` qui enchaînent ensuite la décomposition."""
        assert developper_ligatures("môme") == "môme"
        assert normalize_title("Łódź") == "lodz"

    def test_les_DEUX_casses_sont_traitees(self):
        """Les deux normaliseurs ne changent pas la casse au même moment : une
        table à sens unique manquerait la moitié des titres."""
        assert developper_ligatures("Œuf") == "OEuf"
        assert developper_ligatures("œuf") == "oeuf"

    def test_une_chaine_sans_ligature_ne_bouge_pas(self):
        assert developper_ligatures("Bande organisée") == "Bande organisée"
        assert developper_ligatures("") == ""


class TestNormalizeTitre:
    def test_le_cas_des_logs(self):
        """L'identifiant refusé le 2026-09-09 : Spotify écrit « Peine de cœur »,
        Genius « Peine de coeur »."""
        assert normalize_title("Peine de cœur") == normalize_title("Peine de coeur")

    @pytest.mark.parametrize(
        ("titre", "attendu"),
        [
            ("Cœur de môme", "coeur de mome"),
            ("L’œuvre au noir", "loeuvre au noir"),
            ("L’Œil de la Joconde", "loeil de la joconde"),
            ("La BAC et les mœurs", "la bac et les moeurs"),
        ],
    )
    def test_titres_reels_de_la_base(self, titre, attendu):
        assert normalize_title(titre) == attendu


class TestNormalizeNom:
    @pytest.mark.parametrize(
        ("nom", "attendu"),
        [
            ("Yung Cœur", "yung coeur"),
            ("Rægular", "raegular"),
            ("Yønah", "yonah"),
            ("IBØ", "ibo"),
        ],
    )
    def test_noms_reels_de_credits(self, nom, attendu):
        """Le défaut était PIRE sur les noms : la lettre devenait une coupure de
        mot, donc « IBØ » se réduisait à « ib » — deux lettres, qui s'ancrent
        dans n'importe quoi."""
        assert normalize_name(nom) == attendu

    def test_un_nom_ne_se_reduit_plus_a_deux_lettres(self):
        assert normalize_name("IBØ") != "ib"


class TestNonRegressionMesuree:
    """Sur les 2 117 titres de la base, le correctif change 8 valeurs et fait
    perdre UN rapprochement — un FAUX POSITIF, mesuré le 2026-09-09.

    C'est le seul rapprochement perdu de tout le corpus, et il fallait le
    perdre : le plan exigeait de comprendre lequel avant de poursuivre.
    """

    def test_le_faux_positif_que_le_defaut_fabriquait(self):
        """« La BAC et les mœurs » devenait « la bac et les MURS », qui contient
        le mot « murs » — donc rapproché du morceau « Murs », sans aucun rapport.
        La ligature avalée FABRIQUAIT un mot qui n'existe pas dans le titre."""
        mœurs, murs = normalize_title("La BAC et les mœurs"), normalize_title("Murs")
        assert not either_contains_as_words(mœurs, murs)

    def test_les_rapprochements_legitimes_survivent(self):
        """Le relâchement utile est conservé : le suffixe « feat. » tombe, les
        éditions se rejoignent."""
        assert normalize_title("Titre (feat. SCH)") == normalize_title("Titre")
        assert either_contains_as_words(
            normalize_title("Un pour la plume"), normalize_title("Un pour la plume (Live)")
        )
