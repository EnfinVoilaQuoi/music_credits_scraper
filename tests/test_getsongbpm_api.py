"""Client GetSongBPM : sélection du hit, cache, construction du SongData.

Les fixtures HTML/JSON réelles sont couvertes par `test_getsongbpm_fixtures.py`.
Ici c'est la DÉCISION qui est testée — en particulier `_select_hit`, qui ancre
STRICTEMENT sur l'artiste : avec `type=both`, le premier résultat de l'API peut
être un objet *artiste* (sans tempo), et un `search[0]` aveugle rendait alors un
BPM nul ou celui d'un autre morceau.

Rappel de convention : le backlink getsongbpm.com est obligatoire pour l'usage
gratuit (`get_attribution_html`) — d'où son test.

Aucun appel réseau, cache toujours dans `tmp_path`.
"""

import pytest

from src.api.getsongbpm_api import GetSongBPMFetcher


@pytest.fixture
def client(tmp_path):
    return GetSongBPMFetcher(api_key="cle-de-test", cache_file=str(tmp_path / "cache.json"))


def _song(titre="Titre", artiste="ISHA", tempo=142, **extra):
    hit = {"id": "s1", "title": titre, "tempo": tempo, "key_of": "Em", "artist": {"name": artiste}}
    hit.update(extra)
    return hit


class TestParseTempo:
    """L'API annonce `tempo` en int mais renvoie parfois une string."""

    @pytest.mark.parametrize(
        ("valeur", "attendu"),
        [(142, 142), ("142", 142), ("142.4", 142), ("142.6", 143), (142.0, 142)],
    )
    def test_valeurs_numeriques(self, client, valeur, attendu):
        assert client._parse_tempo(valeur) == attendu

    @pytest.mark.parametrize("valeur", [None, "", "abc", {}, []])
    def test_valeurs_inexploitables(self, client, valeur):
        assert client._parse_tempo(valeur) is None


class TestModeDepuisLaCle:
    """Notation anglaise : un « m » final marque le mineur (« Em », « F#m »)."""

    @pytest.mark.parametrize(
        ("cle", "attendu"),
        [("Em", "minor"), ("F#m", "minor"), ("Bbm", "minor"), ("C", "major"), ("F#", "major")],
    )
    def test_mode(self, client, cle, attendu):
        assert client._extract_mode_from_key(cle) == attendu

    def test_cle_absente(self, client):
        assert client._extract_mode_from_key("") is None
        assert client._extract_mode_from_key(None) is None


class TestNormalisation:
    @pytest.mark.parametrize(
        ("brut", "attendu"),
        [
            ("L’Or", "l'or"),
            ("L‘Or", "l'or"),
            ("Éléphant", "elephant"),
            ("  DEUX   espaces ", "deux espaces"),
        ],
    )
    def test_norm(self, client, brut, attendu):
        assert client._norm(brut) == attendu

    def test_valeur_vide(self, client):
        assert client._norm(None) == ""


