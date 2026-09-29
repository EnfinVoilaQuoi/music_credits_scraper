"""Tests du provider ReccoBeats (src/enrichment/providers/reccobeats) — sans réseau.

Deux voies : ISRC (try_by_isrc) et Spotify ID (enrich). Clients mockés.
"""

import pytest

from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.reccobeats import ReccoBeatsProvider
from src.models.artist import Artist
from src.models.track import Track


class _FakeReccoClient:
    def __init__(self, isrc_info=None, track_info=None):
        self._isrc_info = isrc_info
        self._track_info = track_info

    def get_track_info_by_isrc(self, isrc):
        return self._isrc_info

    def get_track_info(self, spotify_id):
        return self._track_info


class _FakeSpotifyScraper:
    def __init__(self, spotify_id=None, page_title=None, identite=None):
        self._id = spotify_id
        self._title = page_title
        self._identite = identite

    def get_spotify_id(self, artist, title):
        return self._id

    def get_spotify_page_title(self, spotify_id):
        return self._title

    def get_track_identity(self, spotify_id):
        """Oracle d'identité. `None` = « on ne conclut pas » (défaut hermétique)."""
        return self._identite


def _track():
    return Track(title="Solo", artist=Artist(name="Sofiane Pamart"))


def test_is_available():
    assert ReccoBeatsProvider(None).is_available() is False
    assert ReccoBeatsProvider(_FakeReccoClient()).is_available() is True


def test_try_by_isrc_applique_bpm_et_resolution():
    client = _FakeReccoClient(isrc_info={"success": True, "bpm": 120, "key": 5, "mode": 1})
    provider = ReccoBeatsProvider(client)
    track = _track()
    track.isrc = "FRX9820001"
    ctx = EnrichmentContext()
    assert provider.try_by_isrc(track, ctx) is True
    assert ("reccobeats", 120) in ctx.bpm_ballot.candidates
    assert track.audio.reccobeats_resolution == "isrc"


def test_try_by_isrc_sans_isrc_renvoie_false():
    provider = ReccoBeatsProvider(_FakeReccoClient())
    assert provider.try_by_isrc(_track(), EnrichmentContext()) is False


def test_gate_skip_avec_resultat_true_si_isrc_satisfaite():
    # La voie ISRC (pré-étape) a déjà satisfait la source : pas de second appel,
    # et le résultat posé dans le dict est True (valeur historique)
    ctx = EnrichmentContext(isrc_satisfied=True)
    assert ReccoBeatsProvider().gate(_track(), ctx) is True


def test_gate_execute_sinon():
    assert ReccoBeatsProvider().gate(_track(), EnrichmentContext()) is None


def test_enrich_par_spotify_id_existant_valide():
    client = _FakeReccoClient(
        track_info={"success": True, "bpm": 140, "key": 7, "mode": 0, "duration": 200}
    )
    provider = ReccoBeatsProvider(client)
    track = _track()
    track.spotify_id = "spot123"
    ctx = EnrichmentContext(
        artist_tracks=[Track(title="Autre")],
        validate_spotify_id_unique=lambda sid, t, tracks: True,
    )
    assert provider.enrich(track, ctx) is True
    assert ("reccobeats", 140) in ctx.bpm_ballot.candidates
    assert track.audio.reccobeats_resolution == "spotify_id"
    assert track.duration == 200


def test_enrich_sans_id_ne_cherche_rien_et_renvoie_false():
    """Depuis le 2026-09-28 ReccoBeats CONSOMME l'ID posé par l'étape Identité :
    sans ID, il ne cherche pas (plus de repli scraper) et rend un échec."""
    assert ReccoBeatsProvider(_FakeReccoClient()).enrich(_track(), EnrichmentContext()) is False


