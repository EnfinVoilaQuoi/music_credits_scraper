"""Lot 0 durée (2026-09-22) : les champs de discographie sont ARBITRÉS À
L'ÉCRITURE, comme les streams — l'ordre des sources n'a aucun effet sur la
colonne, `manual` court-circuite, une purge ré-arbitre sur ce qui reste.

Fixture `data_manager` (base tmp, schéma Alembic à jour), jamais de réseau.
"""

from sqlalchemy import text

from src.enrichment.observation import Observation
from src.enrichment.reconcile import Resolution, apply_resolutions
from src.models import Artist, Track


def _artist(dm):
    artist = Artist(name="Isha", deezer_id=1236609)
    artist.id = dm.save_artist(artist)
    return artist


def _track(dm, artist, **kwargs):
    track = Track(title="Durag", artist=artist, **kwargs)
    track.id = dm.save_track(track)
    return track


def _colonnes(dm, track_id):
    with dm.engine.connect() as conn:
        return (
            conn.execute(
                text(
                    "SELECT duration, isrc, release_date, deezer_id, deezer_url FROM tracks WHERE id = :t"
                ),
                {"t": track_id},
            )
            .mappings()
            .one()
        )


class TestRecordDiscographyObservations:
    def test_l_ordre_des_sources_ne_change_pas_la_colonne(self, data_manager):
        artist = _artist(data_manager)
        a = _track(data_manager, artist)
        data_manager.record_duration_observation(a.id, 200, "songbpm")
        data_manager.record_duration_observation(a.id, 203, "deezer")
        b = Track(title="Karma", artist=artist)
        b.id = data_manager.save_track(b)
        data_manager.record_duration_observation(b.id, 203, "deezer")
        data_manager.record_duration_observation(b.id, 200, "songbpm")
        assert _colonnes(data_manager, a.id)["duration"] == 203
        assert _colonnes(data_manager, b.id)["duration"] == 203

    def test_manual_court_circuite(self, data_manager):
        t = _track(data_manager, _artist(data_manager))
        data_manager.record_duration_observation(t.id, 190, "manual")
        data_manager.record_duration_observation(t.id, 203, "deezer")
        assert _colonnes(data_manager, t.id)["duration"] == 190

    def test_legacy_seule_reste_puis_s_efface(self, data_manager):
        t = _track(data_manager, _artist(data_manager))
        data_manager.record_duration_observation(t.id, 250, "legacy")
        assert _colonnes(data_manager, t.id)["duration"] == 250
        data_manager.record_duration_observation(t.id, 203, "spotify_web")
        assert _colonnes(data_manager, t.id)["duration"] == 203

    def test_une_duree_illisible_n_est_pas_ecrite(self, data_manager):
        t = _track(data_manager, _artist(data_manager))
        assert data_manager.record_duration_observation(t.id, "n/a", "ytmusic") is False
        assert _colonnes(data_manager, t.id)["duration"] is None

    def test_isrc_et_date_sont_arbitres_aussi(self, data_manager):
        t = _track(data_manager, _artist(data_manager))
        valeurs = data_manager.record_discography_observations(
            t.id,
            [
                Observation("isrc", "FRXXX0000001", "deezer"),
                Observation("release_date", "2019-05-03", "deezer"),
                Observation("bpm", 120, "deezer"),  # hors périmètre : ignorée
            ],
        )
        assert valeurs == {"isrc": "FRXXX0000001", "release_date": "2019-05-03"}
        col = _colonnes(data_manager, t.id)
        assert col["isrc"] == "FRXXX0000001" and str(col["release_date"])[:10] == "2019-05-03"


class TestSaveTrackRearbitre:
    def test_save_track_rearbitre_sur_l_union_et_met_l_objet_en_phase(self, data_manager):
        t = _track(data_manager, _artist(data_manager))
        data_manager.record_duration_observation(t.id, 203, "deezer")
        # Un flux tenant l'objet arrive avec une durée songbpm fraîche.
        t.duration = 200
        t.observations = [Observation("duration", 200, "songbpm")]
        data_manager.save_track(t)
        assert _colonnes(data_manager, t.id)["duration"] == 203
        assert t.duration == 203  # l'objet suit le verdict, pas sa valeur fraîche

    def test_sans_observation_de_discographie_rien_ne_bouge(self, data_manager):
        t = _track(data_manager, _artist(data_manager), duration=180)
        t.observations = [Observation("bpm", 90, "songbpm")]
        data_manager.save_track(t)
        assert _colonnes(data_manager, t.id)["duration"] == 180 and t.duration == 180


