"""`src/audio/tonalite` : tonalité par profils de Krumhansl sur chroma synthétique.

Aucun audio, aucun réseau, aucune dépendance au-delà de numpy : les chromas
sont construits à la main (triade appuyée + notes de la gamme).
"""

import ast
from pathlib import Path

import numpy as np
import pytest

from src.audio.tonalite import MODE_MAJEUR, MODE_MINEUR, estimer_tonalite, scores_tonalites

_GAMME_MAJEURE = (0, 2, 4, 5, 7, 9, 11)
_GAMME_MINEURE = (0, 2, 3, 5, 7, 8, 10)


def chroma_synthetique(tonique: int, mode: int) -> np.ndarray:
    """Chroma d'un morceau tonal idéalisé : triade forte, gamme moyenne, reste faible."""
    gamme = _GAMME_MAJEURE if mode == MODE_MAJEUR else _GAMME_MINEURE
    tierce = 4 if mode == MODE_MAJEUR else 3
    c = np.full(12, 0.05)
    for degre in gamme:
        c[(tonique + degre) % 12] = 0.4
    c[tonique % 12] = 1.0
    c[(tonique + 7) % 12] = 0.8
    c[(tonique + tierce) % 12] = 0.7
    return c


@pytest.mark.parametrize(
    ("tonique", "mode"),
    [(0, MODE_MAJEUR), (9, MODE_MINEUR), (6, MODE_MINEUR), (7, MODE_MAJEUR), (3, MODE_MAJEUR)],
)
def test_tonalite_retrouvee(tonique, mode):
    t = estimer_tonalite(chroma_synthetique(tonique, mode))
    assert (t.pitch_class, t.mode) == (tonique, mode)
    assert t.marge > 0


def test_do_majeur_et_la_mineur_se_distinguent():
    """Mêmes notes de gamme : c'est la triade appuyée qui tranche."""
    do = estimer_tonalite(chroma_synthetique(0, MODE_MAJEUR))
    la = estimer_tonalite(chroma_synthetique(9, MODE_MINEUR))
    assert (do.pitch_class, do.mode) == (0, MODE_MAJEUR)
    assert (la.pitch_class, la.mode) == (9, MODE_MINEUR)


def test_transposer_le_chroma_transpose_la_reponse():
    base = chroma_synthetique(0, MODE_MAJEUR)
    for demi_tons in range(12):
        t = estimer_tonalite(np.roll(base, demi_tons))
        assert (t.pitch_class, t.mode) == (demi_tons, MODE_MAJEUR)


def test_chroma_plat_rend_une_marge_nulle():
    t = estimer_tonalite(np.ones(12))
    assert t.score == 0.0
    assert t.marge == pytest.approx(0.0)


def test_scores_forme_et_indexation_par_mode():
    s = scores_tonalites(chroma_synthetique(0, MODE_MAJEUR))
    assert s.shape == (2, 12)
    assert s[MODE_MAJEUR, 0] == s.max()


def test_le_package_audio_n_importe_rien_du_projet():
    """`src/audio` est appelé depuis un venv SANS les deps du projet : aucun de ses
    modules ne doit importer `src.*` hors `src.audio` (crible AST, comme les tests
    structurels de `src/services`)."""
    racine = Path(__file__).resolve().parents[1] / "src" / "audio"
    fautifs = []
    for fichier in sorted(racine.glob("*.py")):
        arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        for noeud in ast.walk(arbre):
            noms = []
            if isinstance(noeud, ast.Import):
                noms = [a.name for a in noeud.names]
            elif isinstance(noeud, ast.ImportFrom) and noeud.module:
                noms = [noeud.module]
            for nom in noms:
                if nom.startswith("src.") and not nom.startswith("src.audio"):
                    fautifs.append((fichier.name, nom))
    assert fautifs == []
