"""Comparaison de deux tonalités : catégories et pondération MIREX.

La tâche « Audio Key Detection » de MIREX ne note pas seulement juste/faux :
une erreur à la quinte, la relative ou la parallèle sont des erreurs
MUSICALEMENT proches (mêmes notes ou même tonique) et valent une fraction du
point. C'est ce barème qui rend deux moteurs de tonalité comparables.

Pur Python, aucune dépendance. Conventions : pitch class 0-11, mode 1 = majeur.
"""

EXACTE = "exacte"
QUINTE = "quinte"
RELATIVE = "relative"
PARALLELE = "parallele"
FAUSSE = "fausse"

CATEGORIES = (EXACTE, QUINTE, RELATIVE, PARALLELE, FAUSSE)

# Barème MIREX (Audio Key Detection).
_SCORE = {EXACTE: 1.0, QUINTE: 0.5, RELATIVE: 0.3, PARALLELE: 0.2, FAUSSE: 0.0}


def categorie_tonalite(estime: tuple[int, int], reference: tuple[int, int]) -> str:
    """Catégorie MIREX de `estime` face à `reference`, chacune `(pitch_class, mode)`.

    · exacte    : même tonique, même mode
    · quinte    : même mode, tonique à une quinte (±7 demi-tons)
    · relative  : mode inversé et tonique de la relative (majeur = mineur + 3)
    · parallele : même tonique, mode inversé
    """
    pc_e, mode_e = int(estime[0]) % 12, int(estime[1])
    pc_r, mode_r = int(reference[0]) % 12, int(reference[1])
    ecart = (pc_e - pc_r) % 12
    if mode_e == mode_r:
        if ecart == 0:
            return EXACTE
        if ecart in (5, 7):
            return QUINTE
        return FAUSSE
    if ecart == 0:
        return PARALLELE
    # Relative : le majeur est 3 demi-tons AU-DESSUS de son mineur relatif.
    relative_attendue = 3 if mode_e == 1 else 9
    if ecart == relative_attendue:
        return RELATIVE
    return FAUSSE


def score_mirex(categorie: str) -> float:
    return _SCORE[categorie]
