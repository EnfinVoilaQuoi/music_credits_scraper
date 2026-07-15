"""Gate d'identité YTMusic (src/utils/update_ytmusic) — étape 3 du durcissement.

Deux niveaux :
  - fonctions PURES (`_channel_identity_report`, `_identity_suspect`) : matching
    normalisé, dédup rééditions, planchers/ratios, repêchage par album ;
  - INTÉGRATION à stubs (`YTMusicAPI` + DataManager factices) : abort sans
    écriture sur mauvais canal inféré (+ dé-épinglage), écritures + pin 'inferred'
    sur bon canal, canal manuel divergent → warning + écritures + pin intact,
    jamais de ré-épinglage par-dessus un manuel.
"""

from types import SimpleNamespace

import src.utils.update_ytmusic as mod
from src.utils.title_matching import normalize_title as _n

# ── Fonctions pures ──────────────────────────────────────────────────────────


def _tba(*albums):
    """{albumTitle: [{'title': t}, ...]} depuis des listes de titres."""
    return {f"Alb{i}": [{"title": t} for t in titles] for i, titles in enumerate(albums)}


def test_report_matching_normalise():
    # Ponctuation / casse différentes → même titre normalisé, matché.
    db = {_n("Murder Inc")}
    report = mod._channel_identity_report(_tba(["MURDER INC."]), db, [], set())
    assert report["matched"] == 1
    assert report["ytm_titles"] == 1


def test_report_dedup_reeditions():
    # Le même titre sur 2 albums (rééditions) ne compte qu'une fois côté YTM.
    report = mod._channel_identity_report(_tba(["Song"], ["Song"]), {_n("Song")}, [], set())
    assert report["ytm_titles"] == 1
    assert report["matched"] == 1


def test_report_3_sur_90_suspect():
    db = {_n(f"s{i}") for i in range(3)}
    ytm = [f"s{i}" for i in range(3)] + [f"x{i}" for i in range(87)]  # 90 uniques
    report = mod._channel_identity_report(_tba(ytm), db, [], set())
    assert report["matched"] == 3
    assert report["ytm_titles"] == 90
    assert mod._identity_suspect(report) is True


def test_report_94_sur_110_ok():
    db = {_n(f"s{i}") for i in range(94)}
    ytm = [f"s{i}" for i in range(94)] + [f"x{i}" for i in range(16)]  # 110 uniques
    report = mod._channel_identity_report(_tba(ytm), db, [], set())
    assert report["matched"] == 94
    assert mod._identity_suspect(report) is False


def test_report_topic_8_sur_8_ok():
    # Canal « - Topic » complet mais niche : 8/8 de SES titres.
    db = {_n(f"s{i}") for i in range(8)}
    ytm = [f"s{i}" for i in range(8)]
    report = mod._channel_identity_report(_tba(ytm), db, [], set())
    assert report["ratio"] == 1.0
    assert mod._identity_suspect(report) is False


def test_ratio_faible_repeche_par_album_commun():
    # ratio 0.1 (2/20) MAIS un album entier en commun → repêché (non suspect).
    report = {"matched": 2, "ytm_titles": 20, "ratio": 0.1, "album_overlap": 1}
    assert mod._identity_suspect(report) is False


def test_un_seul_titre_commun_suspect():
    # matched < 2 → suspect même avec un album commun.
    report = {"matched": 1, "ytm_titles": 3, "ratio": 0.33, "album_overlap": 1}
    assert mod._identity_suspect(report) is True


def test_2_sur_5_ok():
    report = {"matched": 2, "ytm_titles": 5, "ratio": 0.4, "album_overlap": 0}
    assert mod._identity_suspect(report) is False


def test_report_album_overlap_normalise():
    ytm_albums = ["L'Album, Pt. 2"]
    db_albums = {_n("L'Album - Pt. 2")}
    report = mod._channel_identity_report(_tba(["x"]), set(), ytm_albums, db_albums)
    assert report["album_overlap"] == 1


# ── Intégration à stubs ──────────────────────────────────────────────────────


def _track(idx, title, album="Alb", spotify_streams=None):
    return SimpleNamespace(
        id=idx,
        title=title,
        album=album,
        spotify_streams=spotify_streams,
        is_featuring=False,
        primary_artist_name=None,
        youtube_url=None,
    )


def _raw(titles):
    return [{"title": t, "video_id": f"v_{t}", "views_str": None} for t in titles]


class FakeAPI:
    def __init__(self, channel_albums, raw, monthly=1000, candidates=None):
        self.channel_albums = channel_albums  # {cid: [{'title','browseId'}]}
        self.raw = raw  # {browseId: [raw tracks]}
        self.monthly = monthly
        self.candidates = candidates or []

    def get_artist_channel_candidates(self, name):
        return self.candidates

    def get_artist_info(self, cid):
        return {"albums": self.channel_albums.get(cid, []), "monthly_listeners": self.monthly}

    def get_album_tracks_raw(self, browse_id):
        return self.raw.get(browse_id, [])

    def fetch_view_counts_batch(self, ids):
        return {v: 100 for v in ids}

    @staticmethod
    def resolve_streams(entry, view_counts):
        return view_counts.get(entry.get("video_id"))


