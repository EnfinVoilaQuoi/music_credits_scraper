"""`rematerialiser` : UN point de calcul des colonnes dérivées (amorce du
chantier A, audit du 2026-10-11) — à blanc par défaut, idempotent."""

from sqlalchemy import text

from src.enrichment.observation import Observation
from src.models import Artist, Track, TrackVideo


def _col(dm, tid, col):
    with dm.engine.connect() as conn:
        return conn.execute(text(f"SELECT {col} FROM tracks WHERE id = :t"), {"t": tid}).scalar()


def _fiche_decalee(dm):
    a = Artist(name="Isha")
    a.id = dm.save_artist(a)
    t = Track(title="Bitume", artist=a)
    dm.save_track(t)
    # L'écrivain d'abord : converti au chantier A, il recalcule la fiche — il
    # effacerait un décrochage fabriqué avant lui.
    dm.record_track_videos(t.id, [TrackVideo(video_id="bitumeclip0", source="manual", views=500)])
    # Puis des faits écrits en CONTOURNANT les écrivains : la colonne décroche.
    with dm.engine.begin() as conn:
        dm._upsert_observations(conn, t.id, [Observation("spotify_streams", 1000, "kworb")])
        conn.execute(
            text("UPDATE tracks SET lyrics = 'couplet', has_lyrics = 0 WHERE id = :t"),
            {"t": t.id},
        )
        conn.execute(text("UPDATE tracks SET ytm_streams = 1 WHERE id = :t"), {"t": t.id})
    return t


def test_a_blanc_rien_n_est_ecrit(data_manager):
    t = _fiche_decalee(data_manager)
    changements = data_manager.rematerialiser([t.id])
    assert changements[t.id]["spotify_streams"] == (None, 1000)
    assert _col(data_manager, t.id, "spotify_streams") is None


def test_appliquer_recale_toutes_les_colonnes_et_est_idempotent(data_manager):
    t = _fiche_decalee(data_manager)
    data_manager.rematerialiser([t.id], appliquer=True)
    assert _col(data_manager, t.id, "spotify_streams") == 1000
    assert _col(data_manager, t.id, "has_lyrics") == 1
    assert _col(data_manager, t.id, "ytm_streams") == 500
    assert data_manager.rematerialiser([t.id]) == {}
