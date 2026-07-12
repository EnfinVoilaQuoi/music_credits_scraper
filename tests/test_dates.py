"""Tests du parseur de date unique (src/utils/dates.parse_flexible)."""

from datetime import datetime

from src.utils.dates import parse_flexible


class TestParseFlexible:
    def test_none(self):
        assert parse_flexible(None) is None

    def test_chaine_vide(self):
        assert parse_flexible("") is None
        assert parse_flexible("   ") is None

    def test_datetime_inchange(self):
        d = datetime(2020, 5, 1, 12, 30)
        assert parse_flexible(d) is d

    def test_date_simple(self):
        assert parse_flexible("2020-05-01") == datetime(2020, 5, 1)

    def test_iso_avec_heure(self):
        assert parse_flexible("2020-05-01T12:30:00") == datetime(2020, 5, 1, 12, 30)

    def test_iso_avec_z_devient_aware(self):
        parsed = parse_flexible("2020-05-01T12:30:00Z")
        assert parsed.year == 2020
        assert parsed.tzinfo is not None

    def test_avec_heure_espace(self):
        assert parse_flexible("2020-05-01 08:00:00") == datetime(2020, 5, 1, 8, 0, 0)

    def test_troncature_dernier_recours(self):
        # Chaîne non-ISO mais commençant par une date : les 10 premiers caractères
        assert parse_flexible("2020-05-01 (single)") == datetime(2020, 5, 1)

    def test_chaine_illisible(self):
        assert parse_flexible("pas une date") is None

    def test_type_non_supporte(self):
        assert parse_flexible(12345) is None
        assert parse_flexible(["2020-05-01"]) is None
