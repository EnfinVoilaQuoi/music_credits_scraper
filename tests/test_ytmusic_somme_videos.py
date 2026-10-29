"""`ytm_streams` = somme DÉDUPLIQUÉE par videoId de toutes les vidéos connues.

Depuis le 2026-10-20, le run n'écrit plus ce total : il enregistre des FAITS
(vidéos et vues exactes, compteurs sans mesure exacte) et le repository calcule
le total. `FakeDM.totaux()` applique la règle du repository (vidéos propres
mesurées + compteurs) à ce que le run a enregistré.

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


def _track(
    idx,
    title,
    *,
    album="Alb",
    youtube_url=None,
    videos=None,
    spotify_streams=None,
    spotify_id=None,
    deezer_id=None,
    durations_observees=None,
):
    return SimpleNamespace(
        durations_observees=durations_observees or {},
        id=idx,
        genius_id=None,
        artist=SimpleNamespace(name="Isha"),
        spotify_id=spotify_id,
        deezer_id=deezer_id,
        title=title,
        album=album,
        duration=None,
        streams=SimpleNamespace(spotify_streams=spotify_streams, ytm_streams=None),
        is_featuring=False,
        primary_artist_name=None,
        featured_artists=None,
        get_music_credits=list,
        youtube_url=youtube_url,
        youtube_url_source="genius_media" if youtube_url else None,
        videos=videos or [],
    )


class FakeAPI:
    """Canal à UN album, et un compteur de vues par videoId."""

    def __init__(self, raw_tracks, vues, details=None):
        self.raw_tracks = raw_tracks
        self.vues = vues
        self.details = details or {}
        self.batches = []

    def get_artist_channel_candidates(self, name):
        return []

    def get_artist_info(self, cid):
        return {"albums": [{"title": "Alb", "browseId": "B"}], "monthly_listeners": None}

    def get_album_tracks_raw(self, browse_id):
        return self.raw_tracks

    def fetch_view_counts_batch(self, ids, details=None):
        self.batches.append(sorted(ids))
        if details is not None:
            details.update({v: self.details[v] for v in ids if v in self.details})
        return {v: self.vues[v] for v in ids if v in self.vues}

    @staticmethod
    def resolve_streams(entry, view_counts):
        # Repli : le compteur ARRONDI d'ytmusicapi (« 1,2 M »).
        return view_counts.get(entry.get("video_id"), entry.get("vues_arrondies"))


class FakeDM:
    def __init__(self, tracks):
        self._tracks = tracks
        self.album_writes = []
        self.video_writes = []
        self.durees = []
        self.vues = {}  # {track_id: {video_id: vues ou None}} enregistrées
        self.compteurs = {}  # {track_id: {clé: vues}} (`record_ytm_compteurs`)

    def totaux(self) -> dict:
        """La règle du repository (`_recalculer_vues_ytm`) sur les faits reçus :
        vidéos PROPRES mesurées + compteurs (sans videoId, ou vidéo propre non
        mesurée). Rien de comptable : None."""
        totaux = {}
        for tid in sorted(set(self.vues) | set(self.compteurs)):
            autres = {v for t, vids in self.vues.items() if t != tid for v in vids}
            propres = {v: n for v, n in self.vues.get(tid, {}).items() if v not in autres}
            comptees = [n for n in propres.values() if n is not None] + [
                n
                for cle, n in self.compteurs.get(tid, {}).items()
                if cle.startswith("_novid_") or (cle in propres and propres[cle] is None)
            ]
            totaux[tid] = sum(comptees) if comptees else None
        return totaux

    def get_artist_ytm_channel_info(self, artist_id):
        return ("UCok", "manual")

    def get_artist_tracks(self, artist_id):
        # Relu par les totaux d'album de fin de run, comme la vraie base.
        for t in self._tracks:
            t.streams.ytm_streams = self.totaux().get(t.id, t.streams.ytm_streams)
        return self._tracks

    def set_artist_ytm_channel(self, artist_id, channel_id, source="manual"):
        return True

    def clear_artist_ytm_channel(self, artist_id):
        return True

    def update_artist_monthly_listeners(self, artist_id, **kwargs):
        return True

    def record_ytm_compteurs(self, track_id, compteurs):
        self.compteurs[track_id] = dict(compteurs)
        return True

    def update_album_ytm_streams(self, artist_id, album_title, total):
        self.album_writes.append((album_title, total))
        return True

    def verdicts_revue(self, artist_id):
        return {}

    def recalculer_vues_ytm(self, track_id, exclues=frozenset()):
        return self.totaux().get(track_id)

    def record_duration_observation(self, track_id, secondes, source):
        self.durees.append((track_id, secondes, source))
        return True

    def retirer_durees_video(self, track_id, sauf=None):
        self.retraits = getattr(self, "retraits", []) + [(track_id, sauf)]
        return 1

    def forget_track_video(self, track_id, video_id):
        self.oublis = getattr(self, "oublis", []) + [(track_id, video_id)]
        self.vues.get(track_id, {}).pop(video_id, None)
        return True

    def clear_track_youtube_link(self, track_id):
        return True

    def record_track_videos(self, track_id, videos):
        self.video_writes.append((track_id, sorted(v.video_id for v in videos)))
        self.durees_videos = getattr(self, "durees_videos", {})
        for v in videos:
            if v.duration is not None:
                self.durees_videos[(track_id, v.video_id)] = v.duration
        vues = self.vues.setdefault(track_id, {})
        for v in videos:
            vues[v.video_id] = v.views if v.views is not None else vues.get(v.video_id)
        for t in self._tracks:
            if t.id == track_id:
                connues = {v.video_id for v in t.videos}
                t.videos = list(t.videos) + [v for v in videos if v.video_id not in connues]
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

    assert dm.totaux()[1] == 1500
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

    assert dm.totaux() == {1: 500}
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

    assert dm.totaux() == {1: 1750}


def test_un_morceau_hors_canal_est_couvert_par_son_lien(sans_recherche):
    """Un morceau absent du canal (feat sur l'album d'un autre) n'a que son
    lien : il est compté sans que Kworb ait eu à le repérer."""
    api = FakeAPI(raw_tracks=[], vues={"clipclipcli": 900})
    tracks = [_track(1, "Feat", youtube_url="https://youtu.be/clipclipcli")]

    dm, result = _lancer(api, tracks)

    assert dm.totaux() == {1: 900}
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

    assert dm.totaux() == {1: 500}
    assert dm.video_writes == [(1, ["audioaudioa"])]


def test_les_compteurs_sans_mesure_exacte_sont_des_faits(sans_recherche):
    """Une piste d'album sans videoId et une vidéo sans `viewCount` exact
    (compteur arrondi) sont comptées : le run les enregistre comme FAITS, la
    vidéo mesurée exactement non (ses vues sont dans `track_videos`)."""
    api = FakeAPI(
        raw_tracks=[
            {"title": "Magot", "video_id": None, "vues_arrondies": 1000},
            {"title": "Magot", "video_id": "arrondiarr", "vues_arrondies": 1_200_000},
            {"title": "Magot", "video_id": "exacteexac", "vues_arrondies": 9},
        ],
        vues={"exacteexac": 500},
    )

    dm, _ = _lancer(api, [_track(1, "Magot")])

    assert dm.compteurs == {1: {"_novid_Alb_magot": 1000, "arrondiarr": 1_200_000}}
    assert dm.totaux() == {1: 1000 + 1_200_000 + 500}


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
        assert dm.totaux() == {1: 500}
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
        assert dm.totaux() == {1: 500, 2: 800}
        assert dm.album_writes == [("Alb", 1300)]


class TestVideoPartagee:
    """Une vidéo rattachée à PLUSIEURS morceaux n'est comptée pour aucun.

    Mesuré sur la base réelle : 15 cas, 32 morceaux. Deux causes, et la règle
    vaut pour les deux — un lien Genius fautif (« Innocent » et « Interlude »
    de Jazzy Bazz), et un CLIP DOUBLE légitime (`iIHdTMHWAic` =
    « B.B. Jacques - Donjon & 2h22 », dont les deux morceaux existent par
    ailleurs séparément en audio). Dans les deux cas, attribuer les vues
    entières à chacun les MULTIPLIE : l'album « Honeymoon » affichait
    3 × 531 930 pour une vidéo vue 531 930 fois.
    """

    def test_la_regle_est_pure(self):
        assert mod.videos_partagees({1: {"a": 10}, 2: {"a": 10, "b": 5}}) == {"a"}
        assert mod.videos_partagees({1: {"a": 10}, 2: {"b": 5}}) == set()
        assert mod.videos_partagees({}) == set()

    def _clip_double(self):
        """« Donjon & 2h22 » : un clip pour deux morceaux, chacun ayant son audio."""
        api = FakeAPI(
            raw_tracks=[
                {"title": "Donjon", "video_id": "audiodonjon", "views_str": None},
                {"title": "2h22", "video_id": "audio2h22aa", "views_str": None},
            ],
            vues={"audiodonjon": 100, "audio2h22aa": 200, "clipdouble0": 5252528},
        )
        tracks = [
            _track(1, "Donjon", youtube_url="https://youtu.be/clipdouble0"),
            _track(2, "2h22", youtube_url="https://youtu.be/clipdouble0"),
        ]
        return api, tracks

    def test_le_clip_double_nest_compte_pour_aucun_des_deux(self, sans_recherche):
        dm, result = _lancer(*self._clip_double())
        # Chacun garde SON audio, et rien de la vidéo commune.
        assert dm.totaux() == {1: 100, 2: 200}
        assert result["vues_non_attribuees"] == 5252528

    def test_la_video_va_au_seul_morceau_qu_elle_nomme(self, sans_recherche):
        """Titre connu (e21) qui ne nomme qu'UN des deux morceaux : elle quitte
        l'autre et compte pour lui (2026-10-10, `porteurs_d_une_autre_version`) —
        avant, partagée, elle n'était comptée nulle part."""
        api, tracks = self._clip_double()
        tracks[0].videos = [TrackVideo(video_id="clipdouble0", title="B.B. Jacques - Donjon")]
        dm, result = _lancer(api, tracks)

        assert dm.oublis == [(2, "clipdouble0")]
        assert result["videos_partagees"] == []
        assert dm.totaux() == {1: 100 + 5252528, 2: 200}

    def test_sans_titre_connu_le_lien_suffit_a_verifier(self, sans_recherche):
        _, result = _lancer(*self._clip_double())
        (partagee,) = result["videos_partagees"]
        assert partagee["titre_video"] is None
        assert partagee["url"] == "https://www.youtube.com/watch?v=clipdouble0"

    def test_un_clip_double_evident_nest_pas_liste(self, sans_recherche):
        """Le titre de la vidéo nomme les deux morceaux : rien à vérifier — et
        toujours rien de compté (décision utilisateur 2026-09-22, A2H)."""
        api, tracks = self._clip_double()
        tracks[0].videos = [
            TrackVideo(video_id="clipdouble0", title="B.B. Jacques - Donjon & 2h22 (Clip)")
        ]
        dm, result = _lancer(api, tracks)
        assert result["videos_partagees"] == [] and result["partages_evidents"] == 1
        assert dm.totaux() == {1: 100, 2: 200}
        assert result["vues_non_attribuees"] == 5252528

    def test_des_doublons_de_fiche_ne_sont_pas_listes(self, sans_recherche):
        api = FakeAPI(raw_tracks=[], vues={"acoustique0": 900})
        tracks = [
            _track(
                1, "Les yeux dans les yeux (Acoustic)", youtube_url="https://youtu.be/acoustique0"
            ),
            _track(
                2,
                "Les yeux dans les yeux (Unplugged)",
                youtube_url="https://youtu.be/acoustique0",
            ),
        ]
        _, result = _lancer(api, tracks)
        assert result["videos_partagees"] == [] and result["partages_evidents"] == 1

    def test_un_morceau_sans_video_propre_nest_pas_ecrit_a_zero(self, sans_recherche):
        """0 se lirait comme « jamais écouté » : jamais écrit. Mais l'ancien total,
        qui comptait la vidéo partagée, est recalculé — VIDÉ faute de vidéo propre
        (2026-10-11 : 17 fiches gardaient 26 M de vues aussi comptées à l'album)."""
        api = FakeAPI(raw_tracks=[], vues={"partageeee0": 900})
        tracks = [
            _track(1, "Innocent", youtube_url="https://youtu.be/partageeee0"),
            _track(2, "Interlude", youtube_url="https://youtu.be/partageeee0"),
        ]
        dm, _ = _lancer(api, tracks)
        assert dm.totaux() == {1: None, 2: None}

    def test_la_regle_se_resout_quand_lambiguite_est_levee(self, sans_recherche):
        """Après un ✖️ Rejeter sur le mauvais lien, la vidéo n'est plus partagée
        et recompte pour le morceau qui la garde. Ce n'est pas une amputation."""
        api = FakeAPI(raw_tracks=[], vues={"partageeee0": 900})
        tracks = [_track(1, "Innocent", youtube_url="https://youtu.be/partageeee0")]
        dm, result = _lancer(api, tracks)
        assert dm.totaux() == {1: 900}
        assert result["videos_partagees"] == []


