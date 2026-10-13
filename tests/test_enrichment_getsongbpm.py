"""Tests du provider GetSongBPM (src/enrichment/providers/getsongbpm) — sans réseau."""

import asyncio
from dataclasses import dataclass

import httpx
import pytest

from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.getsongbpm import GetSongBpmProvider
from src.models.artist import Artist
from src.models.track import Track


@dataclass
class _FakeSongData:
    bpm: int | None = None
    key: str | None = None
    mode: str | None = None
    time_signature: str | None = None
    error: str | None = None


class _FakeFetcher:
    def __init__(self, song_data):
        self._song_data = song_data
        self.calls = []

    async def fetch_track_bpm_async(self, http, artist, title):
        self.calls.append((artist, title))
        return self._song_data


def _enrich(provider, track, ctx):
    """La voie de production (async) — la voie sync est retirée (2026-10-13)."""
    return asyncio.run(provider.enrich_async(track, ctx))


def _track():
    # key/mode sont des attributs dynamiques posés par le mapper DB (pas des
    # champs de la dataclass) : on les initialise comme un track chargé depuis
    # la base, sinon le provider (fidèle à l'historique) lève AttributeError.
    track = Track(title="Solo", artist=Artist(name="Sofiane Pamart"))
    track.audio.key = None
    track.audio.mode = None
    return track


def test_is_available():
    assert GetSongBpmProvider(None).is_available() is False
    assert GetSongBpmProvider(_FakeFetcher(_FakeSongData())).is_available() is True


def test_bpm_devient_candidat_et_key_mode_observes():
    # E7 : plus de pose legacy directe — BPM au scrutin, key/mode en observations
    # PAR SOURCE normalisées (F#m → pitch class 6, "minor" → 0). apply_resolutions
    # pose les colonnes en fin de run (hors périmètre du provider).
    song = _FakeSongData(bpm=142, key="F#m", mode="minor")
    provider = GetSongBpmProvider(_FakeFetcher(song))
    track = _track()
    ctx = EnrichmentContext()
    assert _enrich(provider, track, ctx) is True
    assert ("getsongbpm", 142) in ctx.bpm_ballot.candidates
    keys = [o for o in ctx.observations if o.field == "key" and o.source == "getsongbpm"]
    modes = [o for o in ctx.observations if o.field == "mode" and o.source == "getsongbpm"]
    assert keys and keys[0].value == 6
    assert modes and modes[0].value == 0  # minor → 0


def test_erreur_api_renvoie_false():
    provider = GetSongBpmProvider(_FakeFetcher(_FakeSongData(error="not found")))
    assert _enrich(provider, _track(), EnrichmentContext()) is False


def test_indisponible_renvoie_false():
    assert _enrich(GetSongBpmProvider(None), _track(), EnrichmentContext()) is False


class _RaisingFetcher:
    def __init__(self, exc):
        self._exc = exc

    async def fetch_track_bpm_async(self, http, artist, title):
        raise self._exc


def test_erreur_inattendue_absorbee_mais_tracee(caplog):
    """Une exception hors domaine réseau (bug interne) est absorbée par le dernier
    ressort du provider (→ False) MAIS rendue visible : logger.exception émet un
    traceback (fini le debug muet qui masquait les bugs)."""
    provider = GetSongBpmProvider(_RaisingFetcher(RuntimeError("bug interne")))
    with caplog.at_level("ERROR"):
        assert _enrich(provider, _track(), EnrichmentContext()) is False
    trace_records = [r for r in caplog.records if r.exc_info and "GetSongBPM" in r.getMessage()]
    assert trace_records, "le dernier ressort doit émettre un traceback (logger.exception)"


def test_gate_ne_skip_jamais():
    # API gratuite/rapide : appelée systématiquement (2ᵉ vote BPM, §8.3)
    track = _track()
    track.audio.bpm = 120
    track.audio.key = 5
    track.audio.mode = 1
    ctx = EnrichmentContext()
    ctx.results["reccobeats"] = True  # même quand tout est déjà présent
    assert GetSongBpmProvider().gate(track, ctx) is None