def test_try_by_isrc_emet_observation_provenance():
    """La provenance ReccoBeats est émise en observation → persistée (survit au reload)."""
    from src.enrichment.observation import Observation

    client = _FakeReccoClient(isrc_info={"success": True, "bpm": 120})
    provider = ReccoBeatsProvider(client)
    track = _track()
    track.isrc = "FRX9820001"
    ctx = EnrichmentContext()
    provider.try_by_isrc(track, ctx)
    assert Observation("reccobeats_resolution", "isrc", "reccobeats") in ctx.observations


def test_reccobeats_resolution_repose_au_chargement():
    """apply_resolutions repose reccobeats_resolution depuis son observation (reload)."""
    from src.enrichment.observation import Observation
    from src.enrichment.reconcile import apply_resolutions, reconcile

    track = _track()
    track.audio.reccobeats_resolution = None  # état « rechargé » par le mapper (colonne droppée)
    obs = [Observation("reccobeats_resolution", "spotify_id", "reccobeats")]
    apply_resolutions(track, reconcile(obs))
    assert track.audio.reccobeats_resolution == "spotify_id"


# ── Voie ASYNC (F2/F3b) ───────────────────────────────────────────────────────
#
# Les jumeaux async partagent l'APPLICATION du résultat (`_apply_result`,
# `_apply_spotify_info`) avec la voie sync : seuls les appels réseau changent.
# Ce qu'on vérifie ici, c'est que la voie async aboutit au MÊME état de track
# depuis les mêmes payloads — et qu'elle sait retomber sur le pont sync quand
# aucun scraper async n'est configuré.

import asyncio  # noqa: E402


class _FakeReccoClientAsync:
    def __init__(self, isrc_info=None, track_info=None, leve=None):
        self._isrc_info = isrc_info
        self._track_info = track_info
        self._leve = leve

    async def get_track_info_by_isrc_async(self, http, isrc):
        if self._leve:
            raise self._leve
        return self._isrc_info

    async def get_track_info_async(self, http, spotify_id):
        if self._leve:
            raise self._leve
        return self._track_info


class _FakeDeezerAsync:
    def __init__(self, isrc=None):
        self._isrc = isrc

    async def get_isrc_async(self, http, artist, title):
        return self._isrc


class _FakeScraperAsync:
    def __init__(self, spotify_id=None, page_title=None, identite=None):
        self._id = spotify_id
        self._title = page_title
        self._identite = identite

    async def get_spotify_id_async(self, artist, title):
        return self._id

    async def get_spotify_page_title_async(self, spotify_id):
        return self._title

    async def get_track_identity_async(self, spotify_id):
        """Miroir async de l'oracle d'identité."""
        return self._identite


class _SyncRunner:
    """Pont sync du run : exécute le bloc bloquant tel quel."""

    def __init__(self):
        self.appels = 0

    async def run(self, fn, *args):
        self.appels += 1
        return fn(*args)


def test_try_by_isrc_async_applique_le_meme_resultat_que_le_sync():
    payload = {"success": True, "bpm": 120, "key": 5, "mode": 1}
    track_sync, track_async = _track(), _track()
    track_sync.isrc = track_async.isrc = "FRX9820001"

    ctx_sync, ctx_async = EnrichmentContext(), EnrichmentContext()
    ReccoBeatsProvider(_FakeReccoClient(isrc_info=payload)).try_by_isrc(track_sync, ctx_sync)
    asyncio.run(
        ReccoBeatsProvider(_FakeReccoClientAsync(isrc_info=payload)).try_by_isrc_async(
            track_async, ctx_async
        )
    )
    assert ctx_async.bpm_ballot.candidates == ctx_sync.bpm_ballot.candidates
    assert track_async.audio.reccobeats_resolution == track_sync.audio.reccobeats_resolution


def test_try_by_isrc_async_recupere_l_isrc_via_deezer():
    """Sans ISRC en base, Deezer le fournit — c'est ce qui évite un scrape
    Spotify pour ce morceau."""
    provider = ReccoBeatsProvider(
        _FakeReccoClientAsync(isrc_info={"success": True, "bpm": 90}),
        deezer_client=_FakeDeezerAsync(isrc="FRX9820001"),
    )
    track = _track()
    assert asyncio.run(provider.try_by_isrc_async(track, EnrichmentContext())) is True
    assert track.isrc == "FRX9820001"


