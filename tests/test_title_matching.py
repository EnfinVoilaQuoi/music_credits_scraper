"""Tests du normaliseur de titres PARTAGÉ (matching Kworb / YTM / Genius / DB).

Les cas historiques de faux non-matchés (JOURNAL 2026-07-02) sont verrouillés ici.
"""

import pytest

from src.utils.title_matching import base_album_key, normalize_title


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