@pytest.mark.parametrize(
    ("etat", "attendu"),
    [
        ({}, "2e_vote_bpm"),
        ({"force": True}, "force_update=True"),
        ({"sans_bpm": True}, "no_bpm"),
        ({"recco_ko": True}, "reccobeats_failed"),
        ({"sans_key": True}, "missing_data=key"),
        ({"sans_mode": True}, "missing_data=mode"),
    ],
)
def test_gate_journalise_un_motif_explicite(caplog, etat, attendu):
    """Le motif n'est QUE du log — mais un « (raison: ) » vide avait déjà été
    corrigé une fois (Phase F) : les six branches sont ici verrouillées."""
    track = _track()
    track.audio.bpm = None if etat.get("sans_bpm") else 120
    track.audio.key = None if etat.get("sans_key") else 5
    track.audio.mode = None if etat.get("sans_mode") else 1
    ctx = EnrichmentContext(force_update=bool(etat.get("force")))
    if etat.get("recco_ko"):
        ctx.results["reccobeats"] = False
    with caplog.at_level("INFO"):
        GetSongBpmProvider().gate(track, ctx)
    assert attendu in caplog.text


_FakeFetcherAsync = _FakeFetcher
_RaisingFetcherAsync = _RaisingFetcher


class TestApplicationDesDonnees:
    """`_apply_song_data` est commun aux deux voies : chaque champ mesuré vaut
    un SUCCÈS, même sans BPM (sinon faux échec dans le bilan du run —
    ancien nettoyage retiré le 2026-09-27)."""

    @pytest.mark.parametrize(
        "song",
        [
            _FakeSongData(bpm=142),
            _FakeSongData(key="F#m"),
            _FakeSongData(mode="minor"),
            _FakeSongData(time_signature="4/4"),
        ],
    )
    def test_un_seul_champ_mesure_suffit(self, song):
        assert (
            _enrich(GetSongBpmProvider(_FakeFetcher(song)), _track(), EnrichmentContext()) is True
        )

    def test_time_signature_devient_une_observation(self):
        ctx = EnrichmentContext()
        song = _FakeSongData(time_signature="3/4")
        _enrich(GetSongBpmProvider(_FakeFetcher(song)), _track(), ctx)
        ts = [o for o in ctx.observations if o.field == "time_signature"]
        assert ts and ts[0].value == "3/4" and ts[0].source == "getsongbpm"

    def test_reponse_entierement_vide_est_un_echec(self):
        p = GetSongBpmProvider(_FakeFetcher(_FakeSongData()))
        assert _enrich(p, _track(), EnrichmentContext()) is False

    def test_artiste_principal_si_featuring(self):
        fetcher = _FakeFetcher(_FakeSongData(bpm=142))
        track = _track()
        track.is_featuring = True
        track.primary_artist_name = "Principal"
        _enrich(GetSongBpmProvider(fetcher), track, EnrichmentContext())
        assert fetcher.calls[0][0] == "Principal"


class TestVoieAsync:
    """Le jumeau async partage `_apply_song_data` : ce sont les gardes AUTOUR
    (indisponibilité, frontière httpx, dernier ressort) qui doivent être
    vérifiées des deux côtés — c'est là que le défaut Musixmatch avait survécu."""

    def test_indisponible(self):
        p = GetSongBpmProvider(None)
        assert asyncio.run(p.enrich_async(_track(), EnrichmentContext())) is False

    def test_erreur_reseau_httpx(self):
        """La voie sync catche `requests.RequestException`, l'async `httpx.HTTPError` :
        deux familles distinctes, donc deux gardes à vérifier séparément."""
        p = GetSongBpmProvider(_RaisingFetcherAsync(httpx.ConnectError("réseau")))
        assert asyncio.run(p.enrich_async(_track(), EnrichmentContext())) is False

    def test_erreur_inattendue_tracee(self, caplog):
        p = GetSongBpmProvider(_RaisingFetcherAsync(RuntimeError("bug interne")))
        with caplog.at_level("ERROR"):
            assert asyncio.run(p.enrich_async(_track(), EnrichmentContext())) is False
        assert [r for r in caplog.records if r.exc_info], "traceback attendu"

    def test_erreur_api_renvoyee_dans_song_data(self):
        p = GetSongBpmProvider(_FakeFetcherAsync(_FakeSongData(error="not found")))
        assert asyncio.run(p.enrich_async(_track(), EnrichmentContext())) is False
