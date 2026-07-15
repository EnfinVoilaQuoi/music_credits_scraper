"""Tests de la couche de persistance des observations (phase E5b).

`get_observations` / `upsert_observations` de TrackRepository, exercés via la
façade DataManager sur une base temporaire (fixture `data_manager`). On vérifie
l'upsert (clé (field, source)), la lecture verbatim, le no-op sur liste vide et
la composition dans une transaction fournie (`conn=`).
"""

from src.enrichment.observation import Observation
from src.models import Artist, Track


def _artiste(dm, name="Artiste Test"):
    a = Artist(name=name)
    a.id = dm.save_artist(a)
    return a


def _track(dm, artist, title="Morceau"):
    return dm.save_track(Track(title=title, artist=artist))


def _obs(field, value, source, confidence=None):
    return Observation(field=field, value=value, source=source, confidence=confidence)


def test_upsert_puis_lecture(data_manager):
    artist = _artiste(data_manager)
    tid = _track(data_manager, artist)

    data_manager.upsert_observations(
        tid, [_obs("bpm", 142, "reccobeats", confidence=2), _obs("key", 5, "getsongbpm")]
    )
    obs = {o.field: o for o in data_manager.get_observations(tid)}

    assert obs["bpm"].value == "142"  # stocké TEXT, non coercé (mapper = E6)
    assert obs["bpm"].source == "reccobeats"
    assert obs["bpm"].confidence == 2.0
    assert obs["key"].value == "5"
    assert obs["key"].confidence is None


def test_upsert_remplace_par_meme_field_source(data_manager):
    artist = _artiste(data_manager)
    tid = _track(data_manager, artist)

    data_manager.upsert_observations(tid, [_obs("bpm", 100, "deezer", confidence=1)])
    data_manager.upsert_observations(tid, [_obs("bpm", 120, "deezer", confidence=3)])

    obs = data_manager.get_observations(tid)
    assert len(obs) == 1  # une seule ligne : (tid, bpm, deezer) écrasée
    assert obs[0].value == "120"
    assert obs[0].confidence == 3.0


def test_upsert_sources_distinctes_coexistent(data_manager):
    artist = _artiste(data_manager)
    tid = _track(data_manager, artist)

    data_manager.upsert_observations(
        tid, [_obs("bpm", 140, "deezer"), _obs("bpm", 142, "reccobeats")]
    )
    obs = data_manager.get_observations(tid)
    assert {o.source for o in obs} == {"deezer", "reccobeats"}


def test_value_none_conservee(data_manager):
    artist = _artiste(data_manager)
    tid = _track(data_manager, artist)

    data_manager.upsert_observations(tid, [_obs("mode", None, "songbpm")])
    obs = data_manager.get_observations(tid)
    assert obs[0].value is None


def test_liste_vide_est_no_op(data_manager):
    artist = _artiste(data_manager)
    tid = _track(data_manager, artist)

    data_manager.upsert_observations(tid, [])
    assert data_manager.get_observations(tid) == []


def test_get_sans_observation(data_manager):
    artist = _artiste(data_manager)
    tid = _track(data_manager, artist)
    assert data_manager.get_observations(tid) == []


def test_save_track_persiste_les_observations_du_run(data_manager):
    """save_track upsert `track.observations` dans SA transaction (E5c-1)."""
    artist = _artiste(data_manager)
    track = Track(title="Avec observations", artist=artist)
    track.observations = [
        _obs("bpm", 142, "reccobeats", confidence=2),
        _obs("key", 5, "getsongbpm"),
    ]

    tid = data_manager.save_track(track)

    obs = {o.field: o for o in data_manager.get_observations(tid)}
    assert obs["bpm"].value == "142"
    assert obs["bpm"].source == "reccobeats"
    assert obs["key"].value == "5"


def test_save_track_sans_observations_est_inoffensif(data_manager):
    """Flux actuel : `track.observations` vide → aucune écriture d'observation."""
    artist = _artiste(data_manager)
    tid = data_manager.save_track(Track(title="Sans observations", artist=artist))
    assert data_manager.get_observations(tid) == []


def test_delete_observations_cible_un_champ(data_manager):
    """delete_observations ne purge QUE le champ visé (E7d, force_sync)."""
    artist = _artiste(data_manager)
    tid = _track(data_manager, artist)
    data_manager.upsert_observations(
        tid,
        [
            _obs("lyrics_synced", "[00:01.00]a", "lrclib"),
            _obs("lyrics_synced", "[00:01.00]a", "ytmusic"),
            _obs("bpm", 120, "deezer"),
        ],
    )

    data_manager.delete_observations(tid, "lyrics_synced")

    obs = data_manager.get_observations(tid)
    assert {o.field for o in obs} == {"bpm"}  # lyrics purgées, bpm intact


def test_delete_observations_sur_champ_absent_est_no_op(data_manager):
    artist = _artiste(data_manager)
    tid = _track(data_manager, artist)
    data_manager.upsert_observations(tid, [_obs("bpm", 120, "deezer")])
    data_manager.delete_observations(tid, "lyrics_synced")  # rien à supprimer
    assert len(data_manager.get_observations(tid)) == 1


def test_lyrics_synced_roundtrip_par_source(data_manager):
    """Le LRC brut par source persiste (permet le re-vote inter-runs, E7d)."""
    artist = _artiste(data_manager)
    lrc_l = "[00:01.00]alpha\n[00:05.00]beta"
    lrc_y = "[00:01.00]alpha\n[00:05.00]beta"
    track = Track(title="Sync", artist=artist)
    track.observations = [
        _obs("lyrics_synced", lrc_l, "lrclib"),
        _obs("lyrics_synced", lrc_y, "ytmusic"),
    ]
    tid = data_manager.save_track(track)

    obs = {o.source: o for o in data_manager.get_observations(tid)}
    assert obs["lrclib"].value == lrc_l
    assert obs["ytmusic"].value == lrc_y


def test_composition_dans_une_transaction(data_manager):
    """Upsert + lecture au sein d'une même connexion fournie (gabarit triple écriture E5c)."""
    artist = _artiste(data_manager)
    tid = _track(data_manager, artist)

    with data_manager.engine.begin() as conn:
        data_manager.upsert_observations(tid, [_obs("bpm", 95, "deezer")], conn=conn)
        # Visible dans la même transaction avant commit.
        obs = data_manager.get_observations(tid, conn=conn)
        assert obs[0].value == "95"

    # Persisté après commit.
    assert data_manager.get_observations(tid)[0].value == "95"
