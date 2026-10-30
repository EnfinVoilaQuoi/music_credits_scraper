"""Règles du panneau « À trancher » du rapport panneau-familles (2026-10-31).

Chaque test reprend un cas RÉEL de la simulation sur la copie de la base
(valeurs relevées le 2026-10-30), et sa garde quand la simulation en a montré
une. Aucune base, aucun réseau : des fiches et des contextes en mémoire."""

from datetime import datetime

from src.enrichment.observation import Observation
from src.models import Artist, Track
from src.services import politiques_revue as pol
from src.services import revue

_A = Artist(name="Kanye West")
_LU = datetime(2026, 10, 30, 12, 0)


def _t(titre="X", tid=1, **kw):
    t = Track(title=titre, artist=_A)
    t.id = tid
    t.durees_editions = ()
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _cas(detecteur, track=None, preuves=None, morceau="M"):
    return revue.Cas(detecteur, track.id if track else None, morceau, "", 0, preuves or {}, "k")


def _ctx(*tracks, obs=None, relations=None):
    return revue.contexte(tracks, obs or {}, relations or [])


def _lrc(track, source="LRCLIB"):
    track.lyrics.synced = "[00:10.00] une ligne\n[02:30.00] la fin"
    track.lyrics.synced_source = source
    return track


def _audio(source, secondes):
    """Le LRC d'une source et l'audio sur lequel elle l'a calé, lus ensemble."""
    return [
        Observation("lyrics_synced", "[00:10.00] une ligne", source, seen_at=_LU),
        Observation("lrc_duree_audio", str(secondes), source, seen_at=_LU),
    ]


class TestLrcCaleSurLesCatalogues:
    """LD1 : un LRC LRCLIB douteux (40-60 % des mots de la page) dont l'audio
    est celui que deux catalogues lus par ID donnent est le LRC de la fiche."""

    def test_haaland_garde_son_lrc(self):
        t = _lrc(
            _t(
                "Haaland",
                durations_observees={"deezer": 162, "spotify_web": 162, "youtube": 163},
            )
        )
        ctx = _ctx(t, obs={t.id: _audio("lrclib", 163)})
        arb = pol.arbitrage_etendu(_cas("lrc_douteux", t), t, ctx)
        assert arb.action == "normal"
        assert "163" in arb.raison

    def test_un_seul_catalogue_ne_suffit_pas(self):
        """« Tree » : SongBPM seul (lu au titre) à la durée de l'audio."""
        t = _lrc(_t("Tree", durations_observees={"songbpm": 272, "youtube_video": 209}))
        ctx = _ctx(t, obs={t.id: _audio("lrclib", 272)})
        assert pol.arbitrage_etendu(_cas("lrc_douteux", t), t, ctx) is None

    def test_audio_d_une_autre_duree_reste_a_l_humain(self):
        """« On My Vibe » : audio LRCLIB 234 s, catalogues 251-252 s (LD2, pas branchée)."""
        t = _lrc(
            _t(
                "On My Vibe",
                durations_observees={"deezer": 252, "spotify_web": 251, "youtube": 252},
            )
        )
        ctx = _ctx(t, obs={t.id: _audio("lrclib", 234)})
        assert pol.arbitrage_etendu(_cas("lrc_douteux", t), t, ctx) is None

    def test_le_lrc_youtube_music_n_a_pas_d_audio_qui_compte(self):
        t = _lrc(
            _t("Haaland", durations_observees={"deezer": 162, "spotify_web": 162}),
            source="YouTube Music",
        )
        ctx = _ctx(t, obs={t.id: _audio("ytmusic", 162)})
        assert pol.arbitrage_etendu(_cas("lrc_douteux", t), t, ctx) is None

    def test_spotify_et_reccobeats_ne_font_qu_une_voix(self):
        t = _lrc(_t("X", durations_observees={"spotify_web": 200, "reccobeats": 200}))
        ctx = _ctx(t, obs={t.id: _audio("lrclib", 200)})
        assert pol.arbitrage_etendu(_cas("lrc_douteux", t), t, ctx) is None


def _candidate(piste_id, isrc, duree, titre):
    return {
        "ecart": {
            "nature": "link_candidate",
            "piste": {"id": piste_id, "title": titre, "isrc": isrc, "duration": duree},
            "album": {"id": 1, "title": "A"},
            "matched_by": "title_duration_conflict",
        }
    }


class TestAutrePageDeezer:
    """DL1 : une deuxième page Deezer au même titre, autre ISRC, à plus de
    10 s de la page de la fiche que Spotify confirme, est une autre version."""

    def _galaktika(self):
        return _t(
            "Galaktika",
            deezer_id=249496,
            isrc="FR6V89801681",
            durations_observees={"deezer": 214, "spotify_web": 214, "reccobeats": 214},
        )

    def test_galaktika_ne_se_rattache_pas(self):
        t = self._galaktika()
        cas = _cas(
            "deezer_link_candidate",
            t,
            _candidate(1548546102, "FRZ029800460", 188, "Galaktika"),
        )
        arb = pol.arbitrage_deezer(cas, t, _ctx(t), {}, set())
        assert arb.action == "normal"
        assert "FRZ029800460" in arb.raison

    def test_un_petit_ecart_reste_a_l_humain(self):
        """« Clic clic » : 175 s contre 172 s, peut-être une édition."""
        t = _t(
            "Clic Clic",
            deezer_id=209376,
            isrc="FR6V80708984",
            durations_observees={"deezer": 172, "spotify_web": 172, "apple_music": 172},
        )
        cas = _cas(
            "deezer_link_candidate", t, _candidate(15003630, "FR43Q0700005", 175, "Clic clic")
        )
        assert pol.arbitrage_deezer(cas, t, _ctx(t), {}, set()) is None

    def test_sans_catalogue_qui_confirme_la_page_de_la_fiche(self):
        t = self._galaktika()
        t.durations_observees = {"deezer": 214, "reccobeats": 214}
        cas = _cas(
            "deezer_link_candidate",
            t,
            _candidate(1548546102, "FRZ029800460", 188, "Galaktika"),
        )
        assert pol.arbitrage_deezer(cas, t, _ctx(t), {}, set()) is None
