"""Service streams — ce que le worker ne faisait pas : filtrer les désactivés
(seule fenêtre à les ignorer avant 2026-09-14) et refuser de choisir pour
l'utilisateur sur les rapprochements Kworb incertains (hook headless)."""

from types import SimpleNamespace

from src.models import Artist, Track
from src.services import streams
from src.services.runtime import Hooks, Runtime


def _rt(desactives):
    return Runtime(
        data_manager=None,
        genius_api=None,
        data_enricher=None,
        deleted=None,
        disabled=SimpleNamespace(load_disabled_tracks=lambda nom: set(desactives)),
    )


def _artist():
    a = Artist(name="A")
    a.tracks = []
    for i in (1, 2, 3):
        t = Track(title=f"T{i}", artist=a)
        t.id = i
        a.tracks.append(t)
    return a


class TestTrackIdsActifs:
    def test_sans_desactive_ni_restriction_reste_none(self):
        assert streams.track_ids_actifs(_rt(set()), _artist(), None) is None

    def test_restriction_expurgee_des_desactives(self):
        assert streams.track_ids_actifs(_rt({2}), _artist(), [1, 2, 3]) == frozenset({1, 3})

    def test_desactives_sans_restriction_donne_les_actifs(self):
        assert streams.track_ids_actifs(_rt({3}), _artist(), None) == frozenset({1, 2})


class _Provider:
    def __init__(self, spotify):
        self.spotify = spotify
        self.ferme = False

    def fetch_spotify(self, artist, dm):
        return self.spotify

    def fetch_ytm(self, *a, **k):
        return {"matched": 0}

    def fetch_video_views(self, *a, **k):
        return {"updated": 0}

    def close(self):
        self.ferme = True


class TestHookKworb:
    def test_headless_liste_sans_appliquer(self, caplog):
        p = _Provider({"matched": 1, "suggestions": [{"kworb_title": "Matrix"}]})
        dm = SimpleNamespace(get_artist_tracks=lambda aid: [])
        rt = Runtime(
            data_manager=dm, genius_api=None, data_enricher=None, deleted=None, disabled=None
        )
        a = _artist()
        a.id = 1
        bilan = streams.run(rt, a, streams.OptionsStreams(spotify_web=False), Hooks(), provider=p)
        assert bilan.complete and p.ferme
        assert "NON appliqué" in caplog.text

    def test_hook_gui_recoit_suggestions_et_date(self):
        vus = []
        p = _Provider({"matched": 1, "suggestions": ["s"], "kworb_updated": "2026-09-14"})
        dm = SimpleNamespace(get_artist_tracks=lambda aid: [])
        rt = Runtime(
            data_manager=dm, genius_api=None, data_enricher=None, deleted=None, disabled=None
        )
        a = _artist()
        a.id = 1
        streams.run(
            rt,
            a,
            streams.OptionsStreams(spotify_web=False, ytm=False),
            Hooks(confirmer_kworb=lambda s, d: vus.append((s, d))),
            provider=p,
        )
        assert vus == [(["s"], "2026-09-14")]