class FakeDM:
    def __init__(self, tracks, channel_info=(None, None)):
        self._tracks = tracks
        self._channel = channel_info
        self.stream_writes = []
        self.album_writes = []
        self.monthly_writes = []
        self.set_calls = []
        self.cleared = False

    def get_artist_ytm_channel_info(self, artist_id):
        return self._channel

    def get_artist_tracks(self, artist_id):
        return self._tracks

    def set_artist_ytm_channel(self, artist_id, channel_id, source="manual"):
        self.set_calls.append((channel_id, source))
        self._channel = (channel_id, source)
        return True

    def clear_artist_ytm_channel(self, artist_id):
        self.cleared = True
        self._channel = (None, None)
        return True

    def update_artist_monthly_listeners(
        self, artist_id, ytm_listeners=None, spotify_listeners=None
    ):
        self.monthly_writes.append(ytm_listeners)
        return True

    def update_track_ytm_streams(self, track_id, total):
        self.stream_writes.append((track_id, total))
        return True

    def update_album_ytm_streams(self, artist_id, album_title, total):
        self.album_writes.append((album_title, total))
        return True


_ARTIST = SimpleNamespace(id=1, name="Isha")


def _patch(monkeypatch, api, inferred="__unset__"):
    monkeypatch.setattr(mod, "YTMusicAPI", lambda: api)
    if inferred != "__unset__":

        def _fake_infer(*a, **k):
            return inferred

        monkeypatch.setattr(mod, "_infer_channel_from_youtube_links", _fake_infer)
    else:

        def _boom(*a, **k):
            raise AssertionError("_infer ne devait pas être appelé (canal épinglé)")

        monkeypatch.setattr(mod, "_infer_channel_from_youtube_links", _boom)


def test_mauvais_canal_infere_abort_et_depingle(monkeypatch):
    # Pin INFÉRÉ persisté pointant sur un homonyme : aucune écriture + dé-épinglage.
    base = [_track(i, t) for i, t in enumerate(["A", "B", "C"])]
    api = FakeAPI(
        channel_albums={"UCbad": [{"title": "Other", "browseId": "B_Other"}]},
        raw={"B_Other": _raw(["Z1", "Z2", "Z3", "Z4"])},
        monthly=5000,
    )
    dm = FakeDM(base, channel_info=("UCbad", "inferred"))
    _patch(monkeypatch, api)  # pinned → _infer ne doit pas être appelé

    result = mod.update_ytmusic_streams(_ARTIST, dm)

    assert result["identity"]["status"] == "aborted"
    assert dm.stream_writes == []
    assert dm.album_writes == []
    assert dm.monthly_writes == []
    assert dm.cleared is True
    assert dm.set_calls == []


def test_bon_canal_via_vote_ecrit_et_epingle(monkeypatch):
    titles = ["A", "B", "C", "D", "E"]
    base = [_track(i, t) for i, t in enumerate(titles)]
    api = FakeAPI(
        channel_albums={"UCgood": [{"title": "Alb", "browseId": "B_Alb"}]},
        raw={"B_Alb": _raw(titles)},
        monthly=2000,
    )
    dm = FakeDM(base, channel_info=(None, None))  # pas de pin
    _patch(monkeypatch, api, inferred="UCgood")

    result = mod.update_ytmusic_streams(_ARTIST, dm)

    assert result["identity"]["status"] == "ok"
    assert len(dm.stream_writes) == 5
    assert dm.monthly_writes == [2000]
    assert dm.set_calls == [("UCgood", "inferred")]  # persisté APRÈS validation
    assert dm.cleared is False


def test_canal_manuel_divergent_warning_ecrit_pin_intact(monkeypatch):
    base = [_track(i, t) for i, t in enumerate(["A", "B", "C"])]
    api = FakeAPI(
        channel_albums={"UCman": [{"title": "Other", "browseId": "B_Other"}]},
        raw={"B_Other": _raw(["Z1", "Z2", "Z3", "Z4"])},
        monthly=3000,
    )
    dm = FakeDM(base, channel_info=("UCman", "manual"))
    _patch(monkeypatch, api)

    result = mod.update_ytmusic_streams(_ARTIST, dm)

    assert result["identity"]["status"] == "warning"
    assert dm.monthly_writes == [3000]  # écriture maintenue
    assert dm.set_calls == []  # jamais ré-épinglé
    assert dm.cleared is False


def test_gate_nepingle_jamais_par_dessus_un_manuel(monkeypatch):
    # Canal manuel qui VALIDE : status ok, mais aucun set (le manuel reste maître).
    titles = ["A", "B", "C", "D", "E"]
    base = [_track(i, t) for i, t in enumerate(titles)]
    api = FakeAPI(
        channel_albums={"UCman": [{"title": "Alb", "browseId": "B_Alb"}]},
        raw={"B_Alb": _raw(titles)},
        monthly=1500,
    )
    dm = FakeDM(base, channel_info=("UCman", "manual"))
    _patch(monkeypatch, api)

    result = mod.update_ytmusic_streams(_ARTIST, dm)

    assert result["identity"]["status"] == "ok"
    assert len(dm.stream_writes) == 5
    assert dm.set_calls == []
    assert dm.cleared is False


def test_base_vide_abort_propre(monkeypatch):
    api = FakeAPI(
        channel_albums={"UCgood": [{"title": "Alb", "browseId": "B_Alb"}]},
        raw={"B_Alb": _raw(["A", "B"])},
    )
    dm = FakeDM([], channel_info=(None, None))
    _patch(monkeypatch, api, inferred="UCgood")

    result = mod.update_ytmusic_streams(_ARTIST, dm)

    assert result["identity"]["status"] == "aborted"
    assert dm.stream_writes == []
    assert dm.monthly_writes == []
    assert dm.set_calls == []
