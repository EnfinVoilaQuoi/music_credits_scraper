"""Tests du normaliseur de titres PARTAGÉ (matching Kworb / YTM / Genius / DB).

Les cas historiques de faux non-matchés (JOURNAL 2026-07-02) sont verrouillés ici.
"""

import pytest

from src.utils.title_matching import normalize_title


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
