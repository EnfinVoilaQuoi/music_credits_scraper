"""Chantier B, lot 2 — les INVARIANTS de l'audit tiennent après chaque écrivain.

Une base synthétique (lignes sœurs, observations, vidéos), puis les gestes
réels du repository, un par un ; après CHAQUE geste, les invariants à cible
zéro (`audit.INVARIANTS`) sont vérifiés. C'est le filet du chantier A : la
famille de bugs n°1 du projet est un écrivain qui déplace, recopie ou retire
des faits sans recalculer ce qui en dépend — l'audit le voit, ce test le voit
en CI, sur des données qu'on peut publier.

(Le cycle REJOUÉ sur un vrai trafic attend le brut conservé du chantier E,
qui en fournira la cassette ; une base figée de données réelles ne peut pas
vivre dans ce dépôt public — paroles sous droits.)
"""

import pytest

from src.enrichment.observation import Observation
from src.models import Artist, Track, TrackSpotifyId, TrackVideo
from src.services import audit

_GENIUS = 9001


def _ytm(dm, track_id):
    from sqlalchemy import text

    with dm.engine.connect() as conn:
        return conn.execute(
            text("SELECT ytm_streams FROM tracks WHERE id = :t"), {"t": track_id}
        ).scalar()


def _invariants(dm) -> dict[str, list[str]]:
    resultat = audit.auditer(dm)
    return {
        f: [c.libelle for c in resultat.cas if c.famille == f]
        for f in audit.INVARIANTS
        if any(c.famille == f for c in resultat.cas)
    }


@pytest.fixture
def base(data_manager):
    """Swing et Isha partagent « Grünt #33 » (lignes sœurs) ; Isha a aussi
    « Durag » (Deezer, vidéo) et un doublon « Durag (Live) » à fusionner."""
    swing = Artist(name="Swing")
    swing.id = data_manager.save_artist(swing)
    isha = Artist(name="Isha")
    isha.id = data_manager.save_artist(isha)

    chez_swing = Track(title="Grünt #33", artist=swing)
    chez_swing.genius_id = _GENIUS
    data_manager.save_track(chez_swing)
    chez_isha = Track(title="Grünt #33", artist=isha)
    chez_isha.genius_id = _GENIUS
    chez_isha.is_featuring = True
    chez_isha.primary_artist_name = "Swing"
    data_manager.save_track(chez_isha)

    durag = Track(title="Durag", artist=isha)
    durag.genius_id = 4242
    durag.deezer_id = 70
    data_manager.save_track(durag)
    data_manager.record_discography_observations(
        durag.id,
        [Observation("duration", 201, "deezer"), Observation("isrc", "FRX111", "deezer")],
    )
    doublon = Track(title="Durag (Live)", artist=isha)
    data_manager.save_track(doublon)
    return {"swing": chez_swing, "isha": chez_isha, "durag": durag, "doublon": doublon}


def test_la_base_de_depart_est_saine(data_manager, base):
    assert _invariants(data_manager) == {}


