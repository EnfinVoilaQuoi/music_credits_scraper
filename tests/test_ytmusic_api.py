"""Client YouTube Music (`ytmusic_api`) — résolution de canal, albums, streams.

289 statements à 21 %, 15 fonctions sur 17 jamais exécutées. Quatre fichiers de
test couvraient déjà la LOGIQUE de vote (`_channel_from_votes`) mais aucun ne
touchait aux méthodes qui l'utilisent : c'est pourtant là que se décide quel
canal YouTube est attribué à un artiste, donc quels streams atterrissent sur ses
morceaux.

Harnais : `YTMusicAPI.__new__` (le `__init__` réel instancie un client
`YTMusic()` qui interroge le réseau) + un faux client posé en `self.yt`.
"""

import pytest
import requests

from src.api.ytmusic_api import YTMusicAPI, _normalize, _parse_views


class _FauxYT:
    """Client ytmusicapi de test."""

    def __init__(self, search=None, artist=None, album=None):
        self._search = search if search is not None else []
        self._artist = artist or {}
        self._album = album or {}
        self.search_calls = []

    def search(self, query, filter=None, limit=None):  # noqa: A002 — signature ytmusicapi
        self.search_calls.append((query, filter))
        if isinstance(self._search, Exception):
            raise self._search
        return self._search

    def get_artist(self, channel_id):
        if isinstance(self._artist, Exception):
            raise self._artist
        return self._artist

    def get_album(self, browse_id):
        if isinstance(self._album, Exception):
            raise self._album
        return self._album


def _api(yt=None, avec_cle=False):
    api = YTMusicAPI.__new__(YTMusicAPI)
    api.yt = yt or _FauxYT()
    api._use_yt_api = avec_cle
    return api


# ──────────────────────────────────────────────────────────── fonctions pures


class TestNormalisation:
    def test_accents_et_casse(self):
        assert _normalize("Angèle") == "angele"

    def test_espaces_ecrases(self):
        assert _normalize("  Le   Rat  ") == "le rat"


class TestLectureDesVues:
    """Repli sans clé API : YTMusic ne donne qu'une valeur ARRONDIE et formatée."""

    @pytest.mark.parametrize(
        ("texte", "attendu"),
        [
            ("1,2 M", 1_200_000),
            ("500 k", 500_000),
            ("2 B", 2_000_000_000),
            ("3 Md", 3_000_000_000),
            ("1 234 567", 1_234_567),
            ("1,234,567", 1234567),
        ],
    )
    def test_formats(self, texte, attendu):
        assert _parse_views(texte) == attendu

    def test_espace_insecable(self):
        """YTMusic sépare avec \\xa0, pas un espace ordinaire."""
        assert _parse_views("1,2\xa0M") == 1_200_000

    def test_valeurs_illisibles(self):
        assert _parse_views("abc") is None
        assert _parse_views("") is None
        assert _parse_views("beaucoup M") is None


class TestResolutionDesStreams:
    def test_compte_exact_prioritaire(self):
        """Le viewCount de l'API v3 prime sur la valeur arrondie de ytmusicapi."""
        raw = {"video_id": "V1", "views_str": "1,2 M"}
        assert YTMusicAPI.resolve_streams(raw, {"V1": 1_234_567}) == 1_234_567

    def test_repli_sur_la_valeur_formatee(self):
        raw = {"video_id": "V1", "views_str": "1,2 M"}
        assert YTMusicAPI.resolve_streams(raw, {}) == 1_200_000

    def test_aucune_source(self):
        assert YTMusicAPI.resolve_streams({"video_id": "V1"}, {}) is None
        assert YTMusicAPI.resolve_streams({}, {}) is None


class TestFormatLRC:
    def test_lignes_horodatees(self):
        lignes = [{"text": "Premier", "start_time": 1500}, {"text": "Second", "start_time": 65230}]
        assert YTMusicAPI._format_lrc(lignes) == "[00:01.50]Premier\n[01:05.23]Second"

    def test_objets_avec_attributs(self):
        """ytmusicapi rend tantôt des dicts, tantôt des objets `LyricLine`."""

        class _Ligne:
            def __init__(self, text, start_time):
                self.text = text
                self.start_time = start_time

        assert YTMusicAPI._format_lrc([_Ligne("Texte", 1000)]) == "[00:01.00]Texte"

    def test_sans_aucun_horodatage(self):
        """Des paroles non synchronisées ne sont PAS du LRC : on rend None
        plutôt qu'un faux fichier synchronisé."""
        assert YTMusicAPI._format_lrc([{"text": "A"}, {"text": "B"}]) is None

    def test_lignes_partiellement_horodatees(self):
        lignes = [{"text": "Sans"}, {"text": "Avec", "start_time": 1000}]
        assert YTMusicAPI._format_lrc(lignes) == "Sans\n[00:01.00]Avec"

    def test_liste_vide(self):
        assert YTMusicAPI._format_lrc([]) is None


