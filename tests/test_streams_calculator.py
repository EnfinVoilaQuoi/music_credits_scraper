"""Tests de streams_calculator — extrapolation par parts de marché."""

from src.utils.streams_calculator import (
    calculate_total_monthly_listeners,
    calculate_total_streams,
    format_streams,
    streams_source_label,
)


class TestCalculateTotalStreams:
    def test_deux_sources(self):
        # (400 + 250) / 0.65 = 1000
        assert calculate_total_streams(400, 250) == 1000

    def test_spotify_seul_extrapole_via_sa_part(self):
        assert calculate_total_streams(400, None) == 1000  # 400 / 0.40

    def test_ytm_seul_extrapole_via_sa_part(self):
        assert calculate_total_streams(None, 250) == 1000  # 250 / 0.25

    def test_zero_traite_comme_absent(self):
        assert calculate_total_streams(0, 250) == 1000
        assert calculate_total_streams(0, 0) is None

    def test_aucune_source(self):
        assert calculate_total_streams(None, None) is None


class TestCalculateTotalMonthlyListeners:
    def test_meme_logique_que_les_streams(self):
        assert calculate_total_monthly_listeners(400, 250) == 1000
        assert calculate_total_monthly_listeners(None, None) is None


class TestStreamsSourceLabel:
    def test_donnees_completes_pas_de_suffixe(self):
        assert streams_source_label(100, 100) == ""

    def test_spotify_seul(self):
        assert streams_source_label(100, None) == " ~Sp"

    def test_ytm_seul(self):
        assert streams_source_label(None, 100) == " ~YT"

    def test_aucune_donnee(self):
        assert streams_source_label(None, None) == ""


class TestFormatStreams:
    def test_separateur_milliers_espace_fine_insecable(self):
        # U+202F voulu : évite qu'un nombre se coupe en fin de ligne dans la GUI
        assert format_streams(15734892) == "15 734 892"

    def test_avec_suffixe(self):
        assert format_streams(15734892, " ~Sp") == "15 734 892 ~Sp"

    def test_none_chaine_vide(self):
        assert format_streams(None) == ""