class TestApresChaqueEcrivain:
    def test_observations_et_sauvegarde_des_soeurs(self, data_manager, base):
        data_manager.record_discography_observations(
            base["isha"].id, [Observation("duration", 180, "deezer")]
        )
        data_manager.save_track(base["swing"])  # relance l'union des sœurs
        assert _invariants(data_manager) == {}

    def test_streams_spotify_arbitres(self, data_manager, base):
        data_manager.record_spotify_streams(base["durag"].id, 1_000_000, "kworb")
        data_manager.record_spotify_streams(base["durag"].id, 1_200_000, "spotify_web")
        assert _invariants(data_manager) == {}

    def test_videos_puis_total(self, data_manager, base):
        tid = base["durag"].id
        data_manager.record_track_videos(
            tid,
            [
                TrackVideo(video_id="AAAAAAAAAAA", source="genius_media", views=500, title="Durag"),
                TrackVideo(video_id="BBBBBBBBBBB", source="ytm_album", views=300, title="Durag"),
            ],
        )
        data_manager.recalculer_vues_ytm(tid)
        assert _invariants(data_manager) == {}

    def test_videos_seules_le_total_suit(self, data_manager, base):
        """Chantier A, lot 1 : `record_track_videos` recalcule en fin de
        transaction — plus besoin d'appeler `recalculer_vues_ytm` derrière
        (l'oubli laissait `ytm_streams` sur le relevé précédent)."""
        tid = base["durag"].id
        data_manager.record_track_videos(
            tid, [TrackVideo(video_id="AAAAAAAAAAA", source="ytm_album", views=500, title="Durag")]
        )
        assert _invariants(data_manager) == {}
        data_manager.record_track_videos(
            tid, [TrackVideo(video_id="AAAAAAAAAAA", source="ytm_album", views=900, title="Durag")]
        )
        assert _invariants(data_manager) == {}

    def test_video_qui_devient_partagee_puis_cesse(self, data_manager, base):
        """Une vidéo portée par deux fiches d'un artiste n'est comptée par
        AUCUNE : l'écrire sur la seconde change le total de la PREMIÈRE ;
        l'oublier le lui rend."""
        durag, doublon = base["durag"].id, base["doublon"].id
        video = TrackVideo(video_id="AAAAAAAAAAA", source="ytm_album", views=500, title="Durag")
        data_manager.record_track_videos(durag, [video])
        assert _ytm(data_manager, durag) == 500

        data_manager.record_track_videos(doublon, [video])
        assert _invariants(data_manager) == {}
        assert not _ytm(data_manager, durag)

        data_manager.forget_track_video(doublon, "AAAAAAAAAAA")
        assert _invariants(data_manager) == {}
        assert _ytm(data_manager, durag) == 500

    def test_video_recue_d_une_soeur_devient_partagee_hors_famille(self, data_manager, base):
        """L'union des sœurs donne à « Grünt #33 » chez Isha la vidéo que porte
        déjà « Durag » chez Isha : elle devient partagée, et le total de
        « Durag » — hors de la famille, jamais touché — doit suivre (2026-10-12 :
        « Father Stretch My Hands, Pt. 1 », « Moon »)."""
        video = TrackVideo(video_id="AAAAAAAAAAA", source="ytm_album", views=500, title="Durag")
        data_manager.record_track_videos(base["durag"].id, [video])
        data_manager.record_track_videos(base["swing"].id, [video])
        assert _ytm(data_manager, base["durag"].id) == 500

        data_manager.save_track(base["swing"])  # union : Isha « Grünt #33 » reçoit la vidéo
        assert _invariants(data_manager) == {}
        assert not _ytm(data_manager, base["durag"].id)

    def test_ids_spotify_puis_retrait(self, data_manager, base):
        tid = base["isha"].id
        base["isha"].spotify_id = "3VXzVGAWFSrH47dBtTOPws"
        data_manager.save_track(base["isha"])
        data_manager.record_track_spotify_ids(
            tid, [TrackSpotifyId(spotify_id="3VXzVGAWFSrH47dBtTOPws", source="kworb")]
        )
        data_manager.record_spotify_streams(tid, 5000, "kworb")
        data_manager.save_track(base["swing"])
        assert _invariants(data_manager) == {}
        data_manager.clear_track_spotify_id(base["swing"].id)
        assert _invariants(data_manager) == {}

    def test_retrait_deezer_et_isrc(self, data_manager, base):
        data_manager.clear_track_deezer_id(base["durag"].id)
        assert _invariants(data_manager) == {}
        data_manager.record_discography_observations(
            base["durag"].id, [Observation("isrc", "FRX222", "deezer")]
        )
        data_manager.clear_track_isrc(base["durag"].id)
        assert _invariants(data_manager) == {}

    def test_fusion(self, data_manager, base):
        data_manager.record_discography_observations(
            base["doublon"].id, [Observation("duration", 240, "youtube")]
        )
        data_manager.merge_tracks(base["durag"].id, base["doublon"].id)
        assert _invariants(data_manager) == {}

    def test_suppression(self, data_manager, base):
        data_manager.record_track_videos(
            base["doublon"].id, [TrackVideo(video_id="CCCCCCCCCCC", views=10)]
        )
        data_manager.delete_track(base["doublon"].id)
        assert _invariants(data_manager) == {}

    def test_rematerialiser_ne_change_rien_sur_une_base_saine(self, data_manager, base):
        assert data_manager.rematerialiser() == {}


def test_le_filet_voit_une_colonne_ecrite_sans_ses_faits(data_manager, base):
    """Le filet a des dents : un écrivain qui pose une colonne dérivée sans
    passer par ses observations (ici, simulé en SQL) est vu."""
    from sqlalchemy import text

    with data_manager.engine.begin() as conn:
        conn.execute(
            text("UPDATE tracks SET duration = 999 WHERE id = :t"), {"t": base["durag"].id}
        )
    assert "colonne_verdict" in _invariants(data_manager)
