"""Sans forcer, un run n'interroge que les morceaux à donnée manquante et ceux
en échec (décision utilisateur 2026-10-11 : l'enrichissement d'Isha prenait
53 min pour ~250 morceaux presque tous complets)."""

from types import SimpleNamespace

import pytest

from src.models import Artist, Track
from src.services import runtime as rt
from src.services.runtime import Manque, morceaux_a_traiter, restriction


@pytest.mark.parametrize(
    ("force", "tout", "manquants", "echecs", "attendu"),
    [
        (False, False, False, False, (True, True)),  # défaut : les deux critères
        (True, False, False, False, (False, False)),  # forcer : tout
        (False, True, False, False, (False, False)),  # --tout : tout
        (False, False, True, False, (True, False)),  # --manquants seul
        (False, False, False, True, (False, True)),  # --echecs seul
        (True, False, True, True, (False, False)),  # forcer l'emporte
    ],
)
def test_restriction(force, tout, manquants, echecs, attendu):
    assert restriction(force, tout, manquants, echecs) == attendu


def _morceau(tid, bpm=None, key=None):
    t = Track(title=f"T{tid}", artist=Artist(name="Isha"))
    t.id = tid
    t.audio.bpm, t.audio.key = bpm, key
    return t


def test_seuls_les_manquants_et_les_echecs_restent(monkeypatch):
    complet, sans_bpm, en_echec = _morceau(1, 90, 5), _morceau(2), _morceau(3, 90, 5)
    monkeypatch.setattr(rt, "morceaux_en_echec", lambda runtime, sources: {3})
    monkeypatch.setattr(rt, "absences_recentes", lambda runtime, sources: {})
    garde = morceaux_a_traiter(
        SimpleNamespace(),
        [complet, sans_bpm, en_echec],
        manquants=(Manque.AUDIO,),
        echecs=("reccobeats",),
    )
    assert [t.id for t in garde] == [2, 3]


def test_sans_critere_tout_reste():
    morceaux = [_morceau(1, 90, 5), _morceau(2)]
    assert morceaux_a_traiter(SimpleNamespace(), morceaux) == morceaux
