"""Tests du normaliseur de titres PARTAGÉ (matching Kworb / YTM / Genius / DB).

Les cas historiques de faux non-matchés (JOURNAL 2026-07-02) sont verrouillés ici.
"""

import pytest

from src.utils.title_matching import (
    base_album_key,
    clean_display_title,
    clean_stored_title,
    either_contains_as_words,
    names_match_as_words,
    normalize_name,
    normalize_title,
)


class TestCasHistoriques:
    """Divergences qui ont réellement coûté des faux non-matchés."""

    def test_murder_inc_avec_et_sans_point(self):
        assert normalize_title("MURDER INC") == normalize_title("MURDER INC.")

    def test_acronymes_avec_points(self):
        assert normalize_title("S.O.A.B") == normalize_title("SOAB")

    def test_augmentation_pt2(self):
        assert normalize_title("L'augmentation - Pt. 2") == normalize_title("L’augmentation, Pt. 2")


class TestFeaturings:
    @pytest.mark.parametrize(
        "variante",
        [
            "Titre (feat. Machin)",
            "Titre [feat. Machin]",
            "Titre (ft. Machin)",
            "Titre (avec Machin)",
            "Titre (with Machin)",
            "Titre feat. Machin",
            "Titre ft. Machin",
            # Cas réel vu sur kworb
            "Titre ft. ISHA",
        ],
    )
    def test_suffixes_feat_retires(self, variante):
        assert normalize_title(variante) == "titre"


class TestNormalisation:
    def test_accents_retires(self):
        assert normalize_title("Étoile filante") == "etoile filante"

    def test_apostrophes_typographiques_et_droites(self):
        assert normalize_title("L'empire") == normalize_title("L’empire")

    def test_espace_avant_chiffre(self):
        assert normalize_title("Vol.3") == normalize_title("Vol. 3")

    def test_casse(self):
        assert normalize_title("BITUME CAVIAR") == "bitume caviar"

    def test_vide_et_none(self):
        assert normalize_title("") == ""
        assert normalize_title(None) == ""


# ── Rattachement des éditions ────────────────────────────────────────────────
class TestBaseAlbumKey:
    """Une réédition porte un titre DIFFÉRENT (« … [Bonus] »). Sans la rattacher
    à son album, ses pistes disparaissent du total côté Spotify, et son
    identifiant d'édition se perd côté Kworb — l'album ressort alors sous son
    vrai chiffre sans que rien ne le signale.
    """

    CONNUES = {"man black roses lost feelings", "matrix", "dom perignon crying"}

    @pytest.mark.parametrize(
        "titre",
        [
            "man black roses lost feelings bonus",
            "dom perignon crying bonus",
            "matrix deluxe",
            "matrix reedition",
            "matrix collector",
            "matrix remastered",
        ],
    )
    def test_une_edition_rejoint_son_album(self, titre):
        assert base_album_key(titre, self.CONNUES) is not None

    @pytest.mark.parametrize("titre", ["matrix ii", "matrix 2", "matrix reloaded", "autre chose"])
    def test_une_SUITE_reste_un_autre_album(self, titre):
        """Le garde qui justifie une liste FERMÉE de marqueurs : un simple
        préfixe commun rattacherait « Matrix II » à « Matrix »."""
        assert base_album_key(titre, self.CONNUES) is None

    def test_correspondance_exacte_d_abord(self):
        assert base_album_key("matrix", self.CONNUES) == "matrix"


