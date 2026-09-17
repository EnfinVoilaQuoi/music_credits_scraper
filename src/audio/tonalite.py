"""Tonalité (tonique + mode) d'un chroma 12-D par profils de Krumhansl–Kessler.

Méthode classique (Krumhansl 1990) : le chroma moyen du morceau est corrélé
(Pearson) avec les 24 profils — 12 rotations du profil majeur, 12 du mineur —
et la meilleure corrélation désigne la tonalité. La `marge` (meilleur − second)
sert de proxy de confiance : un chroma plat ou ambigu la rend proche de zéro.

Conventions IDENTIQUES à `src/utils/music_theory.py` : pitch class 0 = C … 11 = B,
mode 1 = majeur / 0 = mineur — pour qu'une observation puisse partir telle quelle
dans `audio_normalize.key_mode_observations`.

Module PUR : numpy seulement, aucune I/O. Le chroma vient de l'appelant
(librosa `chroma_cqt` moyenné sur le temps, ou tout autre extracteur).
"""

from dataclasses import dataclass

import numpy as np

# Profils de Krumhansl–Kessler (1982), indexés par degré chromatique depuis la tonique.
PROFIL_MAJEUR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
PROFIL_MINEUR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

MODE_MINEUR = 0
MODE_MAJEUR = 1


@dataclass(frozen=True)
class Tonalite:
    pitch_class: int  # 0 = C … 11 = B
    mode: int  # 1 = majeur, 0 = mineur
    score: float  # corrélation de Pearson du profil retenu
    marge: float  # score − second meilleur score (proxy de confiance)


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    if denom == 0.0:
        return 0.0
    return float((a * b).sum() / denom)


def scores_tonalites(chroma: np.ndarray) -> np.ndarray:
    """Les 24 corrélations, forme (2, 12) : ligne 0 = mineur, ligne 1 = majeur
    (l'indice de ligne EST la valeur de `mode`), colonne = tonique."""
    c = np.asarray(chroma, dtype=float).reshape(12)
    scores = np.zeros((2, 12))
    for tonique in range(12):
        # Rotation du profil pour que son degré 0 tombe sur `tonique`.
        scores[MODE_MINEUR, tonique] = _pearson(c, np.roll(PROFIL_MINEUR, tonique))
        scores[MODE_MAJEUR, tonique] = _pearson(c, np.roll(PROFIL_MAJEUR, tonique))
    return scores


def estimer_tonalite(chroma: np.ndarray) -> Tonalite:
    """Meilleure tonalité d'un chroma 12-D (moyenne temporelle d'un chromagramme)."""
    scores = scores_tonalites(chroma)
    plat = np.sort(scores.ravel())[::-1]
    mode, tonique = np.unravel_index(int(np.argmax(scores)), scores.shape)
    return Tonalite(
        pitch_class=int(tonique),
        mode=int(mode),
        score=float(plat[0]),
        marge=float(plat[0] - plat[1]),
    )