class TestTotalDalbum:
    def test_une_video_listee_sur_deux_pistes_ne_compte_quune_fois(self, sans_recherche):
        """Le clip double appartient à l'album UNE fois. L'additionner par piste
        gonflerait le total du disque — l'alerte de l'utilisateur, 2026-09-07."""
        api = FakeAPI(
            raw_tracks=[
                {"title": "Donjon", "video_id": "clipdouble0", "views_str": None},
                {"title": "2h22", "video_id": "clipdouble0", "views_str": None},
                {"title": "Autre", "video_id": "autreautre0", "views_str": None},
            ],
            vues={"clipdouble0": 1000, "autreautre0": 7},
        )
        tracks = [_track(1, "Donjon"), _track(2, "2h22"), _track(3, "Autre")]

        dm, _ = _lancer(api, tracks)

        # Décision 2026-10-10 : total d'album = morceaux + clips doubles, le clip
        # double compté UNE fois (il ne l'est par aucun des deux morceaux).
        assert dm.album_writes == [("Alb", 1007)]  # et non 2007


def test_un_rattachement_affirme_l_emporte_sur_une_recherche():
    """A2H (2026-09-24) : la vidéo « De juillet à septembre (Acoustic) » de
    l'album REWORKS avait été trouvée par RECHERCHE pour la fiche « (Live at AK
    Studios) » (session 2020) ; partagée, elle n'était comptée nulle part."""
    from src.utils.update_ytmusic import rattachements_de_recherche_a_retirer

    vid_counts = {1592: {"3Xm": 6158}, 9630: {"3Xm": 6158}, 7: {"clip": 10}, 8: {"clip": 10}}
    sources = {
        (1592, "3Xm"): "search_auto",
        (9630, "3Xm"): "ytm_album",
        (7, "clip"): "genius_media",  # clip double : deux rattachements affirmés
        (8, "clip"): "genius_media",
    }
    assert rattachements_de_recherche_a_retirer(vid_counts, sources) == [(1592, "3Xm")]