def test_try_by_isrc_async_sans_isrc_ni_deezer():
    provider = ReccoBeatsProvider(_FakeReccoClientAsync())
    assert asyncio.run(provider.try_by_isrc_async(_track(), EnrichmentContext())) is False


def test_try_by_isrc_async_api_en_erreur():
    provider = ReccoBeatsProvider(_FakeReccoClientAsync(leve=KeyError("payload inattendu")))
    track = _track()
    track.isrc = "FRX9820001"
    assert asyncio.run(provider.try_by_isrc_async(track, EnrichmentContext())) is False


def test_try_by_isrc_async_sans_client():
    assert (
        asyncio.run(ReccoBeatsProvider(None).try_by_isrc_async(_track(), EnrichmentContext()))
        is False
    )


def test_enrich_async_avec_id_existant():
    """Même contexte que la voie sync : sans `artist_tracks` NI validateur
    d'unicité, un ID existant est considéré comme un doublon et re-scrapé
    (comportement historique, cf. `_existing_spotify_id`)."""
    provider = ReccoBeatsProvider(
        _FakeReccoClientAsync(track_info={"success": True, "bpm": 140, "key": 0, "mode": 1})
    )
    track = _track()
    track.spotify_id = "spot123"
    ctx = EnrichmentContext(
        artist_tracks=[Track(title="Autre")],
        validate_spotify_id_unique=lambda sid, t, tracks: True,
    )
    assert asyncio.run(provider.enrich_async(track, ctx)) is True
    assert ("reccobeats", 140) in ctx.bpm_ballot.candidates


def test_enrich_async_id_duplique_est_ignore_sans_rescrape():
    """Un ID existant non validé est ignoré ; plus de re-scrape ici — même règle
    des deux côtés."""
    provider = ReccoBeatsProvider(_FakeReccoClientAsync(track_info={"success": True, "bpm": 100}))
    track = _track()
    track.spotify_id = "DUPLIQUE"
    ctx = EnrichmentContext(
        artist_tracks=[Track(title="Autre")],
        validate_spotify_id_unique=lambda *a: False,
        sync_runner=_SyncRunner(),
    )
    assert asyncio.run(provider.enrich_async(track, ctx)) is False


def test_enrich_async_sans_id_disponible():
    provider = ReccoBeatsProvider(_FakeReccoClientAsync())
    ctx = EnrichmentContext(sync_runner=_SyncRunner())
    assert asyncio.run(provider.enrich_async(_track(), ctx)) is False


def test_enrich_async_sans_client():
    assert (
        asyncio.run(ReccoBeatsProvider(None).enrich_async(_track(), EnrichmentContext())) is False
    )


def test_enrich_async_api_en_erreur():
    provider = ReccoBeatsProvider(_FakeReccoClientAsync(leve=TypeError("réponse illisible")))
    track = _track()
    track.spotify_id = "4cOdK2wGLETKBW3PvgPWqT"
    assert asyncio.run(provider.enrich_async(track, EnrichmentContext())) is False


# ──────────────────────────────────────────────────────────────────────
# Application des données (étape 2) — commune aux deux voies
# ──────────────────────────────────────────────────────────────────────


def _avec_id(spotify_id="4cOdK2wGLETKBW3PvgPWqT"):
    track = _track()
    track.spotify_id = spotify_id
    return track


def _ctx_id_valide(**kw):
    """`_existing_spotify_id` n'accepte un ID existant que s'il a de quoi le
    VÉRIFIER : sans `artist_tracks` ni validateur, il le jette et re-scrape."""
    return EnrichmentContext(
        artist_tracks=[Track(title="Autre")], validate_spotify_id_unique=lambda *a: True, **kw
    )