# ─────────────────────────────────────────────────────── résolution de canal


class TestResolutionDeCanal:
    @pytest.mark.parametrize(
        "valeur",
        [
            "UCabcdefghijklmnopqrstuv",
            "https://music.youtube.com/channel/UCabcdefghijklmnopqrstuv",
            "  UCabcdefghijklmnopqrstuv  ",
        ],
    )
    def test_identifiant_direct(self, valeur):
        assert _api().resolve_channel(valeur) == "UCabcdefghijklmnopqrstuv"

    def test_valeur_vide(self):
        assert _api().resolve_channel("") is None
        assert _api().resolve_channel(None) is None

    def test_format_non_reconnu(self):
        assert _api().resolve_channel("juste du texte") is None

    def test_handle_via_l_api(self, monkeypatch):
        class _Channels:
            def list(self, **kw):
                assert kw["forHandle"] == "@ISHAOfficiel"
                return self

            def execute(self):
                return {"items": [{"id": "UCaaaaaaaaaaaaaaaaaaaaaa"}]}

        class _Yt:
            def channels(self):
                return _Channels()

        monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **k: _Yt())
        api = _api(avec_cle=True)
        assert api.resolve_channel("@ISHAOfficiel") == "UCaaaaaaaaaaaaaaaaaaaaaa"

    def test_repli_sur_la_page_publique(self, monkeypatch):
        """Sans clé API (ou si elle échoue), on lit l'ID dans le HTML."""

        class _Reponse:
            text = '{"channelId":"UCbbbbbbbbbbbbbbbbbbbbbb"}'

        monkeypatch.setattr(requests, "get", lambda *a, **k: _Reponse())
        assert _api().resolve_channel("@ISHAOfficiel") == "UCbbbbbbbbbbbbbbbbbbbbbb"

    def test_api_en_echec_puis_page(self, monkeypatch):
        def _build_casse(*a, **k):
            raise OSError("quota dépassé")

        class _Reponse:
            text = '{"browseId":"UCccccccccccccccccccccc1"}'

        monkeypatch.setattr("googleapiclient.discovery.build", _build_casse)
        monkeypatch.setattr(requests, "get", lambda *a, **k: _Reponse())
        assert _api(avec_cle=True).resolve_channel("@X") == "UCccccccccccccccccccccc1"

    def test_page_muette(self, monkeypatch):
        class _Reponse:
            text = "<html>rien d'utile</html>"

        monkeypatch.setattr(requests, "get", lambda *a, **k: _Reponse())
        assert _api().resolve_channel("@X") is None

    def test_page_inaccessible(self, monkeypatch):
        def _boum(*a, **k):
            raise requests.RequestException("timeout")

        monkeypatch.setattr(requests, "get", _boum)
        assert _api().resolve_channel("@X") is None