# ── Durée d'après les vidéos (2026-09-27) ────────────────────────────────────


def test_duree_de_l_audio_topic_et_repli_hors_plateformes(sans_recherche):
    """L'audio d'une chaîne Topic donne la durée (`youtube`) ; une vidéo
    ordinaire ne la donne que pour une fiche hors plateformes (`youtube_video`),
    et une vidéo partagée par deux fiches (clip double) ne la donne à aucune."""
    api = FakeAPI(
        raw_tracks=[{"title": "Magot", "video_id": "audioaudioa", "views_str": None}],
        vues={"audioaudioa": 5, "freestylefr": 7, "clipsurspot": 9, "clipdouble1": 3},
        details={
            "audioaudioa": {"duration": 193, "channel": "Isha - Topic"},
            "freestylefr": {
                "duration": 150,
                "channel": "Booska-P",
                "title": "Isha - Freestyle | Booska",
            },
            "clipsurspot": {"duration": 240, "channel": "ISHA"},
            "clipdouble1": {"duration": 400, "channel": "ISHA"},
        },
    )
    tracks = [
        _track(1, "Magot"),
        _track(2, "Freestyle", album=None, youtube_url="https://youtu.be/freestylefr"),
        _track(3, "Clip", spotify_id="SP", youtube_url="https://youtu.be/clipsurspot"),
        _track(4, "A", youtube_url="https://youtu.be/clipdouble1"),
        _track(5, "B", youtube_url="https://youtu.be/clipdouble1"),
    ]

    dm, result = _lancer(api, tracks)

    assert sorted(dm.durees) == [(1, 193, "youtube"), (2, 150, "youtube_video")]
    assert result["durees_youtube"] == 2