class TestSelectionDuHit:
    """L'artiste est une ANCRE STRICTE : un hit dont l'artiste ne correspond pas
    est écarté, même si le titre colle."""

    def test_match_parfait(self, client):
        hits = [_song(titre="Autre"), _song(titre="Titre")]
        assert client._select_hit(hits, "ISHA", "Titre")["title"] == "Titre"

    def test_objet_artiste_ignore(self, client):
        """Avec type=both, le 1er résultat peut être un artiste (sans tempo ni
        key_of) : c'est précisément ce que `search[0]` prenait à tort."""
        hits = [{"name": "ISHA", "id": "a1"}, _song()]
        assert client._select_hit(hits, "ISHA", "Titre")["id"] == "s1"

    def test_artiste_different_ecarte(self, client):
        hits = [_song(artiste="Un Autre")]
        assert client._select_hit(hits, "ISHA", "Titre") is None

    def test_titre_contenu_sert_de_repli(self, client):
        hits = [_song(titre="Titre (Remix)")]
        assert client._select_hit(hits, "ISHA", "Titre")["title"] == "Titre (Remix)"

    def test_match_parfait_prime_sur_le_repli(self, client):
        hits = [_song(titre="Titre (Remix)"), _song(titre="Titre")]
        assert client._select_hit(hits, "ISHA", "Titre")["title"] == "Titre"

    @pytest.mark.parametrize(
        ("cherche", "propose"),
        [("OG", "Yoga"), ("Quoi", "Pourquoi"), ("Pop", "Épopée"), ("Gang", "Gangrène")],
    )
    def test_repli_refuse_une_sous_chaine_intra_mot(self, client, cherche, propose):
        """Le repli acceptait l'inclusion NUE : l'artiste étant ancré strictement,
        c'est un AUTRE morceau du MÊME artiste qui était retenu — et son BPM
        écrit sur le nôtre, sans que rien ne le signale."""
        hits = [_song(titre=propose)]
        assert client._select_hit(hits, "ISHA", cherche) is None

    @pytest.mark.parametrize(
        ("cherche", "propose"),
        [("Titre", "Titre Intro"), ("CEO", "CEO Bonus"), ("Toi", "À cause de toi")],
    )
    def test_repli_conserve_l_inclusion_en_mot_entier(self, client, cherche, propose):
        hits = [_song(titre=propose)]
        assert client._select_hit(hits, "ISHA", cherche)["title"] == propose

    def test_artiste_en_liste(self, client):
        hits = [_song(artist=[{"name": "ISHA"}])]
        assert client._select_hit(hits, "ISHA", "Titre") is not None

    def test_artiste_en_chaine(self, client):
        hits = [_song(artist="ISHA")]
        assert client._select_hit(hits, "ISHA", "Titre") is not None

    def test_comparaison_insensible_aux_accents_et_apostrophes(self, client):
        hits = [_song(artiste="L’Or du Commun", titre="Éléphant")]
        assert client._select_hit(hits, "L'Or du Commun", "Elephant") is not None

    @pytest.mark.parametrize("hits", [[], [None], ["texte"], [{"title": "T"}]])
    def test_entrees_inexploitables(self, client, hits):
        assert client._select_hit(hits, "ISHA", "Titre") is None

    def test_hit_sans_tempo_mais_avec_key_accepte(self, client):
        """`tempo` OU `key_of` suffit à reconnaître un objet 'song'."""
        hits = [{"title": "Titre", "key_of": "Em", "artist": {"name": "ISHA"}}]
        assert client._select_hit(hits, "ISHA", "Titre") is not None


class TestParametresDeRequete:
    def test_format_lookup_documente(self, client):
        _, _, params = client._lookup_params("ISHA", "Titre")
        assert params["lookup"] == "song:Titre artist:ISHA"
        assert params["type"] == "both"
        assert params["api_key"] == "cle-de-test"

    def test_apostrophes_typographiques_redressees(self, client):
        """L'API ne matche pas l'apostrophe typographique."""
        artiste, titre, params = client._lookup_params("L’Or", "C’est")
        assert artiste == "L'Or" and titre == "C'est"
        assert "’" not in params["lookup"]


class TestSelectionDansLaReponse:
    def test_hit_valide(self, client):
        assert client._selected_from_response({"search": [_song()]}, "ISHA", "Titre")["id"] == "s1"

    def test_hits_sans_correspondance(self, client):
        data = {"search": [_song(artiste="Un Autre")]}
        assert client._selected_from_response(data, "ISHA", "Titre") is None

    @pytest.mark.parametrize("data", [{}, {"search": []}, {"search": "texte"}])
    def test_structures_inattendues(self, client, data):
        assert client._selected_from_response(data, "ISHA", "Titre") is None


