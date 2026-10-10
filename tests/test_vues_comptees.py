"""Vues YouTube Music : une vidéo liée par Genius qui ne nomme pas le morceau ne
compte pas, sauf validation (décision utilisateur 2026-10-10).

Cas réel : Médine « Skit - Limits » — Genius lie une pub G-SHOCK (2,8 M de
vues), la vraie piste audio fait 52 000 vues.
"""

from src.models import Artist, Track, TrackVideo
from src.services.revue import cle_du_cas
from src.services.vues_comptees import vue_comptee


def _fiche(titre="Skit - Limits", gid=42):
    return Track(title=titre, artist=Artist(name="Médine"), genius_id=gid)


PUB = ("TcTGQhy3sDc", "G-SHOCK : CHALLENGE THE LIMITS !")


def test_un_lien_genius_qui_ne_nomme_pas_le_morceau_ne_compte_pas():
    assert not vue_comptee(_fiche(), *PUB, "genius_media", set())


def test_un_lien_genius_qui_nomme_le_morceau_compte():
    assert vue_comptee(_fiche(), "x", "Médine - Skit - Limits", "genius_media", set())


def test_les_autres_provenances_ne_sont_pas_concernees():
    for source in ("ytm_album", "search_auto", "manual"):
        assert vue_comptee(_fiche(), *PUB, source, set())


def test_titre_inconnu_on_ne_conclut_pas():
    assert vue_comptee(_fiche(), PUB[0], None, "genius_media", set())


def test_un_verdict_normal_la_fait_compter():
    fiche = _fiche()
    cle = cle_du_cas(fiche, {"video_id": PUB[0], "titre": PUB[1], "impact": 2_842_605})
    assert vue_comptee(fiche, *PUB, "genius_media", {cle})


def test_le_recalcul_en_base_ecarte_la_pub(data_manager):
    a = Artist(name="Médine")
    a.id = data_manager.save_artist(a)
    t = Track(title="Skit - Limits", artist=a, genius_id=42)
    data_manager.save_track(t)
    data_manager.record_track_videos(
        t.id,
        [
            TrackVideo(video_id=PUB[0], title=PUB[1], source="genius_media", views=2_842_605),
            TrackVideo(
                video_id="ZmVqva48lmE", title="Skit - Limits", source="ytm_album", views=52_583
            ),
        ],
    )
    assert data_manager.recalculer_vues_ytm(t.id, {PUB[0]}) == 52_583
    assert data_manager.recalculer_vues_ytm(t.id, {PUB[0], "ZmVqva48lmE"}) is None
    with data_manager.engine.connect() as conn:
        assert (
            conn.exec_driver_sql("SELECT ytm_streams FROM tracks WHERE id = ?", (t.id,)).scalar()
            is None
        )


def _fiche_en_base(data_manager):
    a = Artist(name="Médine")
    a.id = data_manager.save_artist(a)
    t = Track(title="Skit - Limits", artist=a, genius_id=42)
    data_manager.save_track(t)
    data_manager.record_track_videos(
        t.id,
        [
            TrackVideo(video_id=PUB[0], title=PUB[1], source="genius_media", views=2_842_605),
            TrackVideo(
                video_id="ZmVqva48lmE", title="Skit - Limits", source="ytm_album", views=52_583
            ),
        ],
    )
    return t


def _ytm(data_manager, tid):
    with data_manager.engine.connect() as conn:
        return conn.exec_driver_sql("SELECT ytm_streams FROM tracks WHERE id = ?", (tid,)).scalar()


def test_sans_exclues_le_recalcul_les_relit_en_base(data_manager):
    t = _fiche_en_base(data_manager)
    assert data_manager.recalculer_vues_ytm(t.id) == 52_583


def test_valider_le_lien_genius_le_fait_compter_aussitot(data_manager):
    """✔️ dans la fiche : la vidéo passe en `manual`, le total suit sans
    attendre le prochain run YTM."""
    from src.utils.youtube_integration import set_youtube_link

    t = _fiche_en_base(data_manager)
    data_manager.recalculer_vues_ytm(t.id)
    t = next(x for x in data_manager.get_artist_tracks(t.artist.id) if x.id == t.id)
    set_youtube_link(data_manager, t, f"https://www.youtube.com/watch?v={PUB[0]}")
    assert _ytm(data_manager, t.id) == 2_842_605 + 52_583


def test_rejeter_une_video_mesuree_la_retire_du_total(data_manager):
    from src.utils.youtube_integration import reject_youtube_link

    class _Chercheur:
        def forget(self, *_):
            return True

    t = _fiche_en_base(data_manager)
    data_manager.recalculer_vues_ytm(t.id, set())
    assert _ytm(data_manager, t.id) == 2_842_605 + 52_583
    t = next(x for x in data_manager.get_artist_tracks(t.artist.id) if x.id == t.id)
    reject_youtube_link(
        data_manager,
        t,
        f"https://www.youtube.com/watch?v={PUB[0]}",
        "Médine",
        searcher=_Chercheur(),
    )
    assert _ytm(data_manager, t.id) == 52_583


def test_un_verdict_normal_recalcule_le_total_aussitot(data_manager):
    from src.services.revue import Cas
    from src.services.vues_comptees import DETECTEUR, apres_verdict

    t = _fiche_en_base(data_manager)
    data_manager.recalculer_vues_ytm(t.id)
    preuves = {"video_id": PUB[0], "titre": PUB[1], "impact": 2_842_605}
    cas = Cas(DETECTEUR, t.id, t.title, "vidéo", preuves=preuves, cle=cle_du_cas(t, preuves))
    data_manager.trancher_cas(t.artist.id, DETECTEUR, cas.cle)
    apres_verdict(data_manager, cas)
    assert _ytm(data_manager, t.id) == 2_842_605 + 52_583
    data_manager.annuler_verdict(t.artist.id, DETECTEUR, cas.cle)
    apres_verdict(data_manager, cas)
    assert _ytm(data_manager, t.id) == 52_583


def test_rejeter_la_video_partagee_rend_ses_vues_a_l_autre_fiche(data_manager):
    """« All of the Lights (Remix) » portait la vidéo de l'original : partagée,
    elle ne comptait nulle part. Rejetée du remix, elle compte pour l'original
    — tout de suite, pas au prochain run."""
    from src.utils.youtube_integration import reject_youtube_link

    class _Chercheur:
        def forget(self, *_):
            return True

    a = Artist(name="Kanye West")
    a.id = data_manager.save_artist(a)
    original = Track(title="All of the Lights", artist=a, genius_id=1)
    remix = Track(title="All of the Lights (Remix)", artist=a, genius_id=2)
    for t in (original, remix):
        data_manager.save_track(t)
    clip = TrackVideo(
        video_id="CLIPCLIP001", title="All Of The Lights", source="search_auto", views=64
    )
    data_manager.record_track_videos(original.id, [clip])
    data_manager.record_track_videos(remix.id, [clip])
    assert data_manager.recalculer_vues_ytm(original.id) is None  # partagée : ne compte pas
    remix = next(x for x in data_manager.get_artist_tracks(a.id) if x.id == remix.id)
    reject_youtube_link(
        data_manager,
        remix,
        "https://www.youtube.com/watch?v=CLIPCLIP001",
        "Kanye West",
        searcher=_Chercheur(),
    )
    assert _ytm(data_manager, original.id) == 64
