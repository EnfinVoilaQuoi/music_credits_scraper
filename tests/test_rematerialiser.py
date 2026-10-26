"""`rematerialiser` : UN point de calcul des colonnes dérivées (amorce du
chantier A, audit du 2026-10-11) — à blanc par défaut, idempotent."""

import sqlite3
from contextlib import closing

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


def test_a_blanc_ne_garde_pas_le_verrou_entre_deux_fiches(data_manager, monkeypatch):
    """2026-10-26 : l'audit (`colonne_verdict`, à blanc) tenait la base sur toutes
    les fiches ; l'écrivain du run de Kanye lancé à côté a échoué (« database is
    locked »). Entre deux fiches, un autre écrivain doit passer sans attendre."""
    t = _fiche_decalee(data_manager)
    autre = Track(title="Ciel ouvert", artist=t.artist)
    data_manager.save_track(autre)
    chemin = data_manager.engine.url.database
    ecritures = []
    vraie = data_manager._rematerialiser_fiche

    def espion(conn, tid):
        if tid == autre.id:
            with closing(sqlite3.connect(chemin, timeout=0)) as ecrivain:
                ecrivain.execute("UPDATE tracks SET album = 'X' WHERE id = ?", (t.id,))
                ecrivain.commit()
                ecritures.append(tid)
        vraie(conn, tid)

    monkeypatch.setattr(data_manager, "_rematerialiser_fiche", espion)
    changements = data_manager.rematerialiser([t.id, autre.id])
    assert ecritures == [autre.id]
    assert changements[t.id]["spotify_streams"] == (None, 1000)
    assert _col(data_manager, t.id, "spotify_streams") is None


def test_appliquer_recale_toutes_les_colonnes_et_est_idempotent(data_manager):
    t = _fiche_decalee(data_manager)
    data_manager.rematerialiser([t.id], appliquer=True)
    assert _col(data_manager, t.id, "spotify_streams") == 1000
    assert _col(data_manager, t.id, "has_lyrics") == 1
    assert _col(data_manager, t.id, "ytm_streams") == 500
    assert data_manager.rematerialiser([t.id]) == {}
