"""YTMusic : paroles (LRC) et batches YouTube Data API v3.

`test_ytmusic_api.py` couvre la résolution de canal et les albums. Ici, les deux
autres services rendus par ce client : les paroles SYNCHRONISÉES (source 2 des
timestamps, après LRCLIB) et les appels par lot à l'API YouTube Data v3, dont le
découpage en batches de 50 conditionne la consommation de quota.

Harnais : `YTMusicAPI.__new__` (le `__init__` réel instancie un client réseau)
+ faux clients injectés.
"""

import pytest
import requests

from src.api.ytmusic_api import YTMusicAPI


@pytest.fixture
def api():
    inst = YTMusicAPI.__new__(YTMusicAPI)
    inst._use_yt_api = False
    return inst


@pytest.fixture
def api_avec_cle(api):
    api._use_yt_api = True
    return api


class _YTLyrics:
    """Faux client ytmusicapi pour la chaîne paroles."""

    def __init__(self, search=None, watch=None, lyrics=None, lyrics_sync=None):
        self._search = search if search is not None else []
        self._watch = watch if watch is not None else {}
        self._lyrics = lyrics
        self._lyrics_sync = lyrics_sync

    def search(self, query, filter=None, limit=None):  # noqa: A002 — signature ytmusicapi
        return self._search

    def get_watch_playlist(self, videoId=None):  # noqa: N803 — signature ytmusicapi
        return self._watch

    def get_lyrics(self, browse_id, timestamps=False):
        if timestamps:
            if isinstance(self._lyrics_sync, Exception):
                raise self._lyrics_sync
            return self._lyrics_sync
        return self._lyrics


def _resultat(video_id="v1", artiste="ISHA"):
    return {"videoId": video_id, "artists": [{"name": artiste}]}


class TestFormatageLrc:
    def test_lignes_horodatees(self, api):
        lignes = [
            {"text": "Premiere", "start_time": 1500},
            {"text": "Deuxieme", "start_time": 62_000},
        ]
        assert api._format_lrc(lignes) == "[00:01.50]Premiere\n[01:02.00]Deuxieme"

    def test_aucun_horodatage_rend_none(self, api):
        """Sans une seule balise, ce n'est pas du LRC : mieux vaut None."""
        assert api._format_lrc([{"text": "Une ligne", "start_time": None}]) is None

    def test_lignes_objet(self, api):
        class _Ligne:
            def __init__(self, text, start_time):
                self.text = text
                self.start_time = start_time

        assert api._format_lrc([_Ligne("Texte", 0)]) == "[00:00.00]Texte"

    def test_lignes_mixtes(self, api):
        lignes = [{"text": "Sans ts", "start_time": None}, {"text": "Avec", "start_time": 1000}]
        assert api._format_lrc(lignes) == "Sans ts\n[00:01.00]Avec"


class TestParoles:
    """Le contrôle d'artiste évite d'attribuer les paroles d'un homonyme."""

    def test_paroles_synchronisees(self, api):
        api.yt = _YTLyrics(
            search=[_resultat()],
            watch={"lyrics": "browse1"},
            lyrics_sync={
                "lyrics": [{"text": "Une ligne", "start_time": 1000}],
                "source": "LyricFind",
            },
        )

        res = api.get_lyrics("ISHA", "Titre")
        assert res["lyrics_synced"] == "[00:01.00]Une ligne"
        assert res["lyrics"] == "Une ligne"
        assert res["source"] == "LyricFind"

    def test_repli_sur_texte_brut(self, api):
        """Certaines pistes n'ont pas de version synchronisée."""
        api.yt = _YTLyrics(
            search=[_resultat()],
            watch={"lyrics": "browse1"},
            lyrics_sync=requests.RequestException("pas de synchro"),
            lyrics={"lyrics": "Des paroles", "source": "Musixmatch"},
        )

        res = api.get_lyrics("ISHA", "Titre")
        assert res["lyrics"] == "Des paroles"
        assert res["lyrics_synced"] is None

    def test_aucun_resultat(self, api):
        api.yt = _YTLyrics(search=[])
        assert api.get_lyrics("ISHA", "Titre") is None


