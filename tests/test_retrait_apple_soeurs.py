"""Retirer l'ID Apple d'une ligne ne l'efface chez une sœur que si elle porte
le MÊME ID (pendant de `clear_track_deezer_id`). « Gangsta Shit »
(2026-11-23) : single Apple chez Isha, album chez Aketo ; retirer le single
effaçait aussi l'album."""

from sqlalchemy import text

from src.models import Artist, Track

_GENIUS = 9183723


def _ligne(dm, nom):
    artiste = Artist(name=nom)
    artiste.id = dm.save_artist(artiste)
    track = Track(title="Gangsta Shit", artist=artiste)
    track.genius_id = _GENIUS
    dm.save_track(track)
    return track


def _apple(dm, track_id, valeur=None, ecrire=False):
    with dm.engine.begin() as conn:
        if ecrire:
            conn.execute(
                text("UPDATE tracks SET apple_music_id = :v WHERE id = :t"),
                {"v": valeur, "t": track_id},
            )
        return conn.execute(
            text("SELECT apple_music_id FROM tracks WHERE id = :t"), {"t": track_id}
        ).scalar()


def test_la_soeur_a_un_autre_id_apple_le_garde(data_manager):
    isha, aketo = _ligne(data_manager, "Isha"), _ligne(data_manager, "Aketo")
    _apple(data_manager, isha.id, "1886126260", ecrire=True)
    _apple(data_manager, aketo.id, "1886118920", ecrire=True)
    assert data_manager.clear_track_apple_music_id(isha.id)
    assert _apple(data_manager, isha.id) is None
    assert str(_apple(data_manager, aketo.id)) == "1886118920"


def test_la_soeur_au_meme_id_apple_le_perd(data_manager):
    isha, aketo = _ligne(data_manager, "Isha"), _ligne(data_manager, "Aketo")
    _apple(data_manager, isha.id, "1886118920", ecrire=True)
    _apple(data_manager, aketo.id, "1886118920", ecrire=True)
    assert data_manager.clear_track_apple_music_id(isha.id)
    assert _apple(data_manager, isha.id) is None
    assert _apple(data_manager, aketo.id) is None