class TestApplicationSpotifyId:
    """ReccoBeats sert le BPM sous trois formes selon l'endpoint interrogé ;
    manquer une forme, c'est perdre le vote sans que rien ne le signale."""

    @pytest.mark.parametrize(
        "info",
        [
            {"success": True, "bpm": 120},
            {"success": True, "tempo": 120},
            {"success": True, "audio_features": {"tempo": 120}},
        ],
    )
    def test_les_trois_emplacements_du_bpm(self, info):
        ctx = _ctx_id_valide()
        provider = ReccoBeatsProvider(_FakeReccoClient(track_info=info))
        assert provider.enrich(_avec_id(), ctx) is True
        assert ("reccobeats", 120) in ctx.bpm_ballot.candidates

    @pytest.mark.parametrize("info", [None, {"success": False}])
    def test_reponse_sans_donnees(self, info):
        provider = ReccoBeatsProvider(_FakeReccoClient(track_info=info))
        assert provider.enrich(_avec_id(), _ctx_id_valide()) is False

    def test_succes_partiel_sans_bpm(self):
        """Un ID sans BPM reste un SUCCÈS : l'ID est la donnée que la source
        devait fournir. Le compter en échec déclencherait le nettoyage du
        morceau par l'orchestrateur."""
        provider = ReccoBeatsProvider(_FakeReccoClient(track_info={"success": True}))
        assert provider.enrich(_avec_id(), _ctx_id_valide()) is True

    def test_duree_valide_posee(self):
        info = {"success": True, "bpm": 120, "duration": 195}
        track = _avec_id()
        ReccoBeatsProvider(_FakeReccoClient(track_info=info)).enrich(track, _ctx_id_valide())
        assert track.duration == 195

    @pytest.mark.parametrize("duree", [0, -5, "trois minutes"])
    def test_duree_invalide_signalee_et_ignoree(self, duree):
        info = {"success": True, "bpm": 120, "duration": duree}
        track = _avec_id()
        ReccoBeatsProvider(_FakeReccoClient(track_info=info)).enrich(track, _ctx_id_valide())
        assert track.duration is None

    def test_key_et_mode_journalises_et_observes(self):
        info = {"success": True, "bpm": 120, "key": 5, "mode": 1}
        ctx = _ctx_id_valide()
        ReccoBeatsProvider(_FakeReccoClient(track_info=info)).enrich(_avec_id(), ctx)
        champs = {o.field for o in ctx.observations if o.source == "reccobeats"}
        assert {"key", "mode", "reccobeats_resolution"} <= champs


class TestVoieIsrcSync:
    def test_isrc_recupere_via_deezer(self):
        class _Deezer:
            def get_isrc(self, artist, title):
                return "FRX9820001"

        client = _FakeReccoClient(isrc_info={"success": True, "bpm": 120})
        provider = ReccoBeatsProvider(client, deezer_client=_Deezer())
        track = _track()
        assert provider.try_by_isrc(track, EnrichmentContext()) is True
        assert track.isrc == "FRX9820001"

    def test_deezer_muet_puis_aucun_isrc(self):
        class _Deezer:
            def get_isrc(self, artist, title):
                return None

        provider = ReccoBeatsProvider(_FakeReccoClient(), deezer_client=_Deezer())
        assert provider.try_by_isrc(_track(), EnrichmentContext()) is False

    def test_deezer_en_erreur_n_empeche_pas_la_suite(self):
        class _Deezer:
            def get_isrc(self, artist, title):
                raise TypeError("réponse illisible")

        provider = ReccoBeatsProvider(_FakeReccoClient(), deezer_client=_Deezer())
        assert provider.try_by_isrc(_track(), EnrichmentContext()) is False

    def test_api_isrc_en_erreur(self):
        class _Leve(_FakeReccoClient):
            def get_track_info_by_isrc(self, isrc):
                raise KeyError("payload inattendu")

        track = _track()
        track.isrc = "FRX9820001"
        assert ReccoBeatsProvider(_Leve()).try_by_isrc(track, EnrichmentContext()) is False

    def test_isrc_sans_audio_features_bascule_sur_spotify_id(self):
        """Réponse valide mais vide de mesures : l'ISRC n'a pas suffi, le
        provider doit rendre False pour que le fallback Spotify ID s'exécute."""
        client = _FakeReccoClient(isrc_info={"success": True})
        track = _track()
        track.isrc = "FRX9820001"
        assert ReccoBeatsProvider(client).try_by_isrc(track, EnrichmentContext()) is False

    def test_sans_client(self):
        assert ReccoBeatsProvider(None).try_by_isrc(_track(), EnrichmentContext()) is False

    def test_artiste_principal_si_featuring(self):
        vus = []

        class _Deezer:
            def get_isrc(self, artist, title):
                vus.append(artist)
                return None

        track = _track()
        track.is_featuring = True
        track.primary_artist_name = "Principal"
        ReccoBeatsProvider(_FakeReccoClient(), deezer_client=_Deezer()).try_by_isrc(
            track, EnrichmentContext()
        )
        assert vus == ["Principal"]