class TestConstructionDuSongData:
    def test_champs_extraits(self, client):
        song = client._song_from_track_data(
            "ISHA", "Titre", _song(time_sig="4/4", open_key="9m", danceability=70)
        )

        assert song.bpm == 142 and song.key == "Em" and song.mode == "minor"
        assert song.time_signature == "4/4" and song.open_key == "9m"
        assert song.error is None

    def test_genres_de_lartiste(self, client):
        hit = _song(artist={"name": "ISHA", "genres": ["rap", "hip hop"]})
        assert client._song_from_track_data("ISHA", "Titre", hit).genres == ["rap", "hip hop"]

    def test_artiste_non_dictionnaire(self, client):
        assert client._song_from_track_data("ISHA", "Titre", _song(artist="ISHA")).genres is None

    def test_absence_de_hit(self, client):
        song = client._song_from_track_data("ISHA", "Inconnu", None)
        assert song.bpm is None and song.error


class TestCache:
    def test_cle_insensible_a_la_casse_et_aux_espaces(self, client):
        assert client._get_cache_key(" ISHA ", "Titre") == client._get_cache_key("isha", "titre")

    def test_fichier_illisible_donne_un_cache_vide(self, tmp_path):
        chemin = tmp_path / "casse.json"
        chemin.write_text("{ pas du json", encoding="utf-8")
        assert GetSongBPMFetcher(api_key="k", cache_file=str(chemin)).cache == {}

    def test_succes_servi_par_le_cache(self, client):
        client._song_from_track_data("ISHA", "Titre", _song())
        assert client._cached_song("ISHA", "Titre").bpm == 142

    def test_echec_precedent_non_definitif(self, client):
        """Un morceau absent aujourd'hui peut être ajouté demain : l'échec en
        cache ne doit PAS court-circuiter la prochaine recherche."""
        client._song_from_track_data("ISHA", "Inconnu", None)

        assert client.cache[client._get_cache_key("ISHA", "Inconnu")]["error"]
        assert client._cached_song("ISHA", "Inconnu") is None

    def test_entree_absente(self, client):
        assert client._cached_song("ISHA", "Jamais vu") is None


class TestFetchTrackBpm:
    def test_recherche_puis_mise_en_cache(self, client, monkeypatch):
        appels = []

        def fake_search(artist, title):
            appels.append((artist, title))
            return _song()

        monkeypatch.setattr(client, "_search_track", fake_search)

        assert client.fetch_track_bpm("ISHA", "Titre").bpm == 142
        client.fetch_track_bpm("ISHA", "Titre")
        assert len(appels) == 1  # le 2e passage est servi par le cache

    def test_morceau_introuvable(self, client, monkeypatch):
        monkeypatch.setattr(client, "_search_track", lambda *a: None)

        song = client.fetch_track_bpm("ISHA", "Inconnu")
        assert song.bpm is None and song.error

    def test_jumeau_async_meme_cache_meme_verdict(self, client, monkeypatch):
        """La voie ASYNC est celle de l'app : elle n'était pas couverte alors que
        la sync l'était — le symptôme de divergence à chercher (2026-09-05)."""
        import asyncio

        from src.observability import source_usage
        from src.observability.issues import IssueKind

        source_usage.reset()
        appels = []

        async def fake_search(http, artist, title):
            appels.append(title)
            return _song(titre=title) if title == "Titre" else None

        monkeypatch.setattr(client, "_search_track_async", fake_search)
        song = asyncio.run(client.fetch_track_bpm_async(None, "ISHA", "Titre"))
        assert song.bpm == 142
        asyncio.run(client.fetch_track_bpm_async(None, "ISHA", "Titre"))
        assert appels == ["Titre"]  # servi par le cache, aucune observation
        inconnu = asyncio.run(client.fetch_track_bpm_async(None, "ISHA", "Inconnu"))
        assert inconnu.bpm is None and inconnu.error
        # Le succès sort en INDETERMINATE : le faux `_search_track_async` ne
        # passe pas par `AsyncHttpSession`, seul capteur de transport de cette
        # voie — un trou de capteur se signale lui-même, on ne le maquille pas.
        assert [v.issue for v in source_usage.flush()] == [
            IssueKind.INDETERMINATE,
            IssueKind.ABSENT,
        ]


