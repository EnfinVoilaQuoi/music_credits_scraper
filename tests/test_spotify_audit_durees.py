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
