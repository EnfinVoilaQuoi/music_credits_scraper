"""Client ReccoBeats : formats de réponse variables, cache, sélection.

ReccoBeats est la 2ᵉ source BPM/key/mode et exige un pivot en entrée (Spotify
Track ID ou ISRC). Son client était le moins couvert des clients d'API (44 %),
alors qu'il porte trois décisions non triviales : quel objet extraire d'une
réponse au format variable, quel pressing garder pour un ISRC, et quand servir
le cache plutôt que la source.

Aucun appel réseau : la session HTTP est stubbée. Le cache est toujours écrit
dans `tmp_path` — jamais dans le dépôt.
"""

import asyncio
import time

import pytest

from src.api.reccobeats_api import ReccoBeatsIntegratedClient


@pytest.fixture
def client(tmp_path):
    return ReccoBeatsIntegratedClient(cache_file=str(tmp_path / "reccobeats_cache.json"))


class _Reponse:
    def __init__(self, charge, statut=200):
        self._charge = charge
        self.status_code = statut
        self.headers = {}

    def json(self):
        if isinstance(self._charge, Exception):
            raise self._charge
        return self._charge


class _HttpAsync:
    """Session async : sert la DERNIÈRE réponse en boucle, mémorise les URL."""

    def __init__(self, *reponses):
        self.reponses = list(reponses)
        self.vues = []

    async def get(self, url, params=None, headers=None, timeout=None):
        self.vues.append((url, params))
        rep = self.reponses[min(len(self.vues), len(self.reponses)) - 1]
        if isinstance(rep, Exception):
            raise rep
        return rep


class TestExtractionDuTrack:
    """La réponse 200 arrive sous quatre formes selon l'endpoint : liste nue,
    `content` liste, `content` objet, ou objet direct."""

    def test_liste_nue(self, client):
        assert client._pick_track_from_response([{"id": "r1"}])["id"] == "r1"

    def test_content_liste(self, client):
        assert client._pick_track_from_response({"content": [{"id": "r1"}]})["id"] == "r1"

    def test_content_objet(self, client):
        assert client._pick_track_from_response({"content": {"id": "r1"}})["id"] == "r1"

    def test_objet_direct(self, client):
        assert client._pick_track_from_response({"trackTitle": "T"})["trackTitle"] == "T"

    @pytest.mark.parametrize(
        "data",
        [None, [], {}, {"content": []}, {"content": {}}, {"content": {"autre": 1}}, "texte"],
    )
    def test_formes_inexploitables(self, client, data):
        assert client._pick_track_from_response(data) is None


class TestMeilleurPressingIsrc:
    """Un ISRC peut correspondre à PLUSIEURS pressings Spotify : on garde le
    plus populaire, sans quoi le BPM viendrait d'une réédition au hasard."""

    def test_le_plus_populaire_gagne(self, client):
        content = [
            {"id": "a", "popularity": 10},
            {"id": "b", "popularity": 90},
            {"id": "c", "popularity": 50},
        ]
        assert client._best_isrc_hit(content)["id"] == "b"

    def test_popularite_absente_vaut_zero(self, client):
        assert client._best_isrc_hit([{"id": "a"}, {"id": "b", "popularity": 1}])["id"] == "b"

    def test_contenu_vide(self, client):
        assert client._best_isrc_hit([]) is None


class TestApplicationDesDonnees:
    def test_duree_convertie_en_secondes(self, client):
        result = {}
        client._apply_duration(result, {"durationMs": 228_000})
        assert result["duration"] == 228

    def test_duree_nulle_neutralisee(self, client):
        result = {}
        client._apply_duration(result, {"durationMs": 0})
        assert result["duration"] is None

    def test_sans_duree_le_champ_reste_absent(self, client):
        result = {}
        client._apply_duration(result, {})
        assert "duration" not in result

    def test_audio_features_completes(self, client):
        result = {}
        client._apply_audio_features(
            result,
            {
                "tempo": 142.0,
                "key": 0,
                "mode": 1,
                "energy": 0.8,
                "danceability": 0.7,
                "valence": 0.5,
            },
        )
        assert result["bpm"] == 142.0
        assert (result["key"], result["mode"]) == (0, 1)
        assert result["musical_key"]  # dérivé par music_theory

    def test_musical_key_absente_sans_la_paire(self, client):
        """`key` seule ne suffit pas : la tonalité est une PAIRE key+mode."""
        result = {}
        client._apply_audio_features(result, {"tempo": 142.0, "key": 0})
        assert "musical_key" not in result

    def test_features_absentes_ne_touchent_a_rien(self, client):
        result = {"deja": 1}
        client._apply_audio_features(result, None)
        assert result == {"deja": 1}


