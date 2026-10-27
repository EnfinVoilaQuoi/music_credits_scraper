"""Durées du panneau « À trancher » : `duree_spotify` et `duree_isolee`
tranchées par les données (2026-10-27, `scripts/simuler_durees_panneau.py`).
Chaque test fixe un cas réel de la base, réduit à ses durées."""

import pytest

from src.enrichment.observation import Observation
from src.models import Artist, Track
from src.services import politiques_revue as pol
from src.services import revue, revue_auto
from src.utils import vote_durees
from src.utils.vote_durees import INDECISE, ISOLEE, editions_distinctes, voter

_A = Artist(name="Kid Cudi")


def _t(titre="X", tid=1, recco=None, **kw):
    t = Track(title=titre, artist=_A)
    t.id = tid
    t.durees_editions = ()
    t.audio.reccobeats_resolution = recco
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _cas(detecteur, track):
    return revue.Cas(detecteur, track.id, track.title, "", 0, {}, "k")


class TestEditionsALaDureePrincipale:
    """Médine « Rappeur 2 Force » : Deezer 282 s, édition notée 282 s (une autre
    page Deezer du même fichier) — gardée, elle faisait taire Deezer ET Apple."""

    DUREES = {"deezer": 282, "apple_music": 283, "spotify_web": 292}

    def test_une_edition_a_la_duree_principale_n_explique_rien(self):
        assert voter(self.DUREES, 292, editions=(282,)).nature == INDECISE
        assert editions_distinctes((282,), 282) == ()
        assert voter(self.DUREES, 292, editions=editions_distinctes((282,), 282)).nature == ISOLEE

    def test_une_edition_d_une_autre_duree_reste(self):
        """A2H « Pourquoi » : album 226 s, single 232 s noté en édition."""
        assert editions_distinctes((232,), 226) == (232,)
        assert editions_distinctes((232,), None) == (232,)

    def test_interrupteur_baisse(self, monkeypatch):
        monkeypatch.setattr(vote_durees, "EDITIONS_HORS_DUREE_PRINCIPALE", False)
        assert editions_distinctes((282,), 282) == (282,)


class TestReccoBeatsLuParIsrc:
    """Kid Cudi « Erase Me » : la page de l'ID 192 s, YouTube 193, Discogs 193 ;
    ReccoBeats 199 s lu par l'ISRC d'une autre parution."""

    DUREES = {"spotify_web": 192, "reccobeats": 199, "youtube": 193, "discogs": 193}

    def test_n_accuse_pas_l_id(self):
        t = _t("Erase Me", recco="isrc", spotify_id="SP", durations_observees=dict(self.DUREES))
        assert revue.duree_spotify_dementie(t) is None

    def test_lu_par_l_id_il_en_reste_la_voix(self):
        t = _t("Erase Me", recco="spotify_id", spotify_id="SP", durations_observees=self.DUREES)
        assert revue.duree_spotify_dementie(t) is not None

    def test_sans_page_de_l_id_il_reste_la_voix_de_spotify(self):
        durees = {"reccobeats": 199, "youtube": 193}
        assert revue.durees_du_vote(_t(recco="isrc", durations_observees=durees)) == durees

    def test_la_page_de_l_id_parle_pour_spotify(self):
        """Sadek « Fransa » : page de l'ID 262 s = Deezer (même ISRC), ReccoBeats
        225 s et YouTube 226 s. Deezer n'est plus accusé ; c'est l'audio YouTube
        qui est isolé (le LRC finit à 234 s, il ne tient pas dans 226)."""
        t = _t(
            "Fransa",
            recco="isrc",
            spotify_id="SP",
            deezer_id=1,
            durations_observees={
                "deezer": 262,
                "spotify_web": 262,
                "reccobeats": 225,
                "youtube": 226,
            },
        )
        assert revue.duree_spotify_dementie(t) is None
        assert revue.duree_deezer_dementie(t) is None
        assert "youtube 226 s" in revue.duree_isolee(t)[0]

    def test_interrupteur_baisse(self, monkeypatch):
        monkeypatch.setattr(revue, "RECCOBEATS_ISRC_HORS_VOIX", False)
        t = _t("Erase Me", recco="isrc", spotify_id="SP", durations_observees=dict(self.DUREES))
        assert revue.duree_spotify_dementie(t) is not None


class TestEditionSpotify:
    """Booba « Viens danser avec les Lunatic » : la page de l'ID 178 s, Deezer,
    Apple, YouTube et YTM 170 s."""

    def _fiche(self, colonne="deezer"):
        t = _t(
            "Viens danser avec les Lunatic",
            spotify_id="SP",
            duration=170,
            durations_observees={
                "spotify_web": 178,
                "deezer": 170,
                "apple_music": 170,
                "youtube": 170,
                "ytmusic": 170,
            },
        )
        t.duration_source = colonne
        return t

    def test_hors_colonne_normal(self):
        t = self._fiche()
        assert pol.arbitrage_valide(_cas("duree_spotify", t), t, None).action == "normal"

    def test_au_dela_de_dix_secondes_ce_n_est_plus_une_edition(self):
        t = self._fiche()
        t.durations_observees["spotify_web"] = 181
        arbitrage = pol.politique_durees(2)(_cas("duree_spotify", t), t, None)
        assert arbitrage.action == "retirer_spotify"
        assert pol.arbitrage_valide(_cas("duree_spotify", t), t, None).action == "retirer_spotify"

    def test_interrupteur_baisse(self, monkeypatch):
        monkeypatch.setattr(pol, "EDITION_SPOTIFY_NORMALE", False)
        t = self._fiche()
        assert pol.arbitrage_valide(_cas("duree_spotify", t), t, None) is None


