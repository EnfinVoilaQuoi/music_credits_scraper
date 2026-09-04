"""Client ReccoBeats : formats de réponse variables, cache, sélection, batch.

ReccoBeats est la 2ᵉ source BPM/key/mode et exige un pivot en entrée (Spotify
Track ID ou ISRC). Son client était le moins couvert des clients d'API (44 %),
alors qu'il porte trois décisions non triviales : quel objet extraire d'une
réponse au format variable, quel pressing garder pour un ISRC, et quand servir
le cache plutôt que la source.

Aucun appel réseau : la session HTTP est stubbée. Le cache est toujours écrit
dans `tmp_path` — jamais dans le dépôt.
"""

import json
import time
from pathlib import Path

import pytest
import requests

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


def _stub_get(client, monkeypatch, *reponses):
    """Sert les réponses dans l'ordre ; mémorise les URL demandées."""
    vues = []

    def fake_get(url, **kwargs):
        vues.append((url, kwargs.get("params")))
        i = len(vues) - 1
        rep = reponses[i] if i < len(reponses) else reponses[-1]
        if isinstance(rep, Exception):
            raise rep
        return rep

    monkeypatch.setattr(client.recco_session, "get", fake_get)
    return vues


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
        client._cache_not_found(cle, "sp1")

        assert client._cached_spotify_info(cle, "sp1", use_cache=True, force_refresh=False) is None


class TestPeremptionDuCacheNegatif:
    """CORRIGÉ le 2026-09-04. Les entrées « not_found » étaient ÉCRITES mais
    jamais relues : 164 identifiants sur 930 étaient re-demandés à ReccoBeats à
    chaque passage complet, sur une API à débit limité. L'horodatage stocké
    depuis toujours montrait qu'une péremption était prévue."""

    def test_absence_fraiche_reconnue(self, client):
        cle = client._get_cache_key("sp1")
        client._cache_not_found(cle, "sp1")

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
        vues = _stub_get(client, monkeypatch, _Reponse({"content": []}))

        assert client.get_track_info("sp1") is None
        assert len(vues) == 1

        assert client.get_track_info("sp1") is None
        assert len(vues) == 1  # rien de plus

    def test_absence_perimee_redemande(self, client, monkeypatch):
        import src.api.reccobeats_api as mod

        vues = _stub_get(client, monkeypatch, _Reponse({"content": []}))
        client.get_track_info("sp1")

        client.cache[client._get_cache_key("sp1")]["timestamp"] = (
            time.time() - (mod.RECCOBEATS_NOT_FOUND_TTL_DAYS + 1) * 86_400
        )
        client.get_track_info("sp1")

        assert len(vues) == 2

    def test_force_refresh_redemande(self, client, monkeypatch):
        vues = _stub_get(client, monkeypatch, _Reponse({"content": []}))
        client.get_track_info("sp1")
        client.get_track_info("sp1", force_refresh=True)

        assert len(vues) == 2

    def test_voie_isrc_aussi(self, client, monkeypatch):
        """La voie ISRC portait EXACTEMENT le même trou (82 entrées chacune)."""
        vues = _stub_get(client, monkeypatch, _Reponse({"content": []}))

        assert client.get_track_info_by_isrc("FR1234500001") is None
        assert len(vues) == 1

        assert client.get_track_info_by_isrc("FR1234500001") is None
        assert len(vues) == 1

    def test_statistiques(self, client):
        client.cache = {
            "a": {"bpm": 142, "success": True},
            "b": {"error": "not_found"},
            "c": {"audio_features": {}},
        }
        stats = client.get_cache_stats()

        assert stats["total_entries"] == 3
        assert stats["successful_entries"] == 1
        assert stats["error_entries"] == 1


class TestRecuperationParSpotifyId:
    def test_chaine_complete(self, client, monkeypatch):
        """Deux requêtes (track puis audio-features) pour UN appel logique."""
        _stub_get(
            client,
            monkeypatch,
            _Reponse({"content": [{"id": "r1", "trackTitle": "T", "durationMs": 228_000}]}),
            _Reponse({"tempo": 142.0, "key": 0, "mode": 1}),
        )

        res = client.get_track_info("sp1")

        assert res["success"] is True
        assert res["spotify_id"] == "sp1" and res["source"] == "reccobeats"
        assert res["duration"] == 228 and res["bpm"] == 142.0

    def test_resultat_mis_en_cache(self, client, monkeypatch):
        _stub_get(
            client,
            monkeypatch,
            _Reponse({"content": [{"id": "r1", "durationMs": 1000}]}),
            _Reponse({"tempo": 142.0}),
        )
        client.get_track_info("sp1")

        sur_disque = json.loads(Path(client.cache_file).read_text(encoding="utf-8"))
        assert sur_disque[client._get_cache_key("sp1")]["bpm"] == 142.0

    def test_second_appel_servi_par_le_cache(self, client, monkeypatch):
        vues = _stub_get(
            client,
            monkeypatch,
            _Reponse({"content": [{"id": "r1", "durationMs": 1000}]}),
            _Reponse({"tempo": 142.0}),
        )
        client.get_track_info("sp1")
        appels_apres_le_premier = len(vues)

        client.get_track_info("sp1")
        assert len(vues) == appels_apres_le_premier  # aucune requête de plus

    def test_id_inconnu_memorise_lechec(self, client, monkeypatch):
        _stub_get(client, monkeypatch, _Reponse({"content": []}))

        assert client.get_track_info("sp1") is None
        assert client.cache[client._get_cache_key("sp1")]["error"] == "not_found"

    def test_statut_non_200(self, client, monkeypatch):
        _stub_get(client, monkeypatch, _Reponse({}, statut=429))
        assert client.get_track_info("sp1") is None

    def test_panne_reseau(self, client, monkeypatch):
        _stub_get(client, monkeypatch, requests.RequestException("coupure"))
        assert client.get_track_info("sp1") is None


