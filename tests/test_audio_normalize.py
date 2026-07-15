"""Tests de la normalisation des observations audio (src/enrichment/audio_normalize).

key/mode d'une source (int ReccoBeats, lettre SongBPM/GetSongBPM, mot "minor") →
observations normalisées (pitch class 0-11 / mode 0-1). C'est ce qui permet à
reccobeats `mode=0` et songbpm `mode="minor"` de s'apparier (fix du bug WIP).
"""

from src.enrichment.audio_normalize import key_mode_observations


def _by_field(observations):
    return {o.field: o for o in observations}


def test_reccobeats_numerique_passthrough():
    obs = _by_field(key_mode_observations("reccobeats", key=8, mode=1))
    assert obs["key"].value == 8
    assert obs["mode"].value == 1
    assert obs["key"].source == "reccobeats"


def test_songbpm_lettre_et_mot_normalises():
    # "G#/Ab" → pitch class 8 ; "minor" → 0. C'est le cœur du fix mode="minor".
    obs = _by_field(key_mode_observations("songbpm", key="G#/Ab", mode="minor"))
    assert obs["key"].value == 8
    assert obs["mode"].value == 0


def test_major_vaut_1():
    obs = _by_field(key_mode_observations("getsongbpm", key="A", mode="major"))
    assert obs["key"].value == 9
    assert obs["mode"].value == 1


def test_source_meme_valeur_normalisee_pour_appariement():
    # reccobeats (numérique) et songbpm (mot) doivent produire la MÊME valeur mode.
    recco = _by_field(key_mode_observations("reccobeats", mode=0))
    songbpm = _by_field(key_mode_observations("songbpm", mode="minor"))
    assert recco["mode"].value == songbpm["mode"].value == 0


def test_valeurs_illisibles_omises():
    obs = key_mode_observations("songbpm", key="???", mode="bogus")
    assert obs == []


def test_champs_absents_omis():
    obs = _by_field(key_mode_observations("reccobeats", key=5))  # mode absent
    assert "key" in obs
    assert "mode" not in obs