def test_chaque_video_garde_sa_duree(sans_recherche):
    """e62 : la durée lue de CHAQUE vidéo s'enregistre sur la vidéo — y compris
    celle d'une vidéo partagée, qui ne donne pourtant sa durée à aucune fiche :
    c'est elle qui départagera les pages au même titre. Une vidéo dont l'API
    ne dit pas la durée n'en reçoit aucune."""
    api = FakeAPI(
        raw_tracks=[{"title": "Magot", "video_id": "audioaudioa", "views_str": None}],
        vues={"audioaudioa": 5, "clipdouble1": 3, "sansduree11": 4},
        details={
            "audioaudioa": {"duration": 193, "channel": "Isha - Topic"},
            "clipdouble1": {"duration": 400, "channel": "ISHA"},
            "sansduree11": {"duration": None, "channel": "ISHA"},
        },
    )
    tracks = [
        _track(1, "Magot"),
        _track(4, "A", youtube_url="https://youtu.be/clipdouble1"),
        _track(5, "B", youtube_url="https://youtu.be/clipdouble1"),
        _track(6, "C", youtube_url="https://youtu.be/sansduree11"),
    ]

    dm, _ = _lancer(api, tracks)

    assert dm.durees_videos == {
        (1, "audioaudioa"): 193,
        (4, "clipdouble1"): 400,
        (5, "clipdouble1"): 400,
    }
    assert (6, ["sansduree11"]) in dm.video_writes