class TestRecuperationParIsrc:
    def test_pressing_le_plus_populaire(self, client, monkeypatch):
        _stub_get(
            client,
            monkeypatch,
            _Reponse({"content": [{"id": "a", "popularity": 1}, {"id": "b", "popularity": 99}]}),
        )
        assert client.get_track_by_isrc("FR1234500001")["id"] == "b"

    def test_isrc_inconnu(self, client, monkeypatch):
        _stub_get(client, monkeypatch, _Reponse({"content": []}))
        assert client.get_track_by_isrc("FR1234500001") is None

    def test_statut_non_200(self, client, monkeypatch):
        _stub_get(client, monkeypatch, _Reponse({}, statut=500))
        assert client.get_track_by_isrc("FR1234500001") is None

    def test_json_illisible(self, client, monkeypatch):
        _stub_get(client, monkeypatch, _Reponse(ValueError("pas du JSON")))
        assert client.get_track_by_isrc("FR1234500001") is None


class TestAudioFeaturesEnLot:
    """`/audio-features?ids=` : 40 IDs par requête, mappés par ID ReccoBeats."""

    def test_mapping_par_id(self, client, monkeypatch):
        _stub_get(
            client,
            monkeypatch,
            _Reponse({"content": [{"id": "a", "tempo": 100}, {"id": "b", "tempo": 200}]}),
        )
        res = client.get_audio_features_batch(["a", "b"])

        assert res["a"]["tempo"] == 100 and res["b"]["tempo"] == 200

    def test_liste_vide_sans_requete(self, client, monkeypatch):
        vues = _stub_get(client, monkeypatch, _Reponse({"content": []}))
        assert client.get_audio_features_batch([]) == {}
        assert vues == []

    def test_plafond_a_40_ids(self, client, monkeypatch):
        vues = _stub_get(client, monkeypatch, _Reponse({"content": []}))
        client.get_audio_features_batch([f"id{i}" for i in range(60)])

        assert len(vues[0][1]["ids"].split(",")) == 40

    def test_entree_sans_id_ignoree(self, client, monkeypatch):
        _stub_get(client, monkeypatch, _Reponse({"content": [{"tempo": 100}]}))
        assert client.get_audio_features_batch(["a"]) == {}

    def test_statut_non_200(self, client, monkeypatch):
        _stub_get(client, monkeypatch, _Reponse({}, statut=503))
        assert client.get_audio_features_batch(["a"]) == {}


class TestLotDeTracks:
    """`get_multiple_tracks_with_bpm` : prêt mais NON câblé à l'orchestrateur
    (cf. WIP). À garder testé pour qu'il soit branchable sans surprise."""

    def test_deux_requetes_pour_un_lot(self, client, monkeypatch):
        vues = _stub_get(
            client,
            monkeypatch,
            _Reponse({"content": [{"id": "a"}, {"id": "b"}]}),
            _Reponse({"content": [{"id": "a", "tempo": 100}]}),
        )

        tracks = client.get_multiple_tracks_with_bpm(["sp1", "sp2"])

        assert len(vues) == 2
        assert tracks[0]["bpm"] == 100
        assert "bpm" not in tracks[1]  # pas de features pour b

    def test_plafond_a_40_ids(self, client, monkeypatch):
        vues = _stub_get(client, monkeypatch, _Reponse({"content": []}))
        client.get_multiple_tracks_with_bpm([f"id{i}" for i in range(60)])

        assert len(vues[0][1]["ids"].split(",")) == 40

    def test_lot_vide(self, client, monkeypatch):
        _stub_get(client, monkeypatch, _Reponse({"content": []}))
        assert client.get_multiple_tracks_with_bpm(["sp1"]) == []

    def test_statut_non_200(self, client, monkeypatch):
        _stub_get(client, monkeypatch, _Reponse({}, statut=500))
        assert client.get_multiple_tracks_with_bpm(["sp1"]) == []
