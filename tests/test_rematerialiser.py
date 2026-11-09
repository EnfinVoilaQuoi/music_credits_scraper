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


def test_une_date_a_l_annee_absurde_deja_ecrite_est_rattrapee(data_manager):
    """2026-11-09 : « 0011-04-24 » (Genius) posée avant la règle ; le recalcul
    la remplace par la meilleure autre observation, ou vide la colonne."""
    a = Artist(name="Kaishakuninja")
    a.id = data_manager.save_artist(a)
    seule = Track(title="KAVKAZ DRILL", artist=a)
    data_manager.save_track(seule)
    avec = Track(title="Libre Maintenant", artist=a)
    data_manager.save_track(avec)
    with data_manager.engine.begin() as conn:
        data_manager._upsert_observations(
            conn,
            seule.id,
            [
                Observation("release_date", "0011-04-24", "genius"),
                Observation("release_date", "0011-04-24 00:00:00", "legacy"),
            ],
        )
        data_manager._upsert_observations(
            conn,
            avec.id,
            [
                Observation("release_date", "0001-01-10", "genius"),
                Observation("release_date", "2009-03-02", "deezer"),
            ],
        )
        conn.execute(
            text("UPDATE tracks SET release_date = '0011-04-24' WHERE id = :t"), {"t": seule.id}
        )
        conn.execute(
            text("UPDATE tracks SET release_date = '0001-01-10' WHERE id = :t"), {"t": avec.id}
        )
    changements = data_manager.rematerialiser([seule.id, avec.id])
    assert changements[seule.id]["release_date"] == ("0011-04-24", None)
    assert changements[avec.id]["release_date"] == ("0001-01-10", "2009-03-02")
    data_manager.rematerialiser([seule.id, avec.id], appliquer=True)
    assert _col(data_manager, seule.id, "release_date") is None
    assert str(_col(data_manager, avec.id, "release_date"))[:10] == "2009-03-02"


def test_appliquer_recale_toutes_les_colonnes_et_est_idempotent(data_manager):
    t = _fiche_decalee(data_manager)
    data_manager.rematerialiser([t.id], appliquer=True)
    assert _col(data_manager, t.id, "spotify_streams") == 1000
    assert _col(data_manager, t.id, "has_lyrics") == 1
    assert _col(data_manager, t.id, "ytm_streams") == 500
    assert data_manager.rematerialiser([t.id]) == {}