class TestDureeVideoPerimee:
    """2026-10-27 : une durée vidéo qu'un run passé avait déclarée restait
    quand le run suivant ne la justifiait plus — « La balade des indépendants
    (Remix) » 257 s d'une vidéo de FLYNTMC devant les 399 s de Spotify, « Feel
    A Way » 252 s sans plus aucune vidéo."""

    def test_vues_relues_sans_audio_topic_la_duree_part(self, sans_recherche):
        api = FakeAPI(
            raw_tracks=[],
            vues={"remixflynt": 9},
            details={
                "remixflynt": {
                    "duration": 257,
                    "channel": "FLYNTMC",
                    "title": "La balade des indépendants REMIX",
                }
            },
        )
        tracks = [
            _track(
                1,
                "La balade des indépendants (Remix)",
                spotify_id="SP",
                youtube_url="https://youtu.be/remixflynt",
                durations_observees={"youtube": 257, "spotify_web": 399},
            )
        ]
        dm, result = _lancer(api, tracks)
        assert dm.durees == []
        assert dm.retraits == [(1, None)]
        assert result["durees_youtube_retirees"] == 1

    def test_le_verdict_du_run_garde_sa_source_et_retire_l_autre(self, sans_recherche):
        api = FakeAPI(
            raw_tracks=[],
            vues={"freestylefr": 7},
            details={
                "freestylefr": {
                    "duration": 150,
                    "channel": "Booska-P",
                    "title": "Isha - Freestyle | Booska",
                }
            },
        )
        tracks = [
            _track(
                1,
                "Freestyle",
                album=None,
                youtube_url="https://youtu.be/freestylefr",
                durations_observees={"youtube": 140},
            )
        ]
        dm, _ = _lancer(api, tracks)
        assert dm.durees == [(1, 150, "youtube_video")]
        assert dm.retraits == [(1, "youtube_video")]

    def test_meme_source_rien_a_retirer(self, sans_recherche):
        api = FakeAPI(
            raw_tracks=[{"title": "Magot", "video_id": "audioaudioa", "views_str": None}],
            vues={"audioaudioa": 5},
            details={"audioaudioa": {"duration": 193, "channel": "Isha - Topic"}},
        )
        dm, _ = _lancer(api, [_track(1, "Magot", durations_observees={"youtube": 193})])
        assert dm.durees == [(1, 193, "youtube")]
        assert not hasattr(dm, "retraits")

    def test_une_video_sans_reponse_ne_retire_rien(self, sans_recherche):
        """Absent ≠ échec : l'API n'a pas rendu les détails de l'audio Topic
        connue de la fiche — la durée qu'il avait donnée reste."""
        api = FakeAPI(
            raw_tracks=[],
            vues={"clipclipcli": 9, "audioaudioa": 5},
            details={"clipclipcli": {"duration": 260, "channel": "ISHA", "title": "Magot"}},
        )
        tracks = [
            _track(
                1,
                "Magot",
                spotify_id="SP",
                youtube_url="https://youtu.be/clipclipcli",
                videos=[TrackVideo(video_id="audioaudioa")],
                durations_observees={"youtube": 193},
            )
        ]
        dm, _ = _lancer(api, tracks)
        assert not hasattr(dm, "retraits")

    def test_une_fiche_sans_aucune_video_perd_sa_duree_video(self, sans_recherche):
        api = FakeAPI(raw_tracks=[], vues={})
        tracks = [
            _track(1, "Feel A Way", durations_observees={"youtube": 252, "spotify_web": 213}),
            _track(2, "Sans durée vidéo", durations_observees={"spotify_web": 200}),
        ]
        dm, _ = _lancer(api, tracks)
        assert dm.retraits == [(1, None)]


