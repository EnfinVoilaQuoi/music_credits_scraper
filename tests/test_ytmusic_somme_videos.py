"""`ytm_streams` = somme DÉDUPLIQUÉE par videoId de toutes les vidéos connues.

Le défaut réparé le 2026-09-07 : le clip officiel d'un morceau porte un videoId
différent de sa version audio et ne vit pas sur le canal « - Topic ». Il n'était
additionné que pour les feats repérés par Kworb (`spotify_streams` renseigné) —
les vues du clip d'un morceau d'album n'étaient donc comptées nulle part, alors
que le lien était en base depuis Genius.

Tout est à stubs : aucun appel réseau, aucune base.
"""

from types import SimpleNamespace

import pytest

import src.utils.update_ytmusic as mod
from src.models import TrackVideo

_ARTIST = SimpleNamespace(id=1, name="Isha")


def _track(idx, title, *, album="Alb", youtube_url=None, videos=None, spotify_streams=None):
    return SimpleNamespace(
        id=idx,
        title=title,
        album=album,
        streams=SimpleNamespace(spotify_streams=spotify_streams),
        is_featuring=False,
        primary_artist_name=None,
        youtube_url=youtube_url,
        youtube_url_source="genius_media" if youtube_url else None,
        videos=videos or [],
    )


class FakeAPI:
    """Canal à UN album, et un compteur de vues par videoId."""

    def __init__(self, raw_tracks, vues):
        self.raw_tracks = raw_tracks
        self.vues = vues
        self.batches = []

    def get_artist_channel_candidates(self, name):
        return []

    def get_artist_info(self, cid):
        return {"albums": [{"title": "Alb", "browseId": "B"}], "monthly_listeners": None}

    def get_album_tracks_raw(self, browse_id):
        return self.raw_tracks

    def fetch_view_counts_batch(self, ids):
        self.batches.append(sorted(ids))
        return {v: self.vues[v] for v in ids if v in self.vues}

    @staticmethod
    def resolve_streams(entry, view_counts):
        return view_counts.get(entry.get("video_id"))


class FakeDM:
    def __init__(self, tracks):
        self._tracks = tracks
        self.stream_writes = []
        self.album_writes = []
        self.video_writes = []

    def get_artist_ytm_channel_info(self, artist_id):
        return ("UCok", "manual")

    def get_artist_tracks(self, artist_id):
        return self._tracks

    def set_artist_ytm_channel(self, artist_id, channel_id, source="manual"):
        return True

    def clear_artist_ytm_channel(self, artist_id):
        return True

    def update_artist_monthly_listeners(self, artist_id, **kwargs):
        return True

    def update_track_ytm_streams(self, track_id, total):
        self.stream_writes.append((track_id, total))
        return True

    def update_album_ytm_streams(self, artist_id, album_title, total):
        self.album_writes.append((album_title, total))
        return True

    def record_track_videos(self, track_id, videos):
        self.video_writes.append((track_id, sorted(v.video_id for v in videos)))
        return len(videos)


@pytest.fixture
def sans_recherche(monkeypatch):
    """Neutralise la découverte live (étape 4) : ces tests portent sur la SOMME."""
    monkeypatch.setattr(
        mod, "_infer_channel_from_youtube_links", lambda *a, **k: pytest.fail("canal épinglé")
    )
    yield


def _lancer(api, tracks):
    dm = FakeDM(tracks)
    result = mod.update_ytmusic_streams(_ARTIST, dm, api=api)
    return dm, result


def test_le_clip_sajoute_a_laudio_du_canal(sans_recherche):
    """Le cas « Magot » : 500 (audio du canal) + 1 000 (clip Genius) = 1 500.
    Avant le correctif, seules les 500 étaient écrites."""
    api = FakeAPI(
        raw_tracks=[
            {"title": "Magot", "video_id": "audioaudioa", "views_str": None},
            {"title": "Autre", "video_id": "bbbbbbbbbbb", "views_str": None},
        ],
        vues={"audioaudioa": 500, "bbbbbbbbbbb": 10, "clipclipcli": 1000},
    )
    tracks = [
        _track(1, "Magot", youtube_url="https://youtu.be/clipclipcli"),
        _track(2, "Autre"),
    ]

    dm, result = _lancer(api, tracks)

    assert dict(dm.stream_writes)[1] == 1500
    assert result["multi_video"] == 1


def test_une_video_partagee_nest_comptee_quune_fois(sans_recherche):
    """Si le lien Genius EST la vidéo audio du canal, la somme ne double pas —
    c'est le dictionnaire indexé par videoId qui le garantit, pas une vigilance
    à l'appel."""
    api = FakeAPI(
        raw_tracks=[{"title": "Magot", "video_id": "audioaudioa", "views_str": None}],
        vues={"audioaudioa": 500},
    )
    tracks = [_track(1, "Magot", youtube_url="https://youtu.be/audioaudioa")]

    dm, result = _lancer(api, tracks)

    assert dm.stream_writes == [(1, 500)]
    assert result["multi_video"] == 0
    # Et la vidéo déjà chiffrée par la passe canal n'est pas redemandée.
    assert api.batches == [["audioaudioa"]]


