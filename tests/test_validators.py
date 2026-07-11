"""Tests des helpers purs des validators de certifications (RIAA, BRMA)
et du formateur LRC de ytmusic_api."""

import pytest

from src.api.ytmusic_api import YTMusicAPI
from src.utils.brma_validator import _level_known as brma_level_known
from src.utils.riaa_validator import _level_known as riaa_level_known
from src.utils.riaa_validator import _level_norm, _to_iso


class TestRiaaToIso:
    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("October 17, 2017", "2017-10-17"),
            ("Oct 17, 2017", "2017-10-17"),
            ("10/17/2017", "2017-10-17"),
            ("2017-10-17", "2017-10-17"),
            ("", ""),
            ("None", ""),
            # Contrairement à cert_matcher._to_iso_date, un format inconnu
            # donne "" (et non la chaîne d'origine)
            ("17 octobre 2017", ""),
        ],
    )
    def test_conversions(self, entree, attendu):
        assert _to_iso(entree) == attendu


class TestRiaaLevelNorm:
    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("4x Multi-Platinum", "4X PLATINUM"),
            ("Multi-Platinum", "PLATINUM"),
            ("gold", "GOLD"),
            ("  Double   Or ", "DOUBLE OR"),  # espaces normalisés + majuscules
        ],
    )
    def test_normalisation(self, entree, attendu):
        assert _level_norm(entree) == attendu


class TestRiaaLevelKnown:
    @pytest.mark.parametrize(
        "valide", ["Gold", "platinum", "DIAMOND", "3x Platinum", "2x Multi-Platinum"]
    )
    def test_niveaux_valides(self, valide):
        assert riaa_level_known(valide)

    @pytest.mark.parametrize("invalide", ["Ruby", "", "3x Gold"])
    def test_niveaux_invalides(self, invalide):
        assert not riaa_level_known(invalide)


class TestBrmaLevelKnown:
    REFERENTIEL = {"or", "platine", "diamant"}

    def test_niveau_du_referentiel(self):
        assert brma_level_known("Or", self.REFERENTIEL)
        assert brma_level_known("PLATINE", self.REFERENTIEL)

    def test_multiplicateur_ultratop(self):
        # '2x Platine', '12x Or' : notés en multiplicateur sur Ultratop
        assert brma_level_known("2x Platine", self.REFERENTIEL)
        assert brma_level_known("12x Or", self.REFERENTIEL)

    def test_niveau_inconnu(self):
        assert not brma_level_known("Ruby", self.REFERENTIEL)
        assert not brma_level_known("", self.REFERENTIEL)


class TestFormatLrc:
    """Conversion des lignes synchronisées YTM (start_time en ms) vers LRC."""

    def test_lignes_dict(self):
        lrc = YTMusicAPI._format_lrc(
            [
                {"text": "Première ligne", "start_time": 5000},
                {"text": "Deuxième ligne", "start_time": 72340},
            ]
        )
        assert lrc == "[00:05.00]Première ligne\n[01:12.34]Deuxième ligne"

    def test_aucun_timestamp_retourne_none(self):
        # Paroles non synchronisées → pas de LRC
        assert YTMusicAPI._format_lrc([{"text": "Sans temps", "start_time": None}]) is None

    def test_ligne_sans_timestamp_conservee_sans_balise(self):
        lrc = YTMusicAPI._format_lrc(
            [
                {"text": "Avec temps", "start_time": 1000},
                {"text": "Sans temps", "start_time": None},
            ]
        )
        assert lrc == "[00:01.00]Avec temps\nSans temps"