class TestVideoHorsColonne:
    juger = staticmethod(pol.arbitrage_video_hors_colonne)

    def test_la_piste_ytm_dement_la_video(self):
        """Kanye « Only One » : vidéo 245 s, Apple et piste YTM 224 s — le vote
        range la piste YTM avec la vidéo, mais c'est un autre fichier."""
        t = _t(
            "Only One",
            duration=224,
            durations_observees={"apple_music": 224, "youtube_video": 245, "ytmusic": 224},
        )
        t.duration_source = "ytmusic"
        assert pol.arbitrage_valide(_cas("duree_isolee", t), t, None) is None
        assert self.juger(_cas("duree_isolee", t), t, None).action == "normal"

    def test_une_version_aussi(self):
        """Booba « Kayna (Remix) » : clip 171 s, catalogues 178-179 s."""
        t = _t(
            "Kayna (Remix)",
            duration=178,
            durations_observees={
                "apple_music": 179,
                "deezer": 178,
                "spotify_web": 178,
                "youtube": 179,
                "youtube_officiel": 171,
            },
        )
        t.duration_source = "deezer"
        assert self.juger(_cas("duree_isolee", t), t, None).action == "normal"

    @pytest.mark.parametrize(
        "durees, colonne",
        [
            # Un seul catalogue contre la vidéo.
            ({"apple_music": 224, "youtube_video": 245}, "apple_music"),
            # La vidéo tient la colonne.
            ({"apple_music": 224, "ytmusic": 224, "youtube_video": 245}, "youtube_video"),
            # L'audio Topic n'est pas une vidéo ordinaire (« Nuit (Interlude) »).
            ({"deezer": 133, "spotify_web": 133, "apple_music": 134, "youtube": 81}, "deezer"),
        ],
    )
    def test_reste_a_l_humain(self, durees, colonne):
        t = _t("Only One", duration=durees[colonne], durations_observees=durees)
        t.duration_source = colonne
        assert self.juger(_cas("duree_isolee", t), t, None) is None

    def test_la_colonne_doit_avoir_la_duree_des_catalogues(self):
        t = _t(
            "Only One",
            duration=150,
            durations_observees={"apple_music": 224, "youtube_video": 245, "ytmusic": 224},
        )
        t.duration_source = "songbpm"
        assert self.juger(_cas("duree_isolee", t), t, None) is None

    def test_regles_gelees(self):
        assert {("duree_isolee", "normal")} == pol.REGLES_VIDEO


@pytest.fixture
def artiste(data_manager):
    a = Artist(name="Kanye West")
    a.id = data_manager.save_artist(a)
    return a


def _fiche_durees(dm, artiste, titre, gid, durees):
    t = Track(title=titre, artist=artiste, genius_id=gid)
    t.observations += [Observation("duration", d, s) for s, d in durees.items()]
    dm.save_track(t)


def test_video_marquee_normal_par_regle_puis_retablie(data_manager, artiste, monkeypatch):
    monkeypatch.setattr(revue_auto, "REGLES_VIDEO_BRANCHEES", True)
    durees = {"apple_music": 224, "youtube_video": 245, "ytmusic": 224}
    _fiche_durees(data_manager, artiste, "Only One", 10, durees)
    artiste.tracks = data_manager.get_artist_tracks(artiste.id)

    bilan = revue_auto.corriger(data_manager, artiste)
    assert len(bilan.appliquees) == 1 and "Only One" in bilan.appliquees[0]
    assert not [
        c for c in revue.analyser(data_manager, artiste).actifs if c.detecteur == "duree_isolee"
    ]

    (correction,) = data_manager.corrections_revue(artiste.id)
    revue_auto.retablir(data_manager, artiste, correction)
    assert revue_auto.corriger(data_manager, artiste).appliquees == []
    actifs = revue.analyser(data_manager, artiste).actifs
    assert [c.morceau for c in actifs if c.detecteur == "duree_isolee"] == ["Only One"]


def test_video_interrupteur_baisse(data_manager, artiste, monkeypatch):
    monkeypatch.setattr(revue_auto, "REGLES_VIDEO_BRANCHEES", False)
    _fiche_durees(
        data_manager,
        artiste,
        "Only One",
        10,
        {"apple_music": 224, "youtube_video": 245, "ytmusic": 224},
    )
    artiste.tracks = data_manager.get_artist_tracks(artiste.id)
    assert revue_auto.corriger(data_manager, artiste).appliquees == []