def test_une_video_qui_ne_nomme_pas_le_morceau_ne_donne_pas_sa_duree(sans_recherche):
    """CORRECTIFS 1.10 : « LEGO » prenait les 3 181 s d'un Planète Rap entier,
    « Morts pour rien » la durée d'un reportage sans rapport."""
    api = FakeAPI(
        raw_tracks=[],
        vues={"planeterap1": 9, "reportage01": 9},
        details={
            "planeterap1": {
                "duration": 3181,
                "channel": "Skyrock",
                "title": '#PlanèteRap BB Jacques "BlackBird" avec Fred Musa ! (1-5)',
            },
            "reportage01": {"duration": 3823, "channel": "X", "title": "zied&Bouna"},
        },
    )
    tracks = [
        _track(1, "LEGO", youtube_url="https://youtu.be/planeterap1"),
        _track(2, "Morts pour rien", youtube_url="https://youtu.be/reportage01"),
    ]

    dm, _ = _lancer(api, tracks)

    assert dm.durees == []


def test_une_video_partagee_va_a_la_fiche_qu_elle_designe():
    """2026-10-10 : 21 vidéos partagées (648 M de vues) n'étaient comptées par
    aucune fiche alors que leur titre en désignait une seule."""
    from src.utils.update_ytmusic import porteurs_d_une_autre_version

    fiches = {
        1: SimpleNamespace(title="Trèfle d’Or"),
        2: SimpleNamespace(title="Trèfle d’Or (Version clip)"),
        3: SimpleNamespace(title="Outro (Labrador bleu)"),
        4: SimpleNamespace(title="OUTRO"),
        5: SimpleNamespace(title="Avec une rose"),
        6: SimpleNamespace(title="Masterclass"),
    }
    vid_counts = {1: {"T": 9}, 2: {"T": 9}, 3: {"O": 5}, 4: {"O": 5}, 5: {"C": 1}, 6: {"C": 1}}
    titres = {
        "T": "L'Or Du Commun - Trèfle D'or",
        "O": "Outro",
        "C": "A2H - Avec une rose / Masterclass",  # clip double : rien à retirer
    }
    assert sorted(porteurs_d_une_autre_version(vid_counts, titres, fiches)) == [
        (2, "T"),
        (3, "O"),
    ]
    # Une vidéo qui ne NOMME aucune de ses fiches (l'EP entier) ne va à aucune.
    ep = {7: SimpleNamespace(title="Hey Mama (Orchestral)"), 8: SimpleNamespace(title="I Wonder")}
    assert (
        porteurs_d_une_autre_version(
            {7: {"E": 2}, 8: {"E": 2}}, {"E": "Kanye West - Glow In The Dark EP"}, ep
        )
        == []
    )


