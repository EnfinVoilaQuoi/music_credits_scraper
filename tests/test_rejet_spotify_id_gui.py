"""Le geste de rejet vu de l'interface : la base ET l'objet en mémoire.

Le bouton lui-même n'est pas testable sans widgets, mais ce qu'il appelle l'est —
et c'est là qu'est le piège. Nettoyer la base sans remettre l'objet d'aplomb
laisserait la fiche afficher l'ID rejeté jusqu'au prochain rechargement, et le
`save_track` suivant le RÉÉCRIRAIT depuis l'objet : le rejet donnerait
l'impression de n'avoir servi à rien. C'est exactement la leçon du rejet de lien
YouTube, dont les trois gestes sont indissociables pour la même raison.
"""

from sqlalchemy import text

from src.models import Artist, Track, TrackSpotifyId
from src.utils.spotify_audit import rejeter_spotify_id

_ID_A = "3VXzVGAWFSrH47dBtTOPws"
_ID_B = "5go793BaOjzfop2JwctQ0r"


def _morceau(data_manager, spotify_id=_ID_A) -> Track:
    artist = Artist(name="Swing")
    artist.id = data_manager.save_artist(artist)
    track = Track(title="Mouton noir", artist=artist)
    track.spotify_id = spotify_id
    track.spotify_id_checked_at = "2026-09-08T11:43:14"
    data_manager.save_track(track)
    data_manager.record_track_spotify_ids(
        track.id, [TrackSpotifyId(spotify_id=spotify_id, source="scraper")]
    )
    (track,) = data_manager.get_artist_tracks(artist.id)
    return track


def test_la_base_est_nettoyee(data_manager):
    track = _morceau(data_manager)

    rapport = rejeter_spotify_id(data_manager, track, _ID_A)

    assert rapport["colonne_effacee"] is True
    with data_manager.engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT spotify_id FROM tracks WHERE id = :t"), {"t": track.id}
            ).scalar()
            is None
        )
    assert data_manager.get_track_spotify_ids(track.id) == []


def test_l_objet_en_memoire_suit(data_manager):
    """Sans ça, la fiche continue d'afficher l'ID et le prochain `save_track` le
    réécrit depuis l'objet — le rejet serait annulé par le geste suivant."""
    track = _morceau(data_manager)
    assert track.spotify_ids == [_ID_A]

    rejeter_spotify_id(data_manager, track, _ID_A)

    assert track.spotify_id is None
    assert track.spotify_ids == []
    assert track.spotify_id_entries == []
    # « jamais cherché » : le prochain enrichissement REFERA la résolution.
    assert track.spotify_id_checked_at is None


def test_le_rejet_survit_a_un_save_track(data_manager):
    """Le test qui compte : rejeter puis sauver ne doit pas ressusciter l'ID."""
    track = _morceau(data_manager)

    rejeter_spotify_id(data_manager, track, _ID_A)
    data_manager.save_track(track)

    with data_manager.engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT spotify_id FROM tracks WHERE id = :t"), {"t": track.id}
            ).scalar()
            is None
        )


def test_rejeter_une_edition_laisse_le_principal(data_manager):
    """Le rejet vise UN identifiant : retirer une édition alternative ne doit
    pas déloger celui qu'on ouvre."""
    track = _morceau(data_manager, spotify_id=_ID_A)
    data_manager.record_track_spotify_ids(
        track.id, [TrackSpotifyId(spotify_id=_ID_B, source="kworb")]
    )
    (track,) = data_manager.get_artist_tracks(track.artist.id)

    rejeter_spotify_id(data_manager, track, _ID_B)

    assert track.spotify_id == _ID_A
    assert track.spotify_ids == [_ID_A]
    assert track.spotify_id_checked_at is not None  # la résolution reste datée
