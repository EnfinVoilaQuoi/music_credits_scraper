"""Liens YouTube morts : seule une RÉPONSE de YouTube prouve une absence.

Le constat est un fait daté ; le retrait, une règle à interrupteur
(`revue.VIDEOS_MORTES_BRANCHEES`), réversible, qui respecte « absent ≠ échec » et
laisse aux fiches leurs durées (2026-11-06).
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import text

from src.api import ytmusic_api, ytmusic_brut
from src.api.ytmusic_api import MetaVideos, YTMusicAPI
from src.enrichment.observation import Observation
from src.models import Artist, Track, TrackVideo
from src.services import revue, revue_auto, videos_mortes
from src.utils import corrections_fiches as cf
from src.utils.update_video_views import update_video_views

_MORTE = "AAAAAAAAAAA"
_URL = f"https://www.youtube.com/watch?v={_MORTE}"


def _fiche(dm, nom="Kanye West", titre="Lost Track", genius_id=4242, source="genius_media"):
    artiste = dm.get_artist_by_name(nom)
    if artiste is None:
        artiste = Artist(name=nom)
        artiste.id = dm.save_artist(artiste)
    track = Track(title=titre, artist=artiste)
    track.genius_id = genius_id
    dm.save_track(track)
    assert dm.update_track_youtube_url(track.id, _URL, source)
    return next(t for t in dm.get_artist_tracks(artiste.id) if t.id == track.id)


def _lien(dm, track_id):
    with dm.engine.connect() as conn:
        return (
            conn.execute(
                text("SELECT youtube_url, youtube_url_source FROM tracks WHERE id = :t"),
                {"t": track_id},
            )
            .mappings()
            .first()
        )


def _api_qui_repond(meta: dict, absentes=()):
    reponse = MetaVideos(meta)
    reponse.absentes = set(absentes)
    return SimpleNamespace(fetch_video_meta_batch=lambda _ids: reponse)


class TestReponseDeYoutube:
    def _api(self, monkeypatch, executer):
        monkeypatch.setattr(ytmusic_brut, "executer_youtube_data", executer)
        monkeypatch.setattr(ytmusic_api, "YOUTUBE_API_KEY", "x", raising=False)
        monkeypatch.setattr(
            "googleapiclient.discovery.build",
            lambda *_a, **_k: SimpleNamespace(
                videos=lambda: SimpleNamespace(list=lambda **_kw: None)
            ),
        )
        api = YTMusicAPI.__new__(YTMusicAPI)
        api._use_yt_api = True
        return api

    def test_un_lot_qui_repond_dit_ce_qu_il_n_a_pas(self, monkeypatch):
        item = {"id": "B", "statistics": {"viewCount": "3"}, "snippet": {"title": "t"}}
        api = self._api(monkeypatch, lambda _r: {"items": [item]})
        meta = api.fetch_video_meta_batch(["A", "B"])
        assert meta["B"]["views"] == 3
        assert meta.absentes == {"A"}

    def test_un_lot_tombe_ne_constate_rien(self, monkeypatch):
        def tombe(_r):
            raise OSError("panne")

        meta = self._api(monkeypatch, tombe).fetch_video_meta_batch(["A"])
        assert meta == {}
        assert meta.absentes == set()

    def test_une_reponse_sans_items_ne_constate_rien(self, monkeypatch):
        meta = self._api(monkeypatch, lambda _r: {}).fetch_video_meta_batch(["A"])
        assert meta.absentes == set()

    def test_mesurer_classe_chaque_video(self):
        api = _api_qui_repond({"V": {"views": 1}}, {"M"})
        mesure = videos_mortes.mesurer(api, ["V", "M", "S"])
        assert (mesure.vivantes, mesure.absentes, mesure.sans_reponse) == ({"V"}, {"M"}, {"S"})


class TestConstat:
    def test_le_releve_pose_le_constat(self, data_manager):
        t = _fiche(data_manager)
        rapport = update_video_views(t.artist, [t], data_manager, api=_api_qui_repond({}, {_MORTE}))
        assert rapport["no_meta"] == 1
        assert videos_mortes.constats_frais(data_manager.engine) == {t.id: {_MORTE}}

    def test_un_lot_sans_reponse_ne_pose_rien(self, data_manager):
        t = _fiche(data_manager)
        update_video_views(t.artist, [t], data_manager, api=_api_qui_repond({}))
        assert videos_mortes.constats_frais(data_manager.engine) == {}

    def test_une_video_revue_leve_le_constat(self, data_manager):
        t = _fiche(data_manager)
        update_video_views(t.artist, [t], data_manager, api=_api_qui_repond({}, {_MORTE}))
        update_video_views(
            t.artist,
            [t],
            data_manager,
            api=_api_qui_repond({_MORTE: {"views": 5, "title": "x", "channel": "y"}}),
        )
        assert videos_mortes.constats_frais(data_manager.engine) == {}

    def test_le_constat_expire(self, data_manager):
        t = _fiche(data_manager)
        from datetime import datetime, timedelta

        videos_mortes.poser_constats(
            data_manager.engine, [(t.id, _MORTE)], quand=datetime.now() - timedelta(days=400)
        )
        assert videos_mortes.constats_frais(data_manager.engine) == {}

    def test_liens_sans_ligne(self, data_manager):
        t = _fiche(data_manager)
        assert [f["id"] for f in videos_mortes.liens_sans_ligne(data_manager.engine)[_MORTE]] == [
            t.id
        ]
        data_manager.record_track_videos(t.id, [TrackVideo(video_id=_MORTE, source="genius_media")])
        assert videos_mortes.liens_sans_ligne(data_manager.engine) == {}


def _artiste(dm, track):
    artiste = dm.get_artist_by_name(track.artist.name)
    artiste.tracks = dm.get_artist_tracks(artiste.id)
    return artiste


@pytest.fixture
def constatee(data_manager):
    t = _fiche(data_manager)
    videos_mortes.poser_constats(data_manager.engine, [(t.id, _MORTE)])
    return t


class TestInterrupteur:
    def test_baisse_aucun_cas_et_rien_ne_part(self, data_manager, constatee, monkeypatch):
        monkeypatch.setattr(revue, "VIDEOS_MORTES_BRANCHEES", False)
        artiste = _artiste(data_manager, constatee)
        assert not [
            c for c in revue.analyser(data_manager, artiste).actifs if c.detecteur == "video_morte"
        ]
        revue_auto.corriger(data_manager, artiste)
        assert _lien(data_manager, constatee.id)["youtube_url"] == _URL


def test_la_regle_est_branchee():
    """Décision utilisateur du 2026-11-06."""
    assert revue.VIDEOS_MORTES_BRANCHEES is True


class TestRegle:
    def _corriger(self, dm, track):
        return revue_auto.corriger(dm, _artiste(dm, track), seulement=frozenset({"video_morte"}))

    def test_le_lien_part_par_un_refus_de_regle(self, data_manager, constatee):
        bilan = self._corriger(data_manager, constatee)
        assert len(bilan.appliquees) == 1, bilan
        assert _lien(data_manager, constatee.id)["youtube_url"] is None
        with data_manager.engine.connect() as conn:
            origine = conn.execute(
                text("SELECT origine FROM refus WHERE nature = 'video' AND valeur = :v"),
                {"v": _MORTE},
            ).scalar()
        assert origine == "regle:video_morte"

    def test_la_discographie_ne_le_repose_pas(self, data_manager, constatee):
        self._corriger(data_manager, constatee)
        assert not data_manager.update_track_youtube_url(constatee.id, _URL, "genius_media")
        assert _lien(data_manager, constatee.id)["youtube_url"] is None
        assert _MORTE in cf.videos_refusees(constatee)

    def test_retablir(self, data_manager, constatee):
        self._corriger(data_manager, constatee)
        (correction,) = data_manager.corrections_revue(constatee.artist.id)
        artiste = _artiste(data_manager, constatee)
        revue_auto.retablir(data_manager, artiste, correction)
        lien = _lien(data_manager, constatee.id)
        assert (lien["youtube_url"], lien["youtube_url_source"]) == (_URL, "genius_media")
        assert _MORTE not in cf.videos_refusees(constatee)
        # Le cas est marqué normal : la passe suivante ne le refait pas.
        assert not self._corriger(data_manager, constatee).appliquees
        assert _lien(data_manager, constatee.id)["youtube_url"] == _URL

    def test_le_lien_part_chez_la_soeur(self, data_manager, constatee):
        soeur = _fiche(data_manager, nom="Kid Cudi")
        self._corriger(data_manager, constatee)
        assert _lien(data_manager, soeur.id)["youtube_url"] is None

    def test_la_duree_garde_sa_provenance(self, data_manager, constatee):
        """Une vidéo disparue n'est pas une vidéo fausse : la durée `youtube`
        reste, observation et colonne (`un retrait emporte la colonne qu'il
        laisse sans provenance` : ici elle la garde)."""
        constatee.observations = [Observation("duration", 390, "youtube")]
        data_manager.save_track(constatee)
        self._corriger(data_manager, constatee)
        with data_manager.engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT duration FROM tracks WHERE id = :t"), {"t": constatee.id}
                ).scalar()
                == 390
            )
            assert conn.execute(
                text(
                    "SELECT value FROM observations WHERE track_id = :t "
                    "AND field = 'duration' AND source = 'youtube'"
                ),
                {"t": constatee.id},
            ).scalar()

    def test_les_colonnes_du_lien_partent_avec_lui(self, data_manager, constatee):
        with data_manager.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE tracks SET youtube_video_views = 12, youtube_video_kind = 'clip' "
                    "WHERE id = :t"
                ),
                {"t": constatee.id},
            )
        self._corriger(data_manager, constatee)
        with data_manager.engine.connect() as conn:
            assert conn.execute(
                text("SELECT youtube_video_views, youtube_video_kind FROM tracks WHERE id = :t"),
                {"t": constatee.id},
            ).first() == (None, None)

    def test_un_choix_manuel_reste_a_l_humain(self, data_manager):
        t = _fiche(data_manager, source="manual")
        videos_mortes.poser_constats(data_manager.engine, [(t.id, _MORTE)])
        assert not self._corriger(data_manager, t).appliquees
        assert _lien(data_manager, t.id)["youtube_url"] == _URL

    def test_une_video_qui_a_sa_ligne_n_est_pas_jugee(self, data_manager, constatee):
        data_manager.record_track_videos(
            constatee.id, [TrackVideo(video_id=_MORTE, source="genius_media", views=9)]
        )
        assert not self._corriger(data_manager, constatee).appliquees

    def test_sans_constat_rien(self, data_manager):
        t = _fiche(data_manager, titre="Vivante", genius_id=5555)
        assert not self._corriger(data_manager, t).appliquees