class TestCache:
    def test_cache_absent_au_demarrage(self, client):
        assert client.cache == {}

    def test_fichier_illisible_donne_un_cache_vide(self, tmp_path):
        chemin = tmp_path / "casse.json"
        chemin.write_text("{ pas du json", encoding="utf-8")
        assert ReccoBeatsIntegratedClient(cache_file=str(chemin)).cache == {}

    def test_aller_retour_disque(self, client, tmp_path):
        client.cache["k"] = {"bpm": 142}
        client._save_cache()

        relu = ReccoBeatsIntegratedClient(cache_file=client.cache_file)
        assert relu.cache["k"]["bpm"] == 142

    def test_entree_complete_servie(self, client):
        cle = client._get_cache_key("sp1")
        client.cache[cle] = {"bpm": 142}
        assert client._cached_spotify_info(cle, "sp1", use_cache=True, force_refresh=False)

    def test_entree_avec_audio_features_seules_servie(self, client):
        cle = client._get_cache_key("sp1")
        client.cache[cle] = {"audio_features": {"tempo": 142}}
        assert client._cached_spotify_info(cle, "sp1", use_cache=True, force_refresh=False)

    def test_cache_desactive(self, client):
        cle = client._get_cache_key("sp1")
        client.cache[cle] = {"bpm": 142}
        assert client._cached_spotify_info(cle, "sp1", use_cache=False, force_refresh=False) is None

    def test_force_refresh_supprime_lentree(self, client):
        cle = client._get_cache_key("sp1")
        client.cache[cle] = {"bpm": 142}

        assert client._cached_spotify_info(cle, "sp1", use_cache=True, force_refresh=True) is None
        assert cle not in client.cache

    def test_entree_not_found_reste_hors_des_lecteurs_de_donnees(self, client):
        """`_cached_spotify_info` ne sert QUE des données exploitables : une
        absence n'en est pas une. C'est `_not_found_is_fresh` qui la lit."""
        cle = client._get_cache_key("sp1")
        client._cache_not_found(cle, "sp1", _repondu())

        assert client._cached_spotify_info(cle, "sp1", use_cache=True, force_refresh=False) is None


def _repondu():
    """Observation où ReccoBeats a RÉPONDU « inconnu » (200, contenu vide)."""
    from src.observability import source_usage
    from src.observability.issues import IssueKind

    with source_usage.observe("reccobeats") as obs:
        source_usage.record_attempt("reccobeats", IssueKind.OK)
        obs.absent("inconnu")
    return obs


def test_une_panne_n_est_pas_memorisee_comme_absence(client):
    """2026-09-28 : le client rend `None` sur une panne aussi ; figée en
    « not_found », elle privait le morceau de ReccoBeats pendant tout le TTL."""
    from src.observability import source_usage
    from src.observability.issues import IssueKind

    cle = client._get_cache_key("sp1")
    with source_usage.observe("reccobeats") as obs:
        source_usage.record_attempt("reccobeats", IssueKind.TIMEOUT)
        obs.absent("rien")
        client._cache_not_found(cle, "sp1", obs)
    assert cle not in client.cache


class TestPeremptionDuCacheNegatif:
    """CORRIGÉ le 2026-09-04. Les entrées « not_found » étaient ÉCRITES mais
    jamais relues : 164 identifiants sur 930 étaient re-demandés à ReccoBeats à
    chaque passage complet, sur une API à débit limité. L'horodatage stocké
    depuis toujours montrait qu'une péremption était prévue."""

    def test_absence_fraiche_reconnue(self, client):
        cle = client._get_cache_key("sp1")
        client._cache_not_found(cle, "sp1", _repondu())

        assert client._not_found_is_fresh(cle) is True

    def test_absence_perimee(self, client, monkeypatch):
        import src.api.reccobeats_api as mod

        cle = client._get_cache_key("sp1")
        client.cache[cle] = {
            "error": "not_found",
            "timestamp": time.time() - (mod.RECCOBEATS_NOT_FOUND_TTL_DAYS + 1) * 86_400,
        }
        assert client._not_found_is_fresh(cle) is False

    @pytest.mark.parametrize("horodatage", [None, "hier", float("nan")])
    def test_horodatage_douteux_vaut_perime(self, client, horodatage):
        """On préfère redemander plutôt que figer une absence sur une donnée
        qu'on ne sait pas dater."""
        cle = client._get_cache_key("sp1")
        entree = {"error": "not_found"}
        if horodatage is not None:
            entree["timestamp"] = horodatage
        client.cache[cle] = entree

        assert client._not_found_is_fresh(cle) is False

    @pytest.mark.parametrize("entree", [None, {"bpm": 142}, {"error": "autre"}, "texte"])
    def test_ce_qui_n_est_pas_une_absence(self, client, entree):
        cle = client._get_cache_key("sp1")
        if entree is not None:
            client.cache[cle] = entree
        assert client._not_found_is_fresh(cle) is False

    def test_la_source_n_est_plus_sollicitee(self, client, monkeypatch):
        """Le bout en bout : 1ʳᵉ passe → une requête et l'absence mémorisée ;
        2ᵉ passe → aucune requête."""
        http = _HttpAsync(_Reponse({"content": []}))

        assert asyncio.run(client.get_track_info_async(http, "sp1")) is None
        assert len(http.vues) == 1

        assert asyncio.run(client.get_track_info_async(http, "sp1")) is None
        assert len(http.vues) == 1  # rien de plus

    def test_absence_perimee_redemande(self, client, monkeypatch):
        import src.api.reccobeats_api as mod

        http = _HttpAsync(_Reponse({"content": []}))
        asyncio.run(client.get_track_info_async(http, "sp1"))

        client.cache[client._get_cache_key("sp1")]["timestamp"] = (
            time.time() - (mod.RECCOBEATS_NOT_FOUND_TTL_DAYS + 1) * 86_400
        )
        asyncio.run(client.get_track_info_async(http, "sp1"))

        assert len(http.vues) == 2

    def test_force_refresh_redemande(self, client, monkeypatch):
        http = _HttpAsync(_Reponse({"content": []}))
        asyncio.run(client.get_track_info_async(http, "sp1"))
        asyncio.run(client.get_track_info_async(http, "sp1", force_refresh=True))

        assert len(http.vues) == 2

    def test_voie_isrc_aussi(self, client, monkeypatch):
        """La voie ISRC portait EXACTEMENT le même trou (82 entrées chacune)."""
        http = _HttpAsync(_Reponse({"content": []}))

        assert asyncio.run(client.get_track_info_by_isrc_async(http, "FR1234500001")) is None
        assert len(http.vues) == 1

        assert asyncio.run(client.get_track_info_by_isrc_async(http, "FR1234500001")) is None
        assert len(http.vues) == 1
