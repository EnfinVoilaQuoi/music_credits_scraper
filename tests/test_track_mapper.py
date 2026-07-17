"""Tests du mapper ligne DB → Track (src/utils/track_mapper.py).

Le mapper est le seul habitat légal des coercitions de types depuis la base
(durées MM:SS, littéraux 'None'/'NULL', JSON certifications). On teste ici les
helpers purs + track_from_row avec de vraies sqlite3.Row (fidèles à la prod).
"""

import json
import sqlite3

import pytest

from src.models import Artist
from src.utils.track_mapper import (
    _clean,
    _clean_duration,
    _clean_int,
    track_from_row,
)

# Colonnes lues par track_from_row (surensemble suffisant : SELECT * en prod).
_COLUMNS = [
    "id",
    "title",
    "album",
    "track_number",
    "release_date",
    "genius_id",
    "spotify_id",
    "discogs_id",
    "isrc",
    "bpm",
    "bpm_source",
    "bpm_confidence",
    "key_mode_source",
    "reccobeats_resolution",
    "bpm_alt",
    "lyrics_source",
    "lyrics_synced",
    "lyrics_synced_source",
    "lyrics_synced_confidence",
    "youtube_url",
    "youtube_url_source",
    "spotify_streams",
    "spotify_daily_streams",
    "spotify_streams_updated",
    "ytm_streams",
    "ytm_streams_updated",
    "album_override",
    "cover_path",
    "yt_thumbnail_path",
    "youtube_video_kind",
    "youtube_video_views",
    "youtube_video_views_updated",
    "relationships",
    "duration",
    "genre",
    "key",
    "mode",
    "musical_key",
    "time_signature",
    "genius_url",
    "spotify_url",
    "spotify_page_title",
    "created_at",
    "updated_at",
    "last_scraped",
    "is_featuring",
    "primary_artist_name",
    "featured_artists",
    "secondary_role",
    "lyrics",
    "anecdotes",
    "has_lyrics",
    "lyrics_scraped_at",
    "certifications",
    "album_certifications",
]


def make_row(**overrides) -> sqlite3.Row:
    """Construit une vraie sqlite3.Row (toutes colonnes à None sauf overrides)."""
    values = {col: None for col in _COLUMNS}
    values.update(overrides)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cols_sql = ", ".join(f'"{c}"' for c in _COLUMNS)
    placeholders = ", ".join(f":{c}" for c in _COLUMNS)
    conn.execute(f"CREATE TABLE tracks ({cols_sql})")
    conn.execute(f"INSERT INTO tracks ({cols_sql}) VALUES ({placeholders})", values)
    row = conn.execute("SELECT * FROM tracks").fetchone()
    conn.close()
    return row


@pytest.fixture
def artist():
    return Artist(id=1, name="Isha")


class TestClean:
    def test_valeur_reelle_inchangee(self):
        assert _clean("abc") == "abc"

    @pytest.mark.parametrize("empty", [None, "None", "NULL", ""])
    def test_litteraux_vides_deviennent_default(self, empty):
        assert _clean(empty) is None
        assert _clean(empty, default="x") == "x"


class TestCleanInt:
    def test_conversion_int(self):
        assert _clean_int("142") == 142
        assert _clean_int(142) == 142

    def test_non_convertible_defaut(self):
        assert _clean_int("abc") is None

    def test_allow_string_conserve_la_chaine(self):
        # key/mode : "G" / "major" non convertibles → conservés tels quels
        assert _clean_int("G", allow_string=True) == "G"
        assert _clean_int("major", allow_string=True) == "major"

    def test_allow_string_int_reste_int(self):
        assert _clean_int("7", allow_string=True) == 7

    @pytest.mark.parametrize("empty", [None, "None", "NULL", ""])
    def test_litteraux_vides(self, empty):
        assert _clean_int(empty) is None


class TestCleanDuration:
    def test_mm_ss(self):
        assert _clean_duration("3:48") == 228

    def test_int_passthrough(self):
        assert _clean_duration(228) == 228

    def test_string_numerique(self):
        assert _clean_duration("180") == 180

    def test_invalide_defaut(self):
        assert _clean_duration("bloup") is None

    @pytest.mark.parametrize("empty", [None, "None", "NULL", ""])
    def test_litteraux_vides(self, empty):
        assert _clean_duration(empty) is None