class TestDureeDeclaree:
    """Lot 3 (2026-09-22) : YTM rend `duration_seconds` du hit — SEULEMENT si
    son titre est bien celui cherché (l'artiste seul ne suffit pas pour une
    durée qui entre en arbitrage)."""

    def _hit(self, titre, secondes=203):
        return {**_resultat(), "title": titre, "duration_seconds": secondes}

    def test_duree_rendue_quand_le_titre_concorde(self, api):
        api.yt = _YTLyrics(
            search=[self._hit("Titre")],
            watch={"lyrics": "browse1"},
            lyrics_sync=requests.RequestException("pas de synchro"),
            lyrics={"lyrics": "Des paroles", "source": "Musixmatch"},
        )
        res = api.get_lyrics("ISHA", "Titre")
        assert res["duration"] == 203 and res["title"] == "Titre"

    def test_pas_de_duree_sur_un_titre_etranger(self, api):
        api.yt = _YTLyrics(
            search=[self._hit("Tueur de dragon (Vent)")],
            watch={"lyrics": "browse1"},
            lyrics_sync=requests.RequestException("pas de synchro"),
            lyrics={"lyrics": "Des paroles", "source": "Musixmatch"},
        )
        res = api.get_lyrics("ISHA", "Durag")
        assert res["lyrics"] == "Des paroles" and res["duration"] is None

    def test_descripteur_asymetrique_ne_donne_pas_de_duree(self, api):
        api.yt = _YTLyrics(
            search=[self._hit("Titre (Live)")],
            watch={"lyrics": "browse1"},
            lyrics_sync=requests.RequestException("pas de synchro"),
            lyrics={"lyrics": "Des paroles", "source": "Musixmatch"},
        )
        res = api.get_lyrics("ISHA", "Titre")
        assert res["lyrics"] == "Des paroles" and res["duration"] is None

    def test_un_morceau_sans_paroles_declare_quand_meme_sa_duree(self, api):
        api.yt = _YTLyrics(search=[self._hit("Titre")], watch={})
        res = api.get_lyrics("ISHA", "Titre")
        assert res == {
            "lyrics": None,
            "lyrics_synced": None,
            "source": None,
            "duration": 203,
            "title": "Titre",
        }
        # Sans durée ni paroles : None, comme avant.
        api.yt = _YTLyrics(search=[_resultat()], watch={})
        assert api.get_lyrics("ISHA", "Titre") is None

    def test_artiste_non_confirme(self, api):
        api.yt = _YTLyrics(search=[_resultat(artiste="Un Autre Rappeur")])
        assert api.get_lyrics("ISHA", "Titre") is None

    @pytest.mark.parametrize(
        ("cherche", "credite"),
        [
            ("Isha", "Misha Van Der Werf"),
            ("IAM", "Williams"),
            ("SCH", "ScHoolboy Q"),
            ("Jul", "Julien Doré"),
        ],
    )
    def test_homonyme_par_sous_chaine_refuse(self, api, cherche, credite):
        """Le contrôle comparait par SOUS-CHAÎNE nue jusqu'au 2026-09-05 : ces
        quatre résultats étaient acceptés, et leurs paroles — timestamps compris —
        écrites sur notre morceau. `test_artiste_non_confirme` ne le voyait pas :
        « Un Autre Rappeur » échoue déjà avec l'ancienne règle."""
        api.yt = _YTLyrics(
            search=[_resultat(artiste=credite)],
            watch={"lyrics": "b1"},
            lyrics_sync={"lyrics": "Les paroles de quelqu'un d'autre"},
        )
        assert api.get_lyrics(cherche, "Titre") is None

    @pytest.mark.parametrize(
        ("cherche", "credite"),
        [
            ("Jul", "Jul & SCH"),  # l'inclusion utile : un mot du tout
            ("Isha", "ISHA"),  # casse
            ("Isha", "Isha (7)"),  # suffixe de désambiguïsation Genius
            ("Limsa d'Aulnay", "Limsa d’Aulnay"),  # apostrophe typographique
        ],
    )
    def test_relachement_utile_preserve(self, api, cherche, credite):
        api.yt = _YTLyrics(
            search=[_resultat(artiste=credite)],
            watch={"lyrics": "b1"},
            lyrics_sync={"lyrics": "Nos paroles"},
        )
        assert api.get_lyrics(cherche, "Titre")["lyrics"] == "Nos paroles"

    def test_artiste_confirme_sur_un_credite_secondaire(self, api):
        """Le champ `artists` d'un résultat concatène tous les crédités : notre
        artiste peut n'être que le second."""
        api.yt = _YTLyrics(
            search=[{"videoId": "v1", "artists": [{"name": "Limsa d'Aulnay"}, {"name": "Isha"}]}],
            watch={"lyrics": "b1"},
            lyrics_sync={"lyrics": "Nos paroles"},
        )
        assert api.get_lyrics("Isha", "Titre")["lyrics"] == "Nos paroles"

    def test_resultat_sans_video_id(self, api):
        api.yt = _YTLyrics(search=[{"artists": [{"name": "ISHA"}]}])
        assert api.get_lyrics("ISHA", "Titre") is None

    def test_video_sans_paroles(self, api):
        api.yt = _YTLyrics(search=[_resultat()], watch={})
        assert api.get_lyrics("ISHA", "Titre") is None

    def test_paroles_vides(self, api):
        api.yt = _YTLyrics(
            search=[_resultat()], watch={"lyrics": "b1"}, lyrics_sync={"lyrics": "   "}
        )
        assert api.get_lyrics("ISHA", "Titre") is None

    def test_source_par_defaut(self, api):
        api.yt = _YTLyrics(
            search=[_resultat()], watch={"lyrics": "b1"}, lyrics_sync={"lyrics": "Texte"}
        )
        assert api.get_lyrics("ISHA", "Titre")["source"] == "YouTube Music"