class TestEchecsSync:
    def test_echec_complet_sans_id(self):
        """Ni ID existant ni ID scrapé : `_apply_spotify_info` conclut à l'ÉCHEC
        (branche distincte du succès partiel)."""
        p = ReccoBeatsProvider(_FakeReccoClient(track_info={"success": True, "bpm": 120}))
        track = _track()
        assert p._apply_spotify_info(track, {"success": True}, EnrichmentContext(), "SP1") is False

    def test_erreur_inattendue_tracee(self, caplog):
        """Dernier ressort du provider : un bug d'orchestration doit laisser un
        traceback, pas disparaître."""

        class _Client(_FakeReccoClient):
            def get_track_info(self, spotify_id):
                raise RuntimeError("bug interne")

        p = ReccoBeatsProvider(_Client())
        with caplog.at_level("ERROR"):
            assert p.enrich(_avec_id(), _ctx_id_valide()) is False
        assert [r for r in caplog.records if r.exc_info], "traceback attendu"


class TestEchecsAsync:
    def test_erreur_inattendue_tracee(self, caplog):
        class _Client(_FakeReccoClientAsync):
            async def get_track_info_async(self, http, spotify_id):
                raise RuntimeError("bug interne")

        p = ReccoBeatsProvider(_Client())
        with caplog.at_level("ERROR"):
            assert asyncio.run(p.enrich_async(_avec_id(), _ctx_id_valide())) is False
        assert [r for r in caplog.records if r.exc_info], "traceback attendu"


def test_fermeture_ferme_son_client():
    client = _FakeReccoClient()
    client.close = lambda: setattr(client, "ferme", True)
    provider = ReccoBeatsProvider(client_factory=lambda: client)
    provider._resource.get()
    provider.close()
    assert getattr(client, "ferme", False) is True


def test_spotify_ids_par_isrc_le_cache_d_abord(monkeypatch):
    """Lot B2 : la voie ISRC gardait le `href` Spotify en cache sans s'en servir."""
    from src.api.reccobeats_api import ReccoBeatsIntegratedClient

    client = ReccoBeatsIntegratedClient.__new__(ReccoBeatsIntegratedClient)
    client.cache = {"isrc::FR1": {"href": "https://open.spotify.com/track/AAA"}}
    client.recco_base_url = "https://api.reccobeats.com/v1"
    appels = []

    class _Resp:
        status_code, headers = 200, {}

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "content": [
                    {"isrc": "FR2", "href": "https://open.spotify.com/track/BBB", "popularity": 1},
                    {"isrc": "FR2", "href": "https://open.spotify.com/track/CCC", "popularity": 9},
                ]
            }

    client.recco_session = type(
        "S", (), {"get": lambda self, url, params, timeout: appels.append(params) or _Resp()}
    )()
    assert client.spotify_ids_par_isrc(["fr1", "FR2", "FR3"]) == {
        "FR1": ["AAA"],
        "FR2": ["CCC", "BBB"],
    }
    assert appels == [{"ids": "FR2,FR3"}]