class TestAttribution:
    def test_backlink_obligatoire(self, client):
        """Condition de l'usage gratuit : ne jamais retirer le backlink."""
        assert "getsongbpm.com" in client.get_attribution_html()


# ── `_search_track` et son jumeau async : mêmes retries, mêmes verdicts ─────
class _Reponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.headers = {}
        self._payload = payload or {}

    def json(self):
        return self._payload


def _hit_isha():
    return {"search": [_song()]}


class TestSearchTrackJumeaux:
    """Le piège des jumeaux (2026-09-05) : une correction posée sur une seule
    voie ne corrige rien. Chaque cas est joué sur les DEUX."""

    def _sync(self, client, monkeypatch, reponses):
        import src.api.getsongbpm_api as mod

        attentes = []
        monkeypatch.setattr(mod.time, "sleep", attentes.append)
        it = iter(reponses)

        def get(url, params=None, timeout=None):
            r = next(it)
            if isinstance(r, Exception):
                raise r
            return r

        client.session = type("S", (), {"get": staticmethod(get)})()
        return client._search_track("ISHA", "Titre"), attentes

    def _async(self, client, monkeypatch, reponses):
        import asyncio

        import src.api.getsongbpm_api as mod

        attentes = []

        async def sleep(s):
            attentes.append(s)

        monkeypatch.setattr(mod.asyncio, "sleep", sleep)
        it = iter(reponses)

        class _Http:
            async def get(self, url, params=None, headers=None, timeout=None):
                r = next(it)
                if isinstance(r, Exception):
                    raise r
                return r

        return asyncio.run(client._search_track_async(_Http(), "ISHA", "Titre")), attentes

    @pytest.mark.parametrize("voie", ["sync", "async"])
    def test_200_rend_le_hit_valide(self, client, monkeypatch, voie):
        hit, _ = getattr(self, f"_{voie}")(client, monkeypatch, [_Reponse(200, _hit_isha())])
        assert hit and hit["title"] == "Titre"

    @pytest.mark.parametrize("voie", ["sync", "async"])
    def test_429_attend_puis_reessaie(self, client, monkeypatch, voie):
        hit, attentes = getattr(self, f"_{voie}")(
            client, monkeypatch, [_Reponse(429), _Reponse(429), _Reponse(200, _hit_isha())]
        )
        assert hit and attentes == [10, 20]  # 10 s × (tentative + 1)

    @pytest.mark.parametrize("voie", ["sync", "async"])
    def test_429_epuise_rend_none(self, client, monkeypatch, voie):
        hit, attentes = getattr(self, f"_{voie}")(client, monkeypatch, [_Reponse(429)] * 3)
        assert hit is None and attentes == [10, 20, 30]

    @pytest.mark.parametrize("voie", ["sync", "async"])
    @pytest.mark.parametrize("status", [401, 404, 500])
    def test_autres_statuts_sortent_sans_reessayer(self, client, monkeypatch, voie, status):
        hit, attentes = getattr(self, f"_{voie}")(client, monkeypatch, [_Reponse(status)])
        assert hit is None and attentes == []

    def test_reseau_backoff_exponentiel_sync(self, client, monkeypatch):
        import requests

        erreurs = [requests.ConnectionError("x")] * 2 + [_Reponse(200, _hit_isha())]
        hit, attentes = self._sync(client, monkeypatch, erreurs)
        assert hit and attentes == [1, 2]
        hit, attentes = self._sync(client, monkeypatch, [requests.ConnectionError("x")] * 3)
        assert hit is None and attentes == [1, 2]  # pas d'attente après le dernier

    def test_reseau_backoff_exponentiel_async(self, client, monkeypatch):
        import httpx

        erreurs = [httpx.ConnectError("x")] * 2 + [_Reponse(200, _hit_isha())]
        hit, attentes = self._async(client, monkeypatch, erreurs)
        assert hit and attentes == [1, 2]
        hit, attentes = self._async(client, monkeypatch, [httpx.ConnectError("x")] * 3)
        assert hit is None and attentes == [1, 2]
