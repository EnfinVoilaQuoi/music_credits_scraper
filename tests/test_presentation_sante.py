"""La fenêtre Santé des sources (`src/presentation/sante.py`, `src/gui_qt/sante.py`) :
sonde et usage réel côte à côte, jamais mêlés ; aucune lecture de `data/` réel."""

from types import SimpleNamespace

import pytest

from src.observability.rollup import SourceUsageView
from src.presentation import sante


def spec(key, label, *families, note=""):
    return SimpleNamespace(key=key, label=label, families=families, usage_note=note)


SOURCES = [
    spec("genius", "Genius", "credits"),
    spec("ytm", "YouTube Music", "credits", "streams"),
    spec("kworb", "Kworb", "streams", note="503 = notre cadence"),
]


def test_familles_et_lignes():
    etat = {"genius": {"status": "broken", "latency_ms": 120, "last_checked": "2026-11-22T10:00"}}
    vues = {
        "ytm": SourceUsageView(
            key="ytm", calls=200, comm_failures=10, dominant_issue="throttled", absent=5
        )
    }
    fams = {f.cle: f for f in sante.familles(SOURCES, etat, vues)}
    credits = fams["credits"]
    assert [ligne.source for ligne in credits.lignes] == ["Genius", "YouTube Music"]
    g, y = credits.lignes
    assert g.statut == "broken" and g.sonde == "Cassé · 120 ms"
    assert y.appels == "200 (dont 5 sans donnée)" and y.echecs == "10 (5.0%)" and y.en_echec
    assert y.nature == "cadence (429)"
    # Une source qui sert deux familles apparaît dans les deux.
    assert [ligne.source for ligne in fams["streams"].lignes] == ["YouTube Music", "Kworb"]
    assert fams["streams"].lignes[1].derniere_erreur == "503 = notre cadence"
    assert sante.sources_cassees(etat) == 1


@pytest.fixture
def repo():
    return SimpleNamespace(
        daily=lambda **k: [],
        recent_failures=lambda *a, **k: [],
        artists_with_usage=lambda: [(1, "Isha")],
    )


def test_fenetre_sante(qtbot, repo):
    from src.gui_qt.sante import FenetreSante

    sondes = []

    def sonder(niveau):
        sondes.append(niveau)
        return {"kworb": {"status": "broken"}}

    d = FenetreSante(repo, SOURCES, lambda: {}, sonder)
    qtbot.addWidget(d)
    qtbot.waitUntil(lambda: d.artiste.count() == 2, timeout=10_000)
    assert d.arbre.topLevelItemCount() >= 2
    with qtbot.waitSignal(d.sondee, timeout=10_000) as signal:
        d.b_rapide.click()
    assert sondes == ["fast"] and signal.args == [1]
