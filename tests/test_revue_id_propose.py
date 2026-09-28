"""② de l'étape Identité : l'ID Spotify lu par SongBPM est PROPOSÉ, plus écrit.

De bout en bout sur une vraie base : l'observation déclarée par le provider
est persistée (champ générique), relue par le panneau, et ses actions posent
l'ID (gate d'identité, titre tranché par l'utilisateur) ou le refusent pour de
bon (mémoire `retirer_id_spotify`).
"""

import pytest

from src.enrichment.observation import Observation
from src.models import Artist, Track
from src.services import revue, revue_actions
from src.utils.spotify_identity import CHAMP_ID_PROPOSE

PROPOSE = "4uLU6hMCjMI75M1A2tKUQC"


def _fiche(dm, *, spotify_id=None):
    artist = Artist(name="Isha")
    artist.id = dm.save_artist(artist)
    track = Track(title="Durag", artist=artist)
    track.spotify_id = spotify_id
    track.observations = [Observation(CHAMP_ID_PROPOSE, PROPOSE, "songbpm")]
    dm.save_track(track)
    artist.tracks = dm.get_artist_tracks(artist.id)
    return artist


def _cas(dm, artist):
    return [c for c in revue.analyser(dm, artist).actifs if c.detecteur == "id_spotify_propose"]


def _contexte(dm, artist):
    return revue_actions.ContexteAction(dm, artist, {t.id: t for t in artist.tracks})


def test_fiche_sans_id_proposition_puis_pose(data_manager, monkeypatch):
    artist = _fiche(data_manager)
    (cas,) = _cas(data_manager, artist)
    assert "songbpm propose l'ID Spotify" in cas.motif
    actions = {a.code: a for a in revue_actions.actions_pour(cas)}
    assert set(actions) == {"poser_id", "refuser_id"} and actions["poser_id"].reseau

    monkeypatch.setattr(
        "src.utils.spotify_identity.valider_identite", lambda t, sid, **k: k["titres_tranches"]
    )
    actions["poser_id"].executer(_contexte(data_manager, artist), cas)
    artist.tracks = data_manager.get_artist_tracks(artist.id)
    assert artist.tracks[0].spotify_id == PROPOSE
    assert _cas(data_manager, artist) == []  # l'ID porté, le cas disparaît


def test_le_gate_refuse_encore_un_id_faux(data_manager, monkeypatch):
    artist = _fiche(data_manager)
    (cas,) = _cas(data_manager, artist)
    monkeypatch.setattr("src.utils.spotify_identity.valider_identite", lambda *a, **k: False)
    poser = next(a for a in revue_actions.actions_pour(cas) if a.code == "poser_id")
    with pytest.raises(ValueError, match="contrôle d'identité"):
        poser.executer(_contexte(data_manager, artist), cas)
    assert data_manager.get_artist_tracks(artist.id)[0].spotify_id is None


def test_refuser_est_definitif(data_manager):
    artist = _fiche(data_manager)
    (cas,) = _cas(data_manager, artist)
    refuser = next(a for a in revue_actions.actions_pour(cas) if a.code == "refuser_id")
    refuser.executer(_contexte(data_manager, artist), cas)
    assert _cas(data_manager, artist) == []


def test_fiche_a_un_autre_id_le_dit_sans_proposer_de_le_poser(data_manager):
    artist = _fiche(data_manager, spotify_id="0000000000000000000000")
    (cas,) = _cas(data_manager, artist)
    assert "la fiche porte 0000000000000000000000" in cas.motif
    assert [a.code for a in revue_actions.actions_pour(cas)] == ["refuser_id"]