class TestDeductionDeCanalParVote:
    def _api_avec_videos(self, monkeypatch, items):
        class _Videos:
            def list(self, **kw):
                return self

            def execute(self):
                return {"items": items}

        class _Yt:
            def videos(self):
                return _Videos()

        monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **k: _Yt())
        return _api(avec_cle=True)

    def _item(self, cid, nom="Chaîne"):
        return {"snippet": {"channelId": cid, "channelTitle": nom}}

    def test_pluralite_nette(self, monkeypatch):
        api = self._api_avec_videos(
            monkeypatch,
            [self._item("UC1"), self._item("UC1"), self._item("UC1"), self._item("UC2")],
        )
        assert api.infer_channel_from_videos(["a", "b", "c", "d"]) == "UC1"

    def test_sans_cle_api(self):
        """Le vote coûte du quota : sans clé, on ne tente rien."""
        assert _api().infer_channel_from_videos(["a"]) is None

    def test_liste_vide(self):
        assert _api(avec_cle=True).infer_channel_from_videos([]) is None

    def test_items_sans_chaine_ignores(self, monkeypatch):
        """Une vidéo sans `channelId` ne vote pas — et ne crée pas de voix
        fantôme qui fausserait l'écart avec le second."""
        api = self._api_avec_videos(
            monkeypatch, [{"snippet": {}}, self._item("UC1"), self._item("UC1")]
        )
        assert api.infer_channel_from_videos(["a", "b", "c"]) == "UC1"

    def test_une_seule_voix_ne_suffit_pas(self, monkeypatch):
        """Règle de pluralité NETTE : au moins 2 voix, sinon on s'abstient."""
        api = self._api_avec_videos(monkeypatch, [self._item("UC1")])
        assert api.infer_channel_from_videos(["a"]) is None

    def test_egalite_en_tete_rejetee(self, monkeypatch):
        """À égalité, l'ordre serait arbitraire : on préfère ne pas trancher."""
        api = self._api_avec_videos(
            monkeypatch,
            [self._item("UC1"), self._item("UC1"), self._item("UC2"), self._item("UC2")],
        )
        assert api.infer_channel_from_videos(["a", "b", "c", "d"]) is None

    def test_api_en_echec(self, monkeypatch):
        def _build_casse(*a, **k):
            raise OSError("réseau")

        monkeypatch.setattr("googleapiclient.discovery.build", _build_casse)
        assert _api(avec_cle=True).infer_channel_from_videos(["a"]) is None


class TestCandidatsArtiste:
    def test_matchs_exacts_en_premier(self):
        """Homonymes : plusieurs artistes « Isha ». L'appelant essaie les
        candidats dans l'ordre, donc l'exact doit passer devant."""
        yt = _FauxYT(
            search=[
                {"browseId": "UC_approchant", "artist": "Isha Bel"},
                {"browseId": "UC_exact", "artist": "Isha"},
            ]
        )
        assert _api(yt).get_artist_channel_candidates("Isha")[0] == ("UC_exact", "Isha")

    def test_accents_indifferents(self):
        yt = _FauxYT(search=[{"browseId": "UC1", "artist": "Angèle"}])
        assert _api(yt).get_artist_channel_candidates("Angele")[0][0] == "UC1"

    def test_entrees_sans_identifiant_ecartees(self):
        yt = _FauxYT(search=[{"artist": "Sans id"}, {"browseId": "UC1", "artist": "Jul"}])
        assert len(_api(yt).get_artist_channel_candidates("Jul")) == 1

    def test_aucun_resultat(self):
        assert _api(_FauxYT(search=[])).get_artist_channel_candidates("Inconnu") == []

    def test_erreur_reseau(self):
        yt = _FauxYT(search=requests.RequestException("timeout"))
        assert _api(yt).get_artist_channel_candidates("Jul") == []

    def test_premier_candidat_expose(self):
        yt = _FauxYT(search=[{"browseId": "UC1", "artist": "Jul"}])
        assert _api(yt).get_artist_channel_id("Jul") == "UC1"

    def test_aucun_candidat(self):
        assert _api(_FauxYT(search=[])).get_artist_channel_id("Inconnu") is None


class TestInfosArtiste:
    def _artiste(self, albums=(), singles=(), monthly=None):
        data = {
            "albums": {"results": list(albums)},
            "singles": {"results": list(singles)},
        }
        if monthly is not None:
            data["monthlyListeners"] = monthly
        return _FauxYT(artist=data)

    def test_albums_et_singles_fusionnes(self):
        yt = self._artiste(
            albums=[{"title": "Album", "browseId": "B1"}],
            singles=[{"title": "Single", "browseId": "B2"}],
        )
        infos = _api(yt).get_artist_info("UC1")
        assert [a["title"] for a in infos["albums"]] == ["Album", "Single"]

    def test_entrees_sans_identifiant_ecartees(self):
        yt = self._artiste(albums=[{"title": "Sans id"}, {"title": "Ok", "browseId": "B1"}])
        assert len(_api(yt).get_artist_info("UC1")["albums"]) == 1

    def test_auditeurs_mensuels_entier(self):
        assert (
            _api(self._artiste(monthly=146000)).get_artist_info("UC1")["monthly_listeners"]
            == 146000
        )

    def test_auditeurs_mensuels_formates(self):
        """Selon les versions de ytmusicapi, le champ est un entier OU une
        chaîne formatée (« 146K »)."""
        assert (
            _api(self._artiste(monthly="146K")).get_artist_info("UC1")["monthly_listeners"]
            == 146000
        )

    def test_auditeurs_illisibles_n_empechent_pas_les_albums(self):
        """La récupération des albums ne doit jamais échouer à cause de ce champ."""
        yt = self._artiste(albums=[{"title": "A", "browseId": "B1"}], monthly="beaucoup")
        infos = _api(yt).get_artist_info("UC1")
        assert infos["monthly_listeners"] is None
        assert len(infos["albums"]) == 1

    def test_erreur_reseau(self):
        yt = _FauxYT(artist=requests.RequestException("timeout"))
        assert _api(yt).get_artist_info("UC1") == {"albums": [], "monthly_listeners": None}

    def test_albums_seuls(self):
        yt = self._artiste(albums=[{"title": "A", "browseId": "B1"}])
        assert _api(yt).get_artist_albums("UC1") == [{"title": "A", "browseId": "B1"}]

    def test_albums_erreur_reseau(self):
        yt = _FauxYT(artist=requests.RequestException("timeout"))
        assert _api(yt).get_artist_albums("UC1") == []