class _FauxYoutubeApi:
    """Remplace `googleapiclient.discovery.build` : sert des `items` préparés."""

    def __init__(self, items_par_appel):
        self._items = list(items_par_appel)
        self.ids_demandes = []
        self.parts = []

    def videos(self):
        return self

    def list(self, part=None, id=None):  # noqa: A002 — signature googleapiclient
        self.ids_demandes.append(id.split(","))
        self.parts.append(part)
        return self

    def execute(self):
        if not self._items:
            return {"items": []}
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"items": item}


def _stub_build(monkeypatch, faux):
    import googleapiclient.discovery as disco

    monkeypatch.setattr(disco, "build", lambda *a, **k: faux)
    return faux


class TestVuesEnLot:
    """Chaque requête coûte une unité de quota : le découpage en lots de 50 et
    la déduplication sont ce qui rend une grosse discographie tenable."""

    def test_vues_recuperees(self, api_avec_cle, monkeypatch):
        _stub_build(
            monkeypatch, _FauxYoutubeApi([[{"id": "v1", "statistics": {"viewCount": "1000"}}]])
        )
        assert api_avec_cle.fetch_view_counts_batch(["v1"]) == {"v1": 1000}

    def test_liste_vide(self, api_avec_cle):
        assert api_avec_cle.fetch_view_counts_batch([]) == {}

    def test_sans_cle_api(self, api):
        assert api.fetch_view_counts_batch(["v1"]) == {}

    def test_doublons_dedupliques(self, api_avec_cle, monkeypatch):
        faux = _stub_build(monkeypatch, _FauxYoutubeApi([[]]))
        api_avec_cle.fetch_view_counts_batch(["v1", "v1", "v2"])

        assert faux.ids_demandes == [["v1", "v2"]]

    def test_decoupage_en_lots_de_50(self, api_avec_cle, monkeypatch):
        faux = _stub_build(monkeypatch, _FauxYoutubeApi([[], []]))
        api_avec_cle.fetch_view_counts_batch([f"v{i}" for i in range(60)])

        assert [len(lot) for lot in faux.ids_demandes] == [50, 10]

    def test_lot_en_erreur_ne_perd_pas_les_autres(self, api_avec_cle, monkeypatch):
        _stub_build(
            monkeypatch,
            _FauxYoutubeApi([OSError("reseau"), [{"id": "v51", "statistics": {"viewCount": "7"}}]]),
        )
        res = api_avec_cle.fetch_view_counts_batch([f"v{i}" for i in range(60)])

        assert res == {"v51": 7}

    def test_video_sans_statistiques(self, api_avec_cle, monkeypatch):
        _stub_build(monkeypatch, _FauxYoutubeApi([[{"id": "v1", "statistics": {}}]]))
        assert api_avec_cle.fetch_view_counts_batch(["v1"]) == {}


class TestMetaEnLot:
    """Même batch, mais `part="statistics,snippet"` — chantier Media."""

    def test_meta_recuperee(self, api_avec_cle, monkeypatch):
        faux = _stub_build(
            monkeypatch,
            _FauxYoutubeApi(
                [
                    [
                        {
                            "id": "v1",
                            "statistics": {"viewCount": "1000"},
                            "snippet": {"title": "Clip", "channelTitle": "ISHA"},
                        }
                    ]
                ]
            ),
        )
        res = api_avec_cle.fetch_video_meta_batch(["v1"])

        assert res["v1"]["views"] == 1000
        assert "snippet" in faux.parts[0]

    def test_liste_vide(self, api_avec_cle):
        assert api_avec_cle.fetch_video_meta_batch([]) == {}

    def test_sans_cle_api(self, api):
        assert api.fetch_video_meta_batch(["v1"]) == {}
