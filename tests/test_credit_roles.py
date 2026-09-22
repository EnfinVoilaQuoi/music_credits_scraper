"""Tests de la table de mapping ``credit_roles.map_role``.

Reprend les cas du scraper Genius pour garantir l'équivalence après
l'extraction, et ajoute les libellés YouTube/Spotify.
"""

from src.models.track import CreditRole
from src.utils.credit_roles import map_role


class TestExactMatch:
    def test_producer(self):
        assert map_role("Producer") is CreditRole.PRODUCER

    def test_producers_plural(self):
        assert map_role("Producers") is CreditRole.PRODUCER

    def test_writer(self):
        assert map_role("Writer") is CreditRole.WRITER

    def test_songwriter(self):
        assert map_role("Songwriter") is CreditRole.WRITER

    def test_composers(self):
        assert map_role("Composers") is CreditRole.COMPOSER

    def test_lyricist(self):
        assert map_role("Lyricist") is CreditRole.LYRICIST

    def test_mixing_engineer(self):
        assert map_role("Mixing Engineer") is CreditRole.MIXING_ENGINEER

    def test_mastering_engineer(self):
        assert map_role("Mastering Engineer") is CreditRole.MASTERING_ENGINEER

    def test_recording_engineer(self):
        assert map_role("Recording Engineer") is CreditRole.RECORDING_ENGINEER

    def test_remixer(self):
        assert map_role("Remixer") is CreditRole.REMIXER

    def test_remixed_by(self):
        assert map_role("Remixed By") is CreditRole.REMIXER

    def test_a_and_r(self):
        assert map_role("A&R") is CreditRole.A_AND_R

    def test_label(self):
        assert map_role("Label") is CreditRole.LABEL

    def test_featuring(self):
        assert map_role("Featuring") is CreditRole.FEATURED

    def test_sample(self):
        assert map_role("Sample") is CreditRole.SAMPLE


class TestCaseInsensitive:
    def test_producer_lower(self):
        assert map_role("producer") is CreditRole.PRODUCER

    def test_writer_upper(self):
        assert map_role("WRITER") is CreditRole.WRITER

    def test_mixing_engineer_mixed(self):
        assert map_role("mixing engineer") is CreditRole.MIXING_ENGINEER


class TestFuzzyRules:
    def test_video_director(self):
        assert map_role("Video Director") is CreditRole.VIDEO_DIRECTOR

    def test_video_line_producer(self):
        assert map_role("Video Line Producer") is CreditRole.VIDEO_PRODUCER

    def test_video_before_producer(self):
        assert map_role("Video Line Producer") is CreditRole.VIDEO_PRODUCER

    def test_co_producer(self):
        assert map_role("Co-Producer") is CreditRole.CO_PRODUCER

    def test_executive_producer_fuzzy(self):
        assert map_role("Executive Music Producer") is CreditRole.EXECUTIVE_PRODUCER

    def test_vocal_producer_fuzzy(self):
        assert map_role("Vocal Producer") is CreditRole.VOCAL_PRODUCER

    def test_assistant_mix_engineer(self):
        assert map_role("Assistant Mix Engineer") is CreditRole.MIXING_ENGINEER

    def test_lead_vocals(self):
        assert map_role("Lead Vocals") is CreditRole.LEAD_VOCALS

    def test_background_vocals(self):
        assert map_role("Backing Vocals") is CreditRole.BACKGROUND_VOCALS

    def test_bass_guitar(self):
        assert map_role("Bass Guitar") is CreditRole.BASS_GUITAR

    def test_unknown(self):
        assert map_role("Catering") is CreditRole.OTHER


class TestYouTubeSpotifyLabels:
    def test_vocalist(self):
        assert map_role("Vocalist") is CreditRole.VOCALS

    def test_music_by(self):
        assert map_role("Music By") is CreditRole.PRODUCER

    def test_produit_par(self):
        assert map_role("Produit par") is CreditRole.PRODUCER

    def test_ecrit_par(self):
        assert map_role("Écrit par") is CreditRole.WRITER

    def test_interprete_par(self):
        assert map_role("Interprété par") is CreditRole.VOCALS

    def test_paroles(self):
        assert map_role("Paroles") is CreditRole.LYRICIST

    def test_composition(self):
        assert map_role("Composition") is CreditRole.COMPOSER

    def test_source(self):
        assert map_role("Source") is CreditRole.LABEL

    def test_written_by(self):
        assert map_role("Written By") is CreditRole.WRITER

    def test_performed_by(self):
        assert map_role("Performed By") is CreditRole.VOCALS

    def test_produced_by(self):
        assert map_role("Produced By") is CreditRole.PRODUCER

    def test_mixed_by(self):
        assert map_role("Mixed By") is CreditRole.MIXING_ENGINEER

    def test_mastered_by(self):
        assert map_role("Mastered By") is CreditRole.MASTERING_ENGINEER

    def test_recorded_by(self):
        assert map_role("Recorded By") is CreditRole.RECORDING_ENGINEER
