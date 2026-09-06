"""Tests du provider Deezer (src/enrichment/providers/deezer) — sans réseau.

On injecte un faux client Deezer : on vérifie que le provider pose les champs
(duration/isrc/…) et alimente le scrutin BPM du contexte, pas le track direct.
"""

import asyncio

import pytest

from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.deezer import DeezerProvider
from src.models.artist import Artist
from src.models.track import Track


class _FakeDeezerClient:
    """Client Deezer minimal : renvoie le result figé passé au constructeur."""

    def __init__(self, result):
        self._result = result
        self.calls = []

    def enrich_track(self, artist, title, previous_duration, scraped_release_date):
        self.calls.append((artist, title, previous_duration, scraped_release_date))
        return self._result

    async def enrich_track_async(
        self, http, artist, title, previous_duration, scraped_release_date
    ):
        """Jumeau async : même signature au `http` près (session partagée)."""
        self.calls.append((artist, title, previous_duration, scraped_release_date))
        return self._result


def _track():
    return Track(title="Solo", artist=Artist(name="Sofiane Pamart"))


def test_is_available_reflete_le_client():
    assert DeezerProvider(None).is_available() is False
    assert DeezerProvider(_FakeDeezerClient({})).is_available() is True


def test_enrich_pose_duration_isrc_et_candidat_bpm():
    result = {
        "success": True,
        "verifications": {},
        "data": {
            "deezer_duration": 195,
            "deezer_isrc": "FRX9820001",
            "deezer_bpm": 142,
            "deezer_track_id": 999,
        },
    }
    provider = DeezerProvider(_FakeDeezerClient(result))
    ctx = EnrichmentContext()
    track = _track()

    assert provider.enrich(track, ctx) is True
    assert track.duration == 195
    assert track.isrc == "FRX9820001"
    # Le BPM Deezer est un CANDIDAT du scrutin, pas une valeur imposée
    assert ("deezer", 142) in ctx.bpm_ballot.candidates


def test_enrich_bpm_opportuniste_candidat():
    result = {
        "success": True,
        "verifications": {},
        "data": {"deezer_bpm": 100},
    }
    provider = DeezerProvider(_FakeDeezerClient(result))
    track = _track()
    ctx = EnrichmentContext()
    provider.enrich(track, ctx)
    # E7 : le BPM Deezer est un CANDIDAT du scrutin (plus de pose legacy directe)
    assert ("deezer", 100) in ctx.bpm_ballot.candidates


def test_enrich_echec_client_renvoie_false():
    provider = DeezerProvider(_FakeDeezerClient({"success": False, "error": "not found"}))
    track = _track()
    assert provider.enrich(track, EnrichmentContext()) is False
    assert track.duration is None


def test_enrich_utilise_artiste_principal_si_featuring():
    result = {"success": True, "verifications": {}, "data": {}}
    client = _FakeDeezerClient(result)
    provider = DeezerProvider(client)
    track = Track(title="Feat", artist=Artist(name="Cherché"))
    track.is_featuring = True
    track.primary_artist_name = "Principal"
    provider.enrich(track, EnrichmentContext())
    assert client.calls[0][0] == "Principal"  # artist passé à l'API Deezer


def test_provider_non_disponible_renvoie_false():
    assert DeezerProvider(None).enrich(_track(), EnrichmentContext()) is False


# ──────────────────────────────────────────────────────────────────────
# Les branches de REFUS : ce sont elles qui protègent la base d'un
# écrasement par une donnée Deezer incohérente.
# ──────────────────────────────────────────────────────────────────────


def _resultat(data=None, verifications=None):
    return {"success": True, "verifications": verifications or {}, "data": data or {}}


def _provider(result):
    return DeezerProvider(_FakeDeezerClient(result))


