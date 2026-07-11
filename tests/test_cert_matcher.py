"""Tests des helpers purs de cert_matcher (normalisation dates/niveaux, ranking).

Le chargement des CSV (SNEP/BRMA/RIAA) n'est pas couvert ici : il dépend des
fichiers de data/ — hors périmètre des tests unitaires.
"""

import pytest

from src.utils.cert_matcher import CertMatcher, _riaa_level, _to_iso_date


class TestToIsoDate:
    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("October 17, 2017", "2017-10-17"),
            ("Oct 17, 2017", "2017-10-17"),
            ("10/17/2017", "2017-10-17"),
            ("2017-10-17", "2017-10-17"),  # déjà ISO : inchangé
            ("", ""),
            ("None", ""),
        ],
    )
    def test_conversions(self, entree, attendu):
        assert _to_iso_date(entree) == attendu

    def test_format_inconnu_laisse_tel_quel(self):
        assert _to_iso_date("17 octobre 2017") == "17 octobre 2017"


class TestRiaaLevel:
    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("4x Multi-Platinum", "4x Platinum"),
            ("2X MULTI-PLATINUM", "2x Platinum"),
            ("Multi-Platinum", "Platinum"),
            ("Gold", "Gold"),
            ("Diamond", "Diamond"),
            ("", ""),
        ],
    )
    def test_normalisation(self, entree, attendu):
        assert _riaa_level(entree) == attendu


class TestLevelRank:
    """_level_rank n'utilise pas self → appel non lié (évite le chargement CSV)."""

    def _rank(self, level: str) -> float:
        return CertMatcher._level_rank(None, level)

    def test_ordre_des_paliers(self):
        # Plus petit = plus prestigieux
        assert self._rank("Quadruple Diamant") < self._rank("Diamant")
        assert self._rank("Diamant") < self._rank("Platine")
        assert self._rank("Platine") < self._rank("Or")

    def test_equivalence_fr_en(self):
        assert self._rank("Diamant") == self._rank("Diamond")
        assert self._rank("Or") == self._rank("Gold")
        assert self._rank("Platine") == self._rank("Platinum")

    def test_multiplicateurs_nx(self):
        # "4x Platinum" : un cran au-dessus du palier simple, sous le palier supérieur
        assert self._rank("4x Platinum") < self._rank("Platinum")
        assert self._rank("4x Platinum") > self._rank("Diamant")

    def test_niveau_inconnu_relegue_en_fin(self):
        assert self._rank("Ruby") == 99.0