class TestFillTrackIdentities:
    def test_remplit_sans_jamais_remplacer(self, data_manager):
        t = _track(data_manager, _artist(data_manager), isrc="FRAAA0000001")
        ecrit = data_manager.fill_track_identities(
            t.id, deezer_id=42, deezer_url="https://deezer.com/track/42", isrc="FRBBB0000002"
        )
        assert ecrit == {"deezer_id": True, "deezer_url": True}
        col = _colonnes(data_manager, t.id)
        assert col["deezer_id"] == 42 and col["isrc"] == "FRAAA0000001"
        assert data_manager.fill_track_identities(t.id, deezer_id=43) == {}
        assert _colonnes(data_manager, t.id)["deezer_id"] == 42


class TestClearTrackDeezerId:
    def _fiche_contaminee(self, dm):
        artist = _artist(dm)
        t = _track(dm, artist, album="Labrador bleu")
        dm.fill_track_identities(t.id, deezer_id=900, deezer_url="u", isrc="FRDZ0000001")
        dm.record_duration_observation(t.id, 250, "legacy")
        dm.record_discography_observations(
            t.id,
            [
                Observation("duration", 109, "deezer"),
                Observation("isrc", "FRDZ0000001", "deezer"),
                Observation("release_date", "2021-01-01", "deezer"),
            ],
        )
        dm.upsert_observations(t.id, [Observation("bpm", 120, "deezer")])
        return artist, t

    def test_les_trois_gestes(self, data_manager):
        artist, t = self._fiche_contaminee(data_manager)
        assert _colonnes(data_manager, t.id)["duration"] == 109
        rapport = data_manager.clear_track_deezer_id(t.id)
        assert rapport["id_retire"] == 900 and rapport["colonne_effacee"]
        assert sorted(f for f, _ in rapport["observations_retirees"]) == [
            "bpm",
            "duration",
            "isrc",
            "release_date",
        ]
        assert rapport["isrc_efface"] and rapport["release_date_effacee"]
        col = _colonnes(data_manager, t.id)
        assert col["deezer_id"] is None and col["deezer_url"] is None
        assert col["isrc"] is None and col["release_date"] is None
        assert col["duration"] == 250  # la legacy reprend la colonne
        # Rejeu : plus rien à retirer.
        assert data_manager.clear_track_deezer_id(t.id)["id_retire"] is None

    def test_une_isrc_venue_d_ailleurs_est_gardee(self, data_manager):
        artist = _artist(data_manager)
        # L'ISRC vient d'ailleurs ; Deezer n'a déclaré qu'une durée (le lot 2
        # n'émet jamais d'observation ISRC divergente — elle remplacerait la
        # colonne d'identité à l'arbitrage, deux vérités pour un même fait).
        t = _track(data_manager, artist, isrc="FRAUTRE00001")
        data_manager.fill_track_identities(t.id, deezer_id=900)
        data_manager.record_discography_observations(t.id, [Observation("duration", 109, "deezer")])
        rapport = data_manager.clear_track_deezer_id(t.id, 900)
        assert not rapport["isrc_efface"]
        assert _colonnes(data_manager, t.id)["isrc"] == "FRAUTRE00001"

    def test_delie_la_parution_prouvee_par_la_piste_sauf_le_repere(self, data_manager):
        from src.models import ReleaseObservation

        artist, t = self._fiche_contaminee(data_manager)
        data_manager.record_release_observations(
            t.id,
            [
                ReleaseObservation(
                    title="Compilation",
                    source="deezer",
                    external_release_id=5,
                    external_track_id=900,
                    scope="appearance",
                    confidence="deezer_id",
                ),
                ReleaseObservation(
                    title="Labrador bleu",
                    source="deezer",
                    external_release_id=6,
                    external_track_id=900,
                    confidence="deezer_id",
                ),
            ],
        )
        assert len(data_manager.get_track_releases(t.id)) == 2
        rapport = data_manager.clear_track_deezer_id(t.id)
        assert rapport["liens_parution_retires"] == 1
        assert rapport["parution_reperee"] == "Labrador bleu"
        assert [r["title"] for r in data_manager.get_track_releases(t.id)] == ["Labrador bleu"]


def test_apply_resolutions_pose_duration_source():
    t = Track(title="X")
    apply_resolutions(t, {"duration": Resolution("duration", "203", "songbpm")})
    assert t.duration == 203 and t.duration_source == "songbpm"