class TestRechercheDUneAutreFiche:
    """Kanye « Champions » : la recherche rendait l'audio de « Champion »
    (Graduation), une autre fiche de l'artiste (2026-10-13)."""

    def _fiches(self):
        return [SimpleNamespace(id=1, title="Champions"), SimpleNamespace(id=2, title="Champion")]

    def test_le_titre_exact_d_une_autre_fiche_est_ecarte(self):
        champions, champion = self._fiches()
        assert mod.titre_d_une_autre_fiche(champions, "Champion", [champions, champion])

    def test_le_titre_de_la_fiche_reste(self):
        champions, champion = self._fiches()
        assert not mod.titre_d_une_autre_fiche(champions, "Champions", [champions, champion])
        assert not mod.titre_d_une_autre_fiche(champion, "Champion", [champions, champion])

    def test_sans_autre_fiche_la_tolerance_reste(self):
        champions, _ = self._fiches()
        assert not mod.titre_d_une_autre_fiche(champions, "Champion", [champions])


class TestVideosEchangees:
    """Diam's (2026-10-13) : la page d'album YTM rangeait chacune des deux
    vidéos sous la piste de l'autre. Seul l'échange MUTUEL est corrigé."""

    def _index(self, *fiches):
        from collections import defaultdict

        from src.utils.title_matching import normalize_title

        index = defaultdict(list)
        for f in fiches:
            index[normalize_title(f.title)].append(f)
        return index

    def test_l_echange_mutuel_est_rendu(self):
        face = SimpleNamespace(id=1, title="Face au vide")
        ombre = SimpleNamespace(id=2, title="À l’ombre de la lune")
        titres = {
            "A": "Diam's - À l'ombre de la lune (Audio officiel)",
            "B": "Diam's - Face au vide (Audio officiel)",
        }
        echanges = mod.videos_echangees(
            [(face, "A"), (ombre, "B")], titres, "Diam’s", self._index(face, ombre)
        )
        assert echanges == {"A": ombre, "B": face}

    def test_une_seule_vidéo_qui_nomme_une_autre_fiche_reste(self):
        """Le clip studio sur la piste live d'un album live : pas un échange."""
        live = SimpleNamespace(id=1, title="Dans ma bulle - Live 2006")
        studio = SimpleNamespace(id=2, title="Dans ma bulle")
        titres = {"C": "Diam's - Dans ma bulle (Clip officiel)"}
        assert (
            mod.videos_echangees([(live, "C")], titres, "Diam’s", self._index(live, studio)) == {}
        )

    def test_une_version_n_est_pas_nommee_par_l_original(self):
        live = SimpleNamespace(id=1, title="Confessions nocturnes - Live 2006")
        studio = SimpleNamespace(id=2, title="Confessions nocturnes")
        titre = "Diam's - Confessions nocturnes Feat. Vitaa (Live Au tour de ma bulle)"
        assert (
            mod.fiche_que_la_video_nomme(live, titre, "Diam’s", self._index(live, studio)) is None
        )


def test_un_album_bride_est_saute_et_ses_videos_connues_restent_mesurees(sans_recherche):
    """2026-10-18 : une page d'album à réponse VIDE (bridage YTM) levait un
    JSONDecodeError qui emportait tout le run YTM de l'artiste. L'album est
    sauté et compté ; la vidéo déjà connue de sa fiche reste mesurée."""
    api = FakeAPI(raw_tracks=None, vues={"audioaudioa": 500})
    tracks = [
        _track(
            1,
            "Magot",
            videos=[TrackVideo(video_id="audioaudioa", url="u", source="ytm_album")],
        )
    ]

    dm, result = _lancer(api, tracks)

    assert result["albums_brides"] == ["Alb"]
    assert result["albums_processed"] == 0
    assert dm.totaux() == {1: 500}