class TestMorceauxDAlbum:
    def test_extraction(self):
        yt = _FauxYT(album={"tracks": [{"title": "T1", "videoId": "V1", "views": "1,2 M"}]})
        assert _api(yt).get_album_tracks_raw("B1") == [
            {"title": "T1", "video_id": "V1", "views_str": "1,2 M"}
        ]

    def test_champs_absents(self):
        yt = _FauxYT(album={"tracks": [{}]})
        assert _api(yt).get_album_tracks_raw("B1") == [
            {"title": "", "video_id": None, "views_str": None}
        ]

    def test_album_vide(self):
        assert _api(_FauxYT(album={})).get_album_tracks_raw("B1") == []

    def test_erreur_reseau(self):
        yt = _FauxYT(album=requests.RequestException("timeout"))
        assert _api(yt).get_album_tracks_raw("B1") == []


class TestComptesDeVuesEnLot:
    def _api_avec_reponses(self, monkeypatch, reponses):
        appels = []

        class _Videos:
            def list(self, **kw):
                appels.append(kw["id"])
                return self

            def execute(self):
                r = reponses[len(appels) - 1]
                if isinstance(r, Exception):
                    raise r
                return r

        class _Yt:
            def videos(self):
                return _Videos()

        monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **k: _Yt())
        return _api(avec_cle=True), appels

    def _stats(self, *paires):
        return {"items": [{"id": i, "statistics": {"viewCount": str(v)}} for i, v in paires]}

    def test_lot_unique(self, monkeypatch):
        api, _ = self._api_avec_reponses(monkeypatch, [self._stats(("V1", 100), ("V2", 200))])
        assert api.fetch_view_counts_batch(["V1", "V2"]) == {"V1": 100, "V2": 200}

    def test_decoupage_en_lots_de_cinquante(self, monkeypatch):
        """1 unité de quota par requête : les lots de 50 sont ce qui rend
        l'opération soutenable (150 morceaux = 3 unités sur 10 000/jour)."""
        ids = [f"V{i}" for i in range(120)]
        api, appels = self._api_avec_reponses(
            monkeypatch, [self._stats(), self._stats(), self._stats()]
        )
        api.fetch_view_counts_batch(ids)
        assert len(appels) == 3
        assert len(appels[0].split(",")) == 50
        assert len(appels[2].split(",")) == 20

    def test_doublons_dedupliques(self, monkeypatch):
        api, appels = self._api_avec_reponses(monkeypatch, [self._stats(("V1", 100))])
        api.fetch_view_counts_batch(["V1", "V1", "V1"])
        assert appels == ["V1"]

    def test_liste_vide(self):
        assert _api(avec_cle=True).fetch_view_counts_batch([]) == {}

    def test_sans_cle_api(self):
        assert _api().fetch_view_counts_batch(["V1"]) == {}

    def test_un_lot_en_echec_n_annule_pas_les_autres(self, monkeypatch):
        api, _ = self._api_avec_reponses(
            monkeypatch,
            [OSError("quota"), self._stats(("V60", 600))],
        )
        resultat = api.fetch_view_counts_batch([f"V{i}" for i in range(50)] + ["V60"])
        assert resultat == {"V60": 600}

    def test_statistiques_absentes(self, monkeypatch):
        """Une vidéo privée ou supprimée n'a pas de compteur."""
        api, _ = self._api_avec_reponses(monkeypatch, [{"items": [{"id": "V1", "statistics": {}}]}])
        assert api.fetch_view_counts_batch(["V1"]) == {}
