"""Retraits au seuil de DEUX catalogues d'accord (décision utilisateur
2026-11-13, simulée sur la copie du 13/11 : 18 retraits confirmés par un
témoin, la garde écarte « In The City » et « She Wanna Have My Baby »)."""

from __future__ import annotations

from src.enrichment.observation import Observation
from src.models import Artist, Track
from src.services import politiques_revue as pol
from src.services import revue

_A = Artist(name="Mac Miller")


def _t(titre, tid=1, **kw):
    t = Track(title=titre, artist=_A)
    t.id = tid
    t.durees_editions = ()
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _juger(detecteur, track, obs=()):
    cas = revue.Cas(detecteur, track.id, track.title, "", 0, {}, "k")
    return pol.arbitrage_valide(cas, track, revue.contexte([track], {track.id: list(obs)}))


def _face(**kw):
    durees = {"spotify_web": 166, "deezer": 209, "apple_music": 209}
    return _t("Face in the Crowd", duration=209, durations_observees=durees, **kw)


def test_id_spotify_dementi_par_deux_catalogues_retire():
    arb = _juger("duree_spotify", _face())
    assert arb is not None and arb.action == "retirer_spotify"


def test_un_lrc_a_la_duree_dementie_garde_l_id():
    obs = [Observation("lrc_duree_audio", "166", "lrclib")]
    assert _juger("duree_spotify", _face(), obs) is None


def test_discogs_a_la_duree_dementie_garde_l_id():
    durees = {"spotify_web": 240, "deezer": 261, "apple_music": 261, "discogs": 240}
    t = _t("She Wanna Have My Baby", duration=261, durations_observees=durees)
    assert _juger("duree_spotify", t) is None


def test_une_fiche_a_editions_reste_a_l_humain():
    t = _face()
    t.durees_editions = (209,)
    assert _juger("duree_spotify", t) is None


def test_lien_deezer_dementi_par_deux_catalogues_delie():
    durees = {"deezer": 274, "spotify_web": 197, "apple_music": 197}
    arb = _juger("duree_deezer", _t("Quidam", duration=274, durations_observees=durees))
    assert arb is not None and arb.action == "delier_deezer"


def test_une_version_reste_a_l_humain():
    durees = {"spotify_web": 166, "deezer": 209, "apple_music": 209}
    t = _t("Face in the Crowd (Remix)", duration=209, durations_observees=durees)
    assert _juger("duree_spotify", t) is None