def test_les_videos_deja_connues_en_base_sont_additionnees(sans_recherche):
    """`track.videos` (table `track_videos`) est une source de vidéos au même
    titre que `youtube_url` : un audio trouvé lors d'un run précédent compte."""
    api = FakeAPI(
        raw_tracks=[{"title": "Magot", "video_id": "audioaudioa", "views_str": None}],
        vues={"audioaudioa": 500, "clipclipcli": 1000, "trouveetrou": 250},
    )
    tracks = [
        _track(
            1,
            "Magot",
            youtube_url="https://youtu.be/clipclipcli",
            videos=[TrackVideo(video_id="trouveetrou", source="search_auto")],
        )
    ]

    dm, _ = _lancer(api, tracks)

    assert dm.stream_writes == [(1, 1750)]


def test_un_morceau_hors_canal_est_couvert_par_son_lien(sans_recherche):
    """Un morceau absent du canal (feat sur l'album d'un autre) n'a que son
    lien : il est compté sans que Kworb ait eu à le repérer."""
    api = FakeAPI(raw_tracks=[], vues={"clipclipcli": 900})
    tracks = [_track(1, "Feat", youtube_url="https://youtu.be/clipclipcli")]

    dm, result = _lancer(api, tracks)

    assert dm.stream_writes == [(1, 900)]
    assert result["feats_covered"] == 1


def test_les_videos_vues_sont_enregistrees(sans_recherche):
    """La passe alimente `track_videos` — c'est ainsi que la vidéo audio d'un
    morceau devient connue pour les runs suivants."""
    api = FakeAPI(
        raw_tracks=[{"title": "Magot", "video_id": "audioaudioa", "views_str": None}],
        vues={"audioaudioa": 500, "clipclipcli": 1000},
    )
    tracks = [_track(1, "Magot", youtube_url="https://youtu.be/clipclipcli")]

    dm, _ = _lancer(api, tracks)

    assert dm.video_writes == [(1, ["audioaudioa", "clipclipcli"])]


def test_une_video_sans_compteur_nest_pas_sommee(sans_recherche):
    """Un videoId absent du batch (vidéo supprimée, quota épuisé) ne contribue
    pas : mieux vaut un total sans elle qu'un total faux."""
    api = FakeAPI(
        raw_tracks=[{"title": "Magot", "video_id": "audioaudioa", "views_str": None}],
        vues={"audioaudioa": 500},  # le clip ne répond pas
    )
    tracks = [_track(1, "Magot", youtube_url="https://youtu.be/clipclipcli")]

    dm, _ = _lancer(api, tracks)

    assert dm.stream_writes == [(1, 500)]
    assert dm.video_writes == [(1, ["audioaudioa"])]


class TestSelectionDeMorceaux:
    """« Limiter aux morceaux cochés » : le quota et les écritures, pas le parcours."""

    def _api(self):
        return FakeAPI(
            raw_tracks=[
                {"title": "Coché", "video_id": "cocheococh", "views_str": None},
                {"title": "Ignoré", "video_id": "ignoreigno", "views_str": None},
            ],
            vues={"cocheococh": 500, "ignoreigno": 800},
        )

    def _tracks(self):
        return [_track(1, "Coché"), _track(2, "Ignoré")]

    def test_seules_les_ecritures_des_morceaux_coches_ont_lieu(self, sans_recherche):
        dm = FakeDM(self._tracks())
        mod.update_ytmusic_streams(_ARTIST, dm, api=self._api(), track_ids={1})
        assert dm.stream_writes == [(1, 500)]
        assert dm.video_writes == [(1, ["cocheococh"])]

    def test_le_batch_youtube_est_restreint(self, sans_recherche):
        """Demander des vues qu'on n'écrira pas coûterait du quota pour rien."""
        api = self._api()
        mod.update_ytmusic_streams(_ARTIST, FakeDM(self._tracks()), api=api, track_ids={1})
        assert api.batches[0] == ["cocheococh"]

    def test_le_parcours_du_canal_reste_entier(self, sans_recherche):
        """Le gate d'identité confronte la discographie COMPLÈTE du canal à la
        base : la restreindre lui ôterait ce qui lui permet de conclure."""
        result = mod.update_ytmusic_streams(
            _ARTIST, FakeDM(self._tracks()), api=self._api(), track_ids={1}
        )
        assert result["identity"]["ytm_titles"] == 2
        assert result["matched"] == 2  # les deux titres sont bien reconnus

    def test_aucun_total_dalbum_sous_selection(self, sans_recherche):
        """Il s'additionne sur tous les morceaux du disque, dont les compteurs ne
        sont plus demandés : l'écrire donnerait un total amputé."""
        dm = FakeDM(self._tracks())
        mod.update_ytmusic_streams(_ARTIST, dm, api=self._api(), track_ids={1})
        assert dm.album_writes == []

    def test_sans_selection_rien_ne_change(self, sans_recherche):
        dm = FakeDM(self._tracks())
        mod.update_ytmusic_streams(_ARTIST, dm, api=self._api())
        assert sorted(dm.stream_writes) == [(1, 500), (2, 800)]
        assert dm.album_writes == [("Alb", 1300)]