class TestNormalizeName:
    """Le normaliseur de NOMS (ex-`_text_match._norm`), remonté ici le 2026-09-05
    pour que les scrapers et `update_kworb` puissent s'en servir sans importer
    un module privé de `src/api`."""

    @pytest.mark.parametrize(
        ("brut", "attendu"),
        [
            ("Éléphant", "elephant"),
            ("A-B_C", "a b c"),
            ("  Deux  ", "deux"),
            ("Limsa d’Aulnay", "limsa d aulnay"),
            ("Limsa d'Aulnay", "limsa d aulnay"),
            ("Jul & S.C.H. (2024)", "jul s c h 2024"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_normalisation(self, brut, attendu):
        assert normalize_name(brut) == attendu

    def test_ne_coupe_pas_au_featuring(self):
        """`normalize_title` amputerait « feat. » et la suite — sur un NOM
        d'artiste ce serait une mutilation, d'où deux normaliseurs distincts."""
        assert normalize_name("Feat Vincent") == "feat vincent"
        assert normalize_title("Feat Vincent") == "feat vincent"
        assert normalize_title("Titre feat. Vincent") == "titre"


class TestNamesMatchAsWords:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("Isha", "ISHA"),  # casse
            ("Jul", "Jul & SCH"),  # l'inclusion utile
            ("Jul & SCH", "Jul"),  # dans l'autre sens
            ("Isha", "Isha (7)"),  # suffixe de désambiguïsation Genius
            ("Limsa d'Aulnay", "Limsa d’Aulnay"),  # apostrophe typographique
        ],
    )
    def test_meme_artiste(self, a, b):
        assert names_match_as_words(a, b) is True

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("IAM", "Williams"),
            ("Isha", "Misha Van Der Werf"),
            ("SCH", "ScHoolboy Q"),
            ("SCH", "Coline Schneider"),
            ("Jul", "Julien Doré"),
        ],
    )
    def test_homonyme_par_sous_chaine_refuse(self, a, b):
        """Les cinq noms mesurés sur la base réelle au tier 7 (18 pages Kworb
        acceptées à tort sur 2 515 noms croisés)."""
        assert names_match_as_words(a, b) is False

    @pytest.mark.parametrize(
        ("a", "b"), [("", "ISHA"), ("ISHA", ""), (None, "ISHA"), ("!", "ISHA")]
    )
    def test_vide_ou_intraduisible(self, a, b):
        """Un nom qui se normalise en chaîne vide ne matche RIEN — sans quoi il
        matcherait tout."""
        assert names_match_as_words(a, b) is False


class TestEitherContainsAsWords:
    """Le prédicat de TITRES : les chaînes arrivent déjà normalisées."""

    @pytest.mark.parametrize(
        ("a", "b"),
        [("matrix", "matrix intro"), ("ceo bonus", "ceo"), ("toi", "a cause de toi")],
    )
    def test_inclusion_en_mot(self, a, b):
        assert either_contains_as_words(a, b) is True

    @pytest.mark.parametrize(
        ("a", "b"), [("toi", "etoile"), ("quoi", "pourquoi"), ("ares", "la paresse")]
    )
    def test_sous_chaine_intra_mot(self, a, b):
        assert either_contains_as_words(a, b) is False


class TestCleanStoredTitle:
    """Les caractères SANS LARGEUR fabriquent des doublons invisibles.

    Genius en sème : « ​bank » (Josman) porte un `U+200B` en tête, et
    « très tard le soir » en portait QUATRE — d'où deux fiches pour un seul
    morceau, chacune détenant des données que l'autre n'avait pas (fusionnées à
    la main le 2026-09-07). Comme l'unicité d'un morceau est
    `UNIQUE(title, artist_id)`, l'œil ne voit rien et la base voit deux titres.
    """

    @pytest.mark.parametrize(
        "sale, propre",
        [
            ("\u200bbank", "bank"),
            ("\u200b\u200b\u200b\u200btrès tard le soir", "très tard le soir"),
            ("mi\u200bdi", "midi"),  # au milieu aussi
            ("titre\ufeff", "titre"),  # BOM en fin
            ("a\u200cb\u200dc\u2060d", "abcd"),  # liants et joint insécable
        ],
    )
    def test_les_caracteres_sans_largeur_sont_retires(self, sale, propre):
        assert clean_stored_title(sale) == propre

    def test_un_titre_propre_est_rendu_tel_quel(self):
        assert clean_stored_title("Bande organisée") == "Bande organisée"

    def test_les_espaces_de_bordure_sont_coupes(self):
        assert clean_stored_title("  bank  ") == "bank"

    def test_rien(self):
        assert clean_stored_title("") == ""
        assert clean_stored_title(None) == ""

    def test_les_decorations_barrees_RESTENT(self):
        """Contrairement à `clean_display_title` : « F̶i̶e̶s̶t̶a̶ » est le titre
        que Genius publie, il doit être stocké tel quel. Seul l'affichage le
        déshabille."""
        barre = "F\u0336i\u0336e\u0336s\u0336t\u0336a\u0336"
        assert clean_stored_title(barre) == barre
        assert clean_display_title(barre) == "Fiesta"

    def test_laffichage_nettoie_AUSSI_les_invisibles(self):
        assert clean_display_title("\u200bbank") == "bank"

    def test_ne_touche_ni_a_la_casse_ni_a_la_ponctuation(self):
        """C'est `normalize_title` qui écrase tout ça, et son résultat est
        illisible : les deux fonctions ne servent pas au même usage."""
        assert clean_stored_title("S.O.A.B (feat. X)") == "S.O.A.B (feat. X)"
        assert normalize_title("S.O.A.B (feat. X)") == "soab"