class TestDuree:
    """`duration` n'est écrasée que si Deezer est cohérent avec l'existant —
    sauf `force_update`, ou si le morceau n'avait pas de durée du tout."""

    def test_incoherente_ignoree(self):
        p = _provider(
            _resultat(
                {"deezer_duration": 300},
                {"duration": {"is_valid": False, "message": "écart de 105s"}},
            )
        )
        track = _track()
        track.duration = 195
        assert p.enrich(track, EnrichmentContext()) is False
        assert track.duration == 195

    def test_incoherente_mais_force_update(self):
        p = _provider(
            _resultat({"deezer_duration": 300}, {"duration": {"is_valid": False, "message": "x"}})
        )
        track = _track()
        track.duration = 195
        assert p.enrich(track, EnrichmentContext(force_update=True)) is True
        assert track.duration == 300

    def test_aucune_duree_prealable_accepte_sans_verification(self):
        """Rien à écraser : on prend ce que Deezer donne."""
        p = _provider(
            _resultat({"deezer_duration": 300}, {"duration": {"is_valid": False, "message": "x"}})
        )
        track = _track()
        assert p.enrich(track, EnrichmentContext()) is True
        assert track.duration == 300

    @pytest.mark.parametrize(
        "verif",
        [
            {"is_valid": True, "difference": 2},
            {"is_valid": True, "difference": None, "message": "pas de durée à comparer"},
            {"is_valid": False, "message": "incohérente"},
        ],
    )
    def test_les_trois_formes_de_verdict_sont_journalisables(self, verif):
        """Les trois branches de log (écart chiffré / message seul / rejet) sont
        empruntées : un `KeyError` dans l'une d'elles ferait échouer tout le
        provider par son filet d'exception."""
        p = _provider(_resultat({"deezer_duration": 300}, {"duration": verif}))
        assert p.enrich(_track(), EnrichmentContext(force_update=True)) is True


class TestDateDeSortie:
    def test_divergente_ignoree(self):
        p = _provider(
            _resultat(
                {"deezer_release_date": "2019-05-01"},
                {
                    "release_date": {
                        "is_valid": True,
                        "dates_match": False,
                        "deezer_date": "2019-05-01",
                        "scraped_date": "2020-01-10",
                    }
                },
            )
        )
        track = _track()
        track.release_date = "2020-01-10"
        assert p.enrich(track, EnrichmentContext()) is False
        assert track.release_date == "2020-01-10"

    def test_divergente_mais_force_update(self):
        p = _provider(
            _resultat(
                {"deezer_release_date": "2019-05-01"},
                {"release_date": {"is_valid": True, "dates_match": False}},
            )
        )
        track = _track()
        track.release_date = "2020-01-10"
        assert p.enrich(track, EnrichmentContext(force_update=True)) is True
        assert track.release_date == "2019-05-01"

    def test_coherente_ecrite(self):
        p = _provider(
            _resultat(
                {"deezer_release_date": "2020-01-10"},
                {"release_date": {"is_valid": True, "dates_match": True}},
            )
        )
        track = _track()
        track.release_date = "2020-01-10"
        assert p.enrich(track, EnrichmentContext()) is True

    def test_indeterminee_sans_date_prealable_acceptee(self):
        p = _provider(
            _resultat(
                {"deezer_release_date": "2020-01-10"},
                {
                    "release_date": {
                        "is_valid": True,
                        "dates_match": None,
                        "message": "rien à comparer",
                    }
                },
            )
        )
        track = _track()
        assert p.enrich(track, EnrichmentContext()) is True
        assert track.release_date == "2020-01-10"

    def test_verdict_invalide_journalise(self):
        p = _provider(
            _resultat(
                {"deezer_release_date": "2020-01-10"},
                {"release_date": {"is_valid": False, "message": "date Deezer illisible"}},
            )
        )
        assert p.enrich(_track(), EnrichmentContext()) is True


