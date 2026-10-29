"""L'audit des ID Spotify juge avec les durées OBSERVÉES (2026-10-14).

Le gate ne laisse corroborer un titre différent que par une durée qui ne doit
rien à l'ID (`spotify_identity.duree_temoin`) ; l'audit doit voir les mêmes
durées, sinon il jugerait plus large que le gate (SCH « Massimo » ← « La
mallette » : la colonne avait suivi l'ID).
"""

from src.enrichment.observation import Observation
from src.models import Artist, Track
from src.utils.spotify_audit import lignes_a_verifier, track_de_la_ligne
from src.utils.spotify_identity import identite_concorde


def test_l_audit_porte_les_durees_observees(data_manager):
    artiste = Artist(name="SCH")
    artiste.id = data_manager.save_artist(artiste)
    t = Track(title="Massimo", artist=artiste)
    t.spotify_id = "SP"
    t.duration = 201
    t.observations = [Observation("duration", 201, "spotify_web")]
    t.id = data_manager.save_track(t)

    (ligne,) = lignes_a_verifier(data_manager.engine)
    track = track_de_la_ligne(ligne)
    assert track.durations_observees == {"spotify_web": 201}
    ok, motif = identite_concorde(
        track, {"name": "La mallette", "artists": ["SCH"], "duration": 201}
    )
    assert not ok and "titre" in motif


def test_l_audit_peut_se_limiter_a_des_fiches(data_manager):
    artiste = Artist(name="SCH")
    artiste.id = data_manager.save_artist(artiste)
    premier = Track(title="Massimo", artist=artiste)
    premier.spotify_id = "SP1"
    premier.id = data_manager.save_track(premier)
    second = Track(title="La mallette", artist=artiste)
    second.spotify_id = "SP2"
    second.id = data_manager.save_track(second)

    lignes = lignes_a_verifier(data_manager.engine, track_ids=[second.id])

    assert [(ligne["id"], ligne["sid"]) for ligne in lignes] == [(second.id, "SP2")]


def test_l_id_d_une_edition_se_juge_sur_la_duree_de_l_edition(data_manager):
    """Booba « Garcimore » (*Autopsie Vol. 2*, 182 s) porte l'ID Spotify de la
    version de *0.9* (210 s), notée en édition (2026-10-19)."""
    from src.models.track import TrackSpotifyId

    artiste = Artist(name="Booba")
    artiste.id = data_manager.save_artist(artiste)
    t = Track(title="Garcimore", artist=artiste)
    t.spotify_id = "SPA2"
    t.duration = 182
    t.observations = [Observation("duration", 182, "deezer")]
    t.id = data_manager.save_track(t)
    data_manager.record_track_spotify_ids(
        t.id, [TrackSpotifyId(spotify_id="SP09", source="manual")]
    )
    data_manager.record_track_edition(t.id, "Version 0.9", spotify_id="SP09", duration=210)

    lignes = {ligne["sid"]: ligne for ligne in lignes_a_verifier(data_manager.engine)}
    edition = track_de_la_ligne(lignes["SP09"])
    assert edition.duration == 210 and edition.durations_observees == {}
    assert identite_concorde(edition, {"name": "Garcimore", "artists": ["Booba"], "duration": 210})[
        0
    ]
    assert track_de_la_ligne(lignes["SPA2"]).durations_observees == {"deezer": 182}


def test_l_id_principal_note_en_edition_se_juge_sur_la_duree_de_l_edition(data_manager):
    """Lunatic « Le crime paie » (294 s) : le seul ID Spotify est celui de la
    version de 4:40, notée en édition et gardée en ID principal (2026-10-29)."""
    artiste = Artist(name="Booba")
    artiste.id = data_manager.save_artist(artiste)
    t = Track(title="Le crime paie", artist=artiste)
    t.spotify_id = "SP440"
    t.duration = 294
    t.observations = [Observation("duration", 294, "deezer")]
    t.id = data_manager.save_track(t)
    data_manager.record_track_edition(t.id, "Version 4:40", spotify_id="SP440", duration=280)

    (ligne,) = lignes_a_verifier(data_manager.engine)
    track = track_de_la_ligne(ligne)
    assert ligne["principal"] and track.duration == 280 and track.durations_observees == {}
    assert identite_concorde(
        track, {"name": "Le crime paie", "artists": ["Booba"], "duration": 281}
    )[0]


def test_sans_edition_l_id_principal_reste_juge_sur_la_fiche(data_manager):
    artiste = Artist(name="Booba")
    artiste.id = data_manager.save_artist(artiste)
    t = Track(title="Le crime paie", artist=artiste)
    t.spotify_id = "SP440"
    t.duration = 294
    t.id = data_manager.save_track(t)
    data_manager.record_track_edition(t.id, "Version 4:40", spotify_id="AUTRE", duration=280)

    (ligne,) = lignes_a_verifier(data_manager.engine)
    assert track_de_la_ligne(ligne).duration == 294
