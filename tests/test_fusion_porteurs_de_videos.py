"""Une fusion prévient les autres porteurs des vidéos de la fiche ABSORBÉE.

2026-11-18 : les copies de fiches d'IAM chez Akhenaton, fusionnées dans la fiche
du groupe, partageaient une vidéo avec une autre fiche d'Akhenaton. La fiche
gardée (IAM) était recalculée, mais pas l'autre fiche d'Akhenaton, qui gardait
le total YTM de l'ancien partage (invariant `colonne_verdict`).
"""

from sqlalchemy import text

from src.models import Artist, Track, TrackVideo


def _artiste(dm, nom):
    a = Artist(name=nom)
    a.id = dm.save_artist(a)
    return a


def _fiche(dm, artiste, titre, **kw):
    t = Track(title=titre, artist=artiste)
    for k, v in kw.items():
        setattr(t, k, v)
    t.id = dm.save_track(t)
    return t


def _ytm(dm, tid):
    with dm.engine.connect() as conn:
        return conn.execute(
            text("SELECT ytm_streams FROM tracks WHERE id = :t"), {"t": tid}
        ).scalar()


def test_l_autre_porteur_de_la_fiche_absorbee_est_recalcule(data_manager):
    dm = data_manager
    iam, akh = _artiste(dm, "IAM"), _artiste(dm, "Akhenaton")
    garde = _fiche(dm, iam, "Offishall", deezer_id=929264)
    copie = _fiche(dm, akh, "Offishall", deezer_id=929264)
    autre = _fiche(dm, akh, "Offishal", genius_id=777)
    video = TrackVideo(video_id="AAAAAAAAAAA", source="ytm_album", views=443081, title="Offishal")
    for t in (garde, copie, autre):
        dm.record_track_videos(t.id, [video])
    partage = _ytm(dm, autre.id)

    assert dm.merge_tracks(garde.id, copie.id)

    seule = _ytm(dm, autre.id)
    # Recalcul de référence : la fiche relue et sauvée.
    dm.save_track(next(t for t in dm.get_artist_tracks(akh.id) if t.id == autre.id))
    assert seule == _ytm(dm, autre.id)
    assert seule != partage  # la vidéo n'est plus partagée chez Akhenaton