class TestTrackFromRow:
    def test_ligne_sans_id_renvoie_none(self, artist):
        assert track_from_row(make_row(id=None, title="X"), artist) is None

    def test_ligne_sans_titre_renvoie_none(self, artist):
        assert track_from_row(make_row(id=1, title=None), artist) is None

    def test_titre_litteral_null_renvoie_none(self, artist):
        assert track_from_row(make_row(id=1, title="NULL"), artist) is None

    def test_champs_de_base(self, artist):
        row = make_row(id=5, title="  Matrix  ", spotify_id="abc", bpm="142")
        track = track_from_row(row, artist)
        assert track.id == 5
        assert track.title == "Matrix"  # strip appliqué
        assert track.artist is artist
        assert track.spotify_id == "abc"
        assert track.bpm == 142  # coercition str → int

    def test_duree_mm_ss(self, artist):
        track = track_from_row(make_row(id=1, title="X", duration="3:48"), artist)
        assert track.duration == 228

    def test_is_featuring_bool(self, artist):
        track = track_from_row(make_row(id=1, title="X", is_featuring=1), artist)
        assert track.is_featuring is True
        track2 = track_from_row(make_row(id=1, title="X", is_featuring=None), artist)
        assert track2.is_featuring is False

    def test_relationships_json(self, artist):
        rels = [{"type": "sample", "title": "Foo"}]
        track = track_from_row(make_row(id=1, title="X", relationships=json.dumps(rels)), artist)
        assert track.relationships == rels

    def test_relationships_json_invalide_liste_vide(self, artist):
        track = track_from_row(make_row(id=1, title="X", relationships="{pas du json"), artist)
        assert track.relationships == []

    def test_certifications_json_et_backcompat(self, artist):
        certs = [{"certification": "Or", "certification_date": "2020-01-01"}]
        track = track_from_row(make_row(id=1, title="X", certifications=json.dumps(certs)), artist)
        assert track.certifications == certs
        assert track.has_certification is True
        assert track.certification_level == "Or"
        assert track.certification_date == "2020-01-01"

    def test_certifications_absentes_liste_vide(self, artist):
        track = track_from_row(make_row(id=1, title="X"), artist)
        assert track.certifications == []
        assert track.has_certification is False

    def test_key_mode_string_conserves(self, artist):
        track = track_from_row(make_row(id=1, title="X", key="G", mode="major"), artist)
        assert track.key == "G"
        assert track.mode == "major"

    def test_musical_key_recalcule_depuis_key_mode(self, artist):
        # key/mode int présents, musical_key absente → recalcul FR
        track = track_from_row(make_row(id=1, title="X", key=7, mode=1), artist)
        assert track.musical_key  # non vide (calculé par music_theory)

    def test_champs_media(self, artist):
        # Chantier « Media » : chemins d'images + métadonnées vidéo (round-trip).
        row = make_row(
            id=1,
            title="X",
            cover_path="covers/Jul - C'est pas des LOL.jpg",
            yt_thumbnail_path="vignettes/abc123DEF45.jpg",
            youtube_video_kind="clip",
            youtube_video_views="123456",
            youtube_video_views_updated="2026-07-18 10:00:00",
        )
        track = track_from_row(row, artist)
        assert track.cover_path == "covers/Jul - C'est pas des LOL.jpg"
        assert track.yt_thumbnail_path == "vignettes/abc123DEF45.jpg"
        assert track.youtube_video_kind == "clip"
        assert track.youtube_video_views == 123456  # coercition str → int
        assert track.youtube_video_views_updated == "2026-07-18 10:00:00"  # brut (TIMESTAMP)


class TestObservationsOverride:
    """E6 : les observations réconciliées pilotent bpm/key/mode (fallback legacy)."""

    def _obs(self, field, value, source, confidence=None):
        from src.enrichment.observation import Observation

        return Observation(field=field, value=value, source=source, confidence=confidence)

    def test_observations_pilotent_bpm(self, artist):
        # Legacy bpm=100 écrasé par le vote des observations (2 sources à 142).
        row = make_row(id=1, title="X", bpm="100", bpm_source="legacy")
        obs = [self._obs("bpm", "142", "reccobeats"), self._obs("bpm", "142", "songbpm")]
        track = track_from_row(row, artist, obs)
        assert track.bpm == 142
        assert track.bpm_source == "reccobeats+songbpm"
        assert track.bpm_confidence == 2

    def test_valeurs_texte_persistees_coercees(self, artist):
        # value en TEXT (relue de la DB) → int + musical_key recalculée.
        obs = [
            self._obs("key", "8", "reccobeats"),
            self._obs("mode", "0", "reccobeats"),
            self._obs("key", "8", "songbpm"),
            self._obs("mode", "0", "songbpm"),
        ]
        track = track_from_row(make_row(id=1, title="X"), artist, obs)
        assert track.key == 8
        assert track.mode == 0
        assert track.musical_key == "Sol#/Lab mineur"

    def test_sans_observation_fallback_legacy(self, artist):
        row = make_row(id=1, title="X", bpm="120", key="7", mode="1")
        track = track_from_row(row, artist, [])  # aucune observation
        assert track.bpm == 120
        assert track.key == 7

    def test_bpm_observe_key_mode_en_fallback(self, artist):
        # bpm observé (piloté) ; key/mode SANS observation → colonnes legacy gardées.
        row = make_row(id=1, title="X", bpm="100", key="5", mode="1")
        track = track_from_row(row, artist, [self._obs("bpm", "140", "deezer")])
        assert track.bpm == 140  # observé
        assert track.key == 5  # fallback legacy
        assert track.mode == 1

    def test_manual_survit_aux_observations_concurrentes(self, artist):
        # E7a : une saisie manuelle (obs `manual`, value TEXT relue) doit primer
        # sur des sources auto concurrentes à la relecture — sinon écrasement.
        row = make_row(id=1, title="X", bpm="100")
        obs = [
            self._obs("bpm", "140", "deezer"),
            self._obs("bpm", "140", "reccobeats"),
            self._obs("bpm", "95", "manual"),
            self._obs("key", "8", "reccobeats"),
            self._obs("mode", "1", "reccobeats"),
            self._obs("key", "2", "manual"),
            self._obs("mode", "0", "manual"),
        ]
        track = track_from_row(row, artist, obs)
        assert track.bpm == 95
        assert track.bpm_source == "manual"
        assert track.key == 2
        assert track.mode == 0
        assert track.key_mode_source == "manual"