class TestVideoDeLaRecherche:
    """Option A (décision utilisateur 2026-10-20) : la recherche garde ce
    qu'elle a vu, et une vidéo qu'elle a posée ne compte que si ces faits
    passent le score COURANT."""

    def test_la_pose_garde_ce_que_la_recherche_a_vu(self, sans_recherche, monkeypatch):
        import src.youtube.youtube_searcher as ys

        class _Chercheur:
            def search_track(
                self, artist, title, max_results=5, album=None, artistes_connus=(), garde=None
            ):
                return [
                    {
                        "video_id": "trouveetrou",
                        "title": "Feat",
                        "channel_title": "Autre",
                        "artists": ["Autre", "Isha"],
                        "album": "Alb",
                        "relevance_score": 1.0,
                    }
                ]

        monkeypatch.setattr(ys, "YouTubeSearcher", _Chercheur)
        api = FakeAPI(raw_tracks=[], vues={"trouveetrou": 300})
        tracks = [_track(1, "Feat", spotify_streams=200_000)]
        dm = FakeDM(tracks)
        ecrites = []
        enregistrer = dm.record_track_videos
        monkeypatch.setattr(
            dm, "record_track_videos", lambda tid, v: ecrites.extend(v) or enregistrer(tid, v)
        )

        mod.update_ytmusic_streams(_ARTIST, dm, api=api)

        (video,) = ecrites
        assert video.source == "search_auto"
        assert video.recherche == {"titre": "Feat", "artistes": ["Autre", "Isha"], "album": "Alb"}

    def test_une_video_que_le_score_refuse_ne_compte_ni_ne_donne_sa_duree(self, sans_recherche):
        """Django « Chaos » ← le « Chaos » de Jango Jack (5:37) : ni vues, ni durée."""
        api = FakeAPI(
            raw_tracks=[],
            vues={"jangojackch": 400},
            details={"jangojackch": {"duration": 337, "channel": "Jango Jack - Topic"}},
        )
        jango = {"titre": "Chaos", "artistes": ["Jango Jack"], "album": None}
        tracks = [
            _track(
                1,
                "Chaos",
                videos=[TrackVideo(video_id="jangojackch", source="search_auto", recherche=jango)],
            )
        ]

        dm, result = _lancer(api, tracks)

        assert result["vues_ecartees"] == 400
        assert dm.durees == []


class TestTitreGenerique:
    """Un titre générique ne se rattache qu'à la fiche du même disque
    (2026-10-29 : l'« Outro » de *444 Nuits* posé sur celui de *Reste Calme*)."""

    def _api(self):
        return FakeAPI(
            raw_tracks=[{"title": "Outro", "video_id": "outrooutro0", "views_str": None}],
            vues={"outrooutro0": 500},
        )

    def test_l_outro_d_un_autre_disque_n_est_pas_rattache(self, sans_recherche):
        dm, _ = _lancer(self._api(), [_track(1, "Outro", album="Reste Calme")])
        assert dm.totaux().get(1) is None
        assert not dm.vues.get(1)

    def test_l_outro_du_meme_disque_l_est(self, sans_recherche):
        dm, _ = _lancer(self._api(), [_track(1, "Outro", album="Alb (Deluxe)")])
        assert dm.totaux() == {1: 500}

    def test_un_titre_ordinaire_garde_le_titre_seul(self, sans_recherche):
        api = FakeAPI(
            raw_tracks=[{"title": "Magot", "video_id": "magotmagot0", "views_str": None}],
            vues={"magotmagot0": 500},
        )
        dm, _ = _lancer(api, [_track(1, "Magot", album="Autre disque")])
        assert dm.totaux() == {1: 500}