class TestIsrcEtMetadonnees:
    def test_isrc_existant_non_ecrase(self):
        """L'ISRC est le pivot inter-sources (il alimente ReccoBeats) : Deezer ne
        le remplace pas par le sien sans qu'on le lui demande."""
        p = _provider(_resultat({"deezer_isrc": "FRAAA9900001"}))
        track = _track()
        track.isrc = "FRX9820001"
        assert p.enrich(track, EnrichmentContext()) is False
        assert track.isrc == "FRX9820001"

    def test_isrc_existant_ecrase_si_force(self):
        p = _provider(_resultat({"deezer_isrc": "FRAAA9900001"}))
        track = _track()
        track.isrc = "FRX9820001"
        assert p.enrich(track, EnrichmentContext(force_update=True)) is True
        assert track.isrc == "FRAAA9900001"

    def test_bpm_zero_ou_absent_ne_vote_pas(self):
        """Deezer renvoie très souvent 0 : `sanitize_bpm` l'écarte, et un
        candidat nul fausserait le scrutin."""
        ctx = EnrichmentContext()
        assert _provider(_resultat({"deezer_bpm": 0})).enrich(_track(), ctx) is False
        assert ctx.bpm_ballot.candidates == []

    def test_aucune_donnee_utile(self):
        assert _provider(_resultat({})).enrich(_track(), EnrichmentContext()) is False

    def test_identifiants_deezer_poses(self):
        """e19 : ces valeurs ont enfin des colonnes. Jusqu'au 2026-09-06 elles
        étaient posées sur l'objet puis perdues en fin de run, tout en comptant
        dans `updated` — Deezer annonçait donc « réussi » sur du vent."""
        data = {
            "deezer_track_id": 3135556,
            "deezer_link": "https://www.deezer.com/track/3135556",
            "deezer_explicit_lyrics": True,
        }
        track = _track()
        assert _provider(_resultat(data)).enrich(track, EnrichmentContext()) is True
        assert track.deezer_id == 3135556
        assert track.deezer_url == "https://www.deezer.com/track/3135556"
        assert track.lyrics.explicit is True

    def test_identifiants_existants_non_ecrases_sans_force(self):
        data = {"deezer_track_id": 999, "deezer_link": "https://neuf"}
        track = _track()
        track.deezer_id = 42
        track.deezer_url = "https://ancien"
        assert _provider(_resultat(data)).enrich(track, EnrichmentContext()) is False
        assert (track.deezer_id, track.deezer_url) == (42, "https://ancien")

    def test_explicite_faux_est_une_mesure_qui_ne_se_refait_pas(self):
        """`False` est un constat : une fois posé, il ne doit pas être re-posé
        comme s'il manquait (le test distingue False de None)."""
        track = _track()
        track.lyrics.explicit = False
        p = _provider(_resultat({"deezer_explicit_lyrics": True}))
        assert p.enrich(track, EnrichmentContext()) is False
        assert track.lyrics.explicit is False
        assert p.enrich(track, EnrichmentContext(force_update=True)) is True
        assert track.lyrics.explicit is True

    def test_pochette_deezer_non_posee_sur_le_track(self):
        """Le chantier Media récupère la même image en HAUTE résolution et la
        range sur le disque : poser l'URL medium ici était un doublon dégradé,
        retiré le 2026-09-06. `deezer_picture` reste le repli de
        `media_enricher`, ce n'est pas le champ API qui disparaît."""
        data = {"deezer_picture": "https://img/medium.jpg"}
        track = _track()
        assert _provider(_resultat(data)).enrich(track, EnrichmentContext()) is False
        assert not hasattr(track, "deezer_picture_url")


class TestVoieAsync:
    """Jumeau async : même `_apply_result`, donc mêmes décisions. Ce test est le
    filet qui manquait à Musixmatch le 2026-09-05 — un correctif posé sur une
    seule des deux voies."""

    def test_meme_resultat_que_la_voie_sync(self):
        result = _resultat(
            {"deezer_duration": 195, "deezer_isrc": "FRX9820001", "deezer_bpm": 142},
            {"duration": {"is_valid": True, "difference": 0}},
        )
        sync_track, async_track = _track(), _track()
        ctx_sync, ctx_async = EnrichmentContext(), EnrichmentContext()

        assert _provider(result).enrich(sync_track, ctx_sync) is True
        assert (
            asyncio.run(
                DeezerProvider(_FakeDeezerClient(result)).enrich_async(async_track, ctx_async)
            )
            is True
        )

        assert (sync_track.duration, sync_track.isrc) == (async_track.duration, async_track.isrc)
        assert ctx_sync.bpm_ballot.candidates == ctx_async.bpm_ballot.candidates

    def test_artiste_principal_utilise_aussi_en_async(self):
        client = _FakeDeezerClient(_resultat())
        track = Track(title="Feat", artist=Artist(name="Cherché"))
        track.is_featuring = True
        track.primary_artist_name = "Principal"
        asyncio.run(DeezerProvider(client).enrich_async(track, EnrichmentContext()))
        assert client.calls[0][0] == "Principal"

    def test_client_absent(self):
        p = DeezerProvider(None)
        assert asyncio.run(p.enrich_async(_track(), EnrichmentContext())) is False

    def test_echec_client(self):
        p = DeezerProvider(_FakeDeezerClient({"success": False, "error": "not found"}))
        assert asyncio.run(p.enrich_async(_track(), EnrichmentContext())) is False


class TestFiletDException:
    """`_apply_result` est le seul code qui puisse lever ici (le client gère déjà
    réseau et JSON) : un bug y est un ÉCHEC tracé, pas un silence."""

    def test_result_malforme_donne_false_et_trace(self, caplog):
        p = _provider({"success": True})  # ni 'data' ni 'verifications'
        with caplog.at_level("ERROR"):
            assert p.enrich(_track(), EnrichmentContext()) is False
        assert "Deezer" in caplog.text

    def test_result_malforme_en_async_aussi(self):
        p = DeezerProvider(_FakeDeezerClient({"success": True}))
        assert asyncio.run(p.enrich_async(_track(), EnrichmentContext())) is False
