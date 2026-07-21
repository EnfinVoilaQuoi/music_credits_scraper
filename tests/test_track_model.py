"""Tests du modèle Track (et Credit) — logique pure, sans base de données."""

from datetime import datetime

from src.gui.formatters import certification_emoji
from src.models.artist import Artist
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


class TestToDict:
    """P0 AUDIT §3.2 — crash historique : Credit sans to_dict → export impossible
    dès qu'un morceau avait un crédit. (L'export lui-même sera retravaillé plus
    tard ; on ne fige ici QUE la non-régression du crash.)"""

    def test_to_dict_avec_credits_ne_crashe_pas(self):
        track = Track(title="Test")
        track.add_credit(Credit(name="Kore", role=CreditRole.PRODUCER))
        d = track.to_dict()
        assert d["all_credits"][0]["name"] == "Kore"
        assert d["total_credits_count"] == 1

    def test_to_dict_sans_credits(self):
        d = Track(title="Test").to_dict()
        assert d["all_credits"] == []
        assert d["title"] == "Test"


class TestTrackEquality:
    """Égalité MÉTIER : identité = genius_id si présent, sinon (titre, artiste).
    Corrige l'ancien add_track qui comparait tous les champs (lyrics incluses)."""

    def test_meme_genius_id_meme_morceau_malgre_champs_differents(self):
        # Une version re-scrapée (paroles, bpm mis à jour) reste LE même morceau
        a = Track(title="Chanson", genius_id=123)
        b = Track(title="Chanson (Remaster)", genius_id=123)
        a.lyrics.text, b.lyrics.text = "v1", "v2"
        a.audio.bpm, b.audio.bpm = 90, 140  # Phase 5 : audio/lyrics hors constructeur
        assert a == b
        assert hash(a) == hash(b)

    def test_genius_id_different_morceaux_distincts(self):
        a = Track(title="Chanson", genius_id=123)
        b = Track(title="Chanson", genius_id=456)
        assert a != b

    def test_sans_genius_id_titre_et_artiste(self):
        artist = Artist(name="Sofiane Pamart")
        a = Track(title="Solo", artist=artist)
        b = Track(title="Solo", artist=artist)
        assert a == b
        assert hash(a) == hash(b)

    def test_avec_genius_id_jamais_egal_a_sans(self):
        # Le discriminant garantit la cohérence __eq__/__hash__
        a = Track(title="Solo", genius_id=123)
        b = Track(title="Solo")
        assert a != b

    def test_add_track_dedup_par_identite_metier(self):
        artist = Artist(name="X")
        t1 = Track(title="Song", genius_id=999)
        t1.lyrics.text = "ancienne version"
        artist.add_track(t1)
        # Même genius_id, paroles différentes → pas de doublon
        t2 = Track(title="Song", genius_id=999)
        t2.lyrics.text = "nouvelle version"
        artist.add_track(t2)
        assert artist.get_tracks_count() == 1

    def test_pas_egal_a_un_non_track(self):
        assert Track(title="X", genius_id=1) != "pas un track"


class TestCertificationMilestoneDurations:
    def test_un_delai_par_palier_de_base_au_plus_tot(self):
        t = Track(title="X")
        t.release_date = "2020-01-01"
        t.certs.entries = [
            {"certification": "Diamant", "certification_date": "2023-01-01"},
            {"certification": "Platine", "certification_date": "2021-07-01"},
            {"certification": "Or", "certification_date": "2020-07-01"},
            {"certification": "Double Platine", "certification_date": "2022-01-01"},
            {"certification": "Platine", "certification_date": "2021-01-01"},  # + ancien
        ]
        result = dict(t.certification_milestone_durations())
        assert set(result) == {"Or", "Platine", "Diamant"}  # multiplicateurs exclus
        assert result["Or"] == (datetime(2020, 7, 1) - datetime(2020, 1, 1)).days
        # Palier présent deux fois → date la plus ANCIENNE retenue
        assert result["Platine"] == (datetime(2021, 1, 1) - datetime(2020, 1, 1)).days

    def test_sans_date_de_sortie_renvoie_vide(self):
        t = Track(title="X")
        t.certs.entries = [{"certification": "Or", "certification_date": "2020-07-01"}]
        assert t.certification_milestone_durations() == []


class TestCertificationEmoji:
    def test_paliers_connus(self):
        assert certification_emoji("Or") == "🥇"
        assert certification_emoji("Diamant") == "💎"
        assert certification_emoji("Quadruple Diamant") == "💎💎💎💎"

    def test_sans_certification(self):
        assert certification_emoji(None) == ""

    def test_niveau_inconnu_trophee_generique(self):
        assert certification_emoji("Ruby") == "🏆"
