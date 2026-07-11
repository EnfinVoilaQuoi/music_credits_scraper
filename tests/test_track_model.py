"""Tests du modèle Track (et Credit) — logique pure, sans base de données."""

from datetime import datetime

from src.models.track import Credit, CreditRole, Track


class TestCreditFromRoleAndNames:
    def test_role_connu(self):
        credits = Credit.from_role_and_names("Producer", ["Kore", "Katrina Squad"])
        assert len(credits) == 2
        assert all(c.role == CreditRole.PRODUCER for c in credits)
        assert credits[0].name == "Kore"
        assert credits[0].role_detail is None

    def test_role_insensible_a_la_casse(self):
        (credit,) = Credit.from_role_and_names("mixing engineer", ["X"])
        assert credit.role == CreditRole.MIXING_ENGINEER

    def test_role_inconnu_devient_other_avec_detail(self):
        (credit,) = Credit.from_role_and_names("Rôle Exotique", ["X"])
        assert credit.role == CreditRole.OTHER
        assert credit.role_detail == "Rôle Exotique"

    def test_noms_vides_filtres(self):
        credits = Credit.from_role_and_names("Producer", ["Kore", "  ", ""])
        assert [c.name for c in credits] == ["Kore"]


class TestAddCredit:
    def test_ajout_et_deduplication(self):
        track = Track(title="Test")
        c = Credit(name="Kore", role=CreditRole.PRODUCER)
        track.add_credit(c)
        track.add_credit(Credit(name="Kore", role=CreditRole.PRODUCER))
        assert len(track.credits) == 1

    def test_meme_nom_role_different_conserve(self):
        track = Track(title="Test")
        track.add_credit(Credit(name="Kore", role=CreditRole.PRODUCER))
        track.add_credit(Credit(name="Kore", role=CreditRole.WRITER))
        assert len(track.credits) == 2


class TestGetProducers:
    def test_regroupe_les_roles_de_production(self):
        track = Track(title="Test")
        track.add_credit(Credit(name="A", role=CreditRole.PRODUCER))
        track.add_credit(Credit(name="B", role=CreditRole.CO_PRODUCER))
        track.add_credit(Credit(name="C", role=CreditRole.WRITER))
        producers = track.get_producers()
        assert "A" in producers
        assert "B" in producers
        assert "C" not in producers


class TestUpdateReleaseDate:
    def test_pas_de_date_existante(self):
        track = Track(title="Test")
        assert track.update_release_date("2020-05-01") is True
        assert track.release_date == datetime(2020, 5, 1)

    def test_garde_la_plus_ancienne(self):
        # Un single sort AVANT l'album : la date la plus ancienne gagne
        track = Track(title="Test", release_date=datetime(2020, 5, 1))
        assert track.update_release_date("2019-03-01") is True
        assert track.release_date == datetime(2019, 3, 1)

    def test_date_plus_recente_ignoree(self):
        track = Track(title="Test", release_date=datetime(2020, 5, 1))
        assert track.update_release_date("2021-01-01") is False
        assert track.release_date == datetime(2020, 5, 1)

    def test_force_ecrase(self):
        track = Track(title="Test", release_date=datetime(2020, 5, 1))
        assert track.update_release_date("2021-01-01", force=True) is True
        assert track.release_date == datetime(2021, 1, 1)

    def test_format_iso_avec_heure(self):
        track = Track(title="Test")
        assert track.update_release_date("2020-05-01T12:30:00Z") is True
        assert track.release_date.year == 2020

    def test_chaine_invalide_refusee(self):
        track = Track(title="Test")
        assert track.update_release_date("pas une date") is False
        assert track.release_date is None

    def test_type_invalide_refuse(self):
        track = Track(title="Test")
        assert track.update_release_date(12345) is False


class TestSpotifyIds:
    def test_add_puis_legacy_rempli(self):
        track = Track(title="Test")
        assert track.add_spotify_id("abc123") is True
        assert track.spotify_id == "abc123"
        assert track.spotify_ids == ["abc123"]

    def test_doublon_refuse(self):
        track = Track(title="Test")
        track.add_spotify_id("abc123")
        assert track.add_spotify_id("abc123") is False
        assert track.spotify_ids == ["abc123"]

    def test_id_alternatif_accumule_sans_ecraser_le_principal(self):
        # Les désaccords entre sources sont CONSERVÉS, jamais arbitrés
        track = Track(title="Test")
        track.add_spotify_id("abc123")
        assert track.add_spotify_id("def456") is True
        assert track.spotify_id == "abc123"  # le principal ne bouge pas
        assert track.get_all_spotify_ids() == ["abc123", "def456"]

    def test_primary_prefere_la_liste(self):
        track = Track(title="Test", spotify_id="legacy", spotify_ids=["nouveau"])
        assert track.primary_spotify_id == "nouveau"

    def test_primary_fallback_legacy(self):
        track = Track(title="Test", spotify_id="legacy")
        assert track.primary_spotify_id == "legacy"

    def test_id_vide_refuse(self):
        track = Track(title="Test")
        assert track.add_spotify_id("") is False
        assert track.add_spotify_id(None) is False


class TestCertificationEmoji:
    def test_paliers_connus(self):
        track = Track(title="Test")
        assert track.get_certification_emoji("Or") == "🥇"
        assert track.get_certification_emoji("Diamant") == "💎"
        assert track.get_certification_emoji("Quadruple Diamant") == "💎💎💎💎"

    def test_sans_certification(self):
        track = Track(title="Test")
        assert track.get_certification_emoji() == ""

    def test_niveau_inconnu_trophee_generique(self):
        track = Track(title="Test")
        assert track.get_certification_emoji("Ruby") == "🏆"
