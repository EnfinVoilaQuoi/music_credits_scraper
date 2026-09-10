"""Tests des helpers purs des validators de certifications (RIAA, BRMA)
et du formateur LRC de ytmusic_api."""

import pytest

from src.api.ytmusic_api import YTMusicAPI
from src.utils.brma_validator import niveau_connu as brma_niveau_connu
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
            # Le programme LATIN, que la copie privée du validateur ne
            # connaissait pas — elle ne savait lire que « platinum ».
            ("2x Multi-Platino", "2X PLATINO"),
            ("Multi-Platino", "PLATINO"),
            # « 1x » est un multiplicateur qui ne multiplie rien : le nettoyeur
            # le ramène au palier simple, le validateur le gardait distinct.
            ("1x Multi-Platinum", "PLATINUM"),
            # Hors vocabulaire RIAA : PRÉSERVÉ tel quel (espaces normalisés,
            # majuscules). Déléguer ne doit pas mangler ce qu'on ne connaît pas.
            ("  Double   Or ", "DOUBLE OR"),
            ("Titane", "TITANE"),
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


class TestBrmaNiveauConnu:
    """Le prédicat belge, sur le VRAI référentiel.

    Il prenait le référentiel en PARAMÈTRE, et ces tests lui injectaient un jeu
    à eux (`{"or", "platine", "diamant"}`) — ils vérifiaient donc la mécanique
    du prédicat sans jamais toucher au vocabulaire réel. « Quadruple Platine »,
    qui existe chez Ultratop, n'était couvert par rien. Depuis le 2026-09-09 le
    vocabulaire vit dans `cert_normalize` et le prédicat n'a plus de paramètre :
    les tests portent sur ce qui tourne.
    """

    @pytest.mark.parametrize(
        "valide", ["Or", "PLATINE", "Quadruple Platine", "2x Platine", "12x Or", "3x Diamant"]
    )
    def test_niveaux_valides(self, valide):
        assert brma_niveau_connu(valide)

    @pytest.mark.parametrize("invalide", ["Ruby", "", "2x Quadruple Platine"])
    def test_niveaux_invalides(self, invalide):
        assert not brma_niveau_connu(invalide)


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
