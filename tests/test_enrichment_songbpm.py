"""Tests du provider SongBPM (src/enrichment/providers/songbpm) — sans réseau."""

from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.songbpm import SongBpmProvider
from src.models.artist import Artist
from src.models.track import Track


class _FakeScraper:
    def __init__(self, data):
        self._data = data
        self.calls = []

    def search_track(self, title, artist, spotify_id=None, fetch_details=False):
        self.calls.append((title, artist, spotify_id, fetch_details))
        return self._data


def _track():
    return Track(title="Solo", artist=Artist(name="Sofiane Pamart"))


def test_is_available():
    assert SongBpmProvider(None).is_available() is False
    assert SongBpmProvider(_FakeScraper({})).is_available() is True


def test_bpm_candidat_key_mode_duration():
    # E7 : BPM au scrutin, key/mode en observations PAR SOURCE (plus de pose
    # legacy directe). Duration reste une colonne (posée telle quelle).
    data = {"bpm": 90, "key": 5, "mode": 1, "duration": 200}
    provider = SongBpmProvider(_FakeScraper(data))
    track = _track()
    ctx = EnrichmentContext()
    assert provider.enrich(track, ctx) is True
    assert ("songbpm", 90) in ctx.bpm_ballot.candidates
    keys = [o for o in ctx.observations if o.field == "key" and o.source == "songbpm"]
    modes = [o for o in ctx.observations if o.field == "mode" and o.source == "songbpm"]
    assert keys and keys[0].value == 5
    assert modes and modes[0].value == 1
    assert track.duration == 200


def test_candidat_bpm_compte_comme_succes_meme_si_bpm_deja_present():
    # Régression : SongBPM confirme un BPM déjà présent → SUCCÈS (2ᵉ vote),
    # plus « ÉCHEC » à tort. Le candidat rejoint le scrutin.
    provider = SongBpmProvider(_FakeScraper({"bpm": 146}))
    track = _track()
    track.audio.bpm = 146  # déjà renseigné
    ctx = EnrichmentContext()
    assert provider.enrich(track, ctx) is True
    assert ("songbpm", 146) in ctx.bpm_ballot.candidates


def test_scraper_vide_renvoie_false():
    provider = SongBpmProvider(_FakeScraper(None))
    assert provider.enrich(_track(), EnrichmentContext()) is False


# ──────────────────────────────────────────────────────────────────────
# gate() — DÉPARTAGE : skip seulement si consensus BPM ET rien de manquant
# ──────────────────────────────────────────────────────────────────────


def _track_complet():
    track = _track()
    track.audio.key = 5
    track.audio.mode = 1
    track.duration = 200
    return track


def test_gate_skip_si_consensus_et_donnees_completes():
    ctx = EnrichmentContext()
    ctx.bpm_ballot.add("reccobeats", 100)
    ctx.bpm_ballot.add("getsongbpm", 100)  # 2 candidats concordants = consensus
    assert SongBpmProvider().gate(_track_complet(), ctx) == "not_needed"


def test_gate_execute_sans_consensus():
    ctx = EnrichmentContext()
    ctx.bpm_ballot.add("reccobeats", 100)  # 1 seul candidat : pas de consensus
    assert SongBpmProvider().gate(_track_complet(), ctx) is None


def test_gate_execute_si_donnee_manquante_malgre_consensus():
    ctx = EnrichmentContext()
    ctx.bpm_ballot.add("reccobeats", 100)
    ctx.bpm_ballot.add("getsongbpm", 100)
    track = _track_complet()
    track.duration = None
    assert SongBpmProvider().gate(track, ctx) is None


def test_gate_execute_si_force_update():
    ctx = EnrichmentContext(force_update=True)
    ctx.bpm_ballot.add("reccobeats", 100)
    ctx.bpm_ballot.add("getsongbpm", 100)
    assert SongBpmProvider().gate(_track_complet(), ctx) is None


def test_error_result_none_pour_crash():
    # Crash/timeout ≠ « pas de données » : la valeur d'erreur est None, pas False
    assert SongBpmProvider.error_result is None


def test_spotify_id_de_songbpm_rejete_si_duplicata():
    # Un validateur qui refuse tout : l'ID trouvé par SongBPM n'est PAS posé
    data = {"spotify_id": "dup123"}
    provider = SongBpmProvider(_FakeScraper(data))
    track = _track()
    other = Track(title="Autre", artist=track.artist)
    ctx = EnrichmentContext(
        artist_tracks=[other],
        validate_spotify_id_unique=lambda sid, t, tracks: False,
    )
    provider.enrich(track, ctx)
    assert track.spotify_id is None


def test_indisponible_renvoie_false():
    assert SongBpmProvider(None).enrich(_track(), EnrichmentContext()) is False
