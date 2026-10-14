"""Le code-barres d'une édition, écrit pareil par toutes les sources (2026-10-14)."""

import pytest

from src.utils.codes_barres import cle_upc, code_barres_discogs


class TestCleUpc:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("859733291991", "0859733291991"),  # AVLD : UPC-A Deezer / EAN-13
            ("3596974281424", "3 596974 281424"),  # Discogs imprimé avec espaces
            ("602468039310", "0-602468-039310"),
        ],
    )
    def test_deux_ecritures_une_cle(self, a, b):
        assert cle_upc(a) == cle_upc(b) is not None

    @pytest.mark.parametrize(
        "code", [None, "", "LC 0979", "1234567", "123456789012345", "0000000000"]
    )
    def test_ce_qui_n_est_pas_un_code_barres(self, code):
        assert cle_upc(code) is None


class TestCodeBarresDiscogs:
    def test_imprime_et_scanne_une_seule_cle(self):
        """*Civilisation Édition Ultime*, Discogs 24973729."""
        identifiants = [
            {"type": "Barcode", "value": "3 596974 281424", "description": "Printed"},
            {"type": "Barcode", "value": "3596974281424", "description": "Scanned"},
            {"type": "Label Code", "value": "LC 12345"},
        ]
        assert code_barres_discogs(identifiants) == "3596974281424"

    def test_deux_codes_differents_on_ne_choisit_pas(self):
        identifiants = [
            {"type": "Barcode", "value": "3596974281424"},
            {"type": "Barcode", "value": "602468039310"},
        ]
        assert code_barres_discogs(identifiants) is None

    @pytest.mark.parametrize(
        "identifiants", [None, [], [{"type": "Matrix / Runout", "value": "X"}]]
    )
    def test_sans_code_barres(self, identifiants):
        assert code_barres_discogs(identifiants) is None
