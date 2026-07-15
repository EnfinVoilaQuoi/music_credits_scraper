"""Normalisation des observations audio scalaires (phase E5c-2b).

key/mode arrivent d'une source en notations HÉTÉROGÈNES : int ReccoBeats (0-11 /
0-1), lettre "G#/Ab" ou "A" (SongBPM/GetSongBPM), mot "minor"/"major". Le moteur
de réconciliation apparie key/mode PAR VALEUR : elles doivent être normalisées
(pitch class 0-11 / mode 0-1) AVANT d'entrer dans une `Observation`, sinon
reccobeats `mode=0` et songbpm `mode="minor"` ne s'apparient jamais (bug WIP
historique `mode="minor"`). S'appuie sur les normaliseurs canoniques de
`music_theory` (mêmes que ceux du calcul `musical_key`).
"""

from src.enrichment.observation import Observation
from src.utils.music_theory import note_to_pitch_class, parse_mode


def key_mode_observations(source: str, *, key=None, mode=None) -> list[Observation]:
    """Observations key/mode NORMALISÉES d'une source (omet ce qui est illisible).

    Émise indépendamment du « last-writer » legacy : une source qui a MESURÉ une
    key/mode l'observe, même si une autre source pilote la colonne legacy.
    """
    observations: list[Observation] = []
    pitch_class = note_to_pitch_class(key)
    if pitch_class is not None:
        observations.append(Observation("key", pitch_class, source))
    mode_value = parse_mode(mode)
    if mode_value is not None:
        observations.append(Observation("mode", mode_value, source))
    return observations
