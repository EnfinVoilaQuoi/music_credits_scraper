"""Le corpus de paires de titres RÉELLES : aucun verdict ne change en silence.

Chantier D (2026-10-12). `tests/fixtures/paires_titres.json` porte ~7 600 paires
tirées de la base (fiche ↔ fiche d'un même artiste, fiche ↔ vidéo, titre Kworb ↔
fiche confirmés à la main) avec la réponse de chaque prédicat de titre. Une
règle qui bouge change des verdicts : ce test les NOMME. Si le changement est
voulu, relancer `python scripts/corpus_paires_titres.py --ecrire` — le diff git
du fixture montre exactement quelles paires ont basculé.
"""

import json
from pathlib import Path

import pytest

from scripts.corpus_paires_titres import verdicts

FIXTURE = Path(__file__).parent / "fixtures" / "paires_titres.json"


@pytest.fixture(scope="module")
def paires():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_le_corpus_couvre_les_trois_sources(paires):
    sources = {p["source"] for p in paires}
    assert sources == {"fiches", "video", "kworb_confirme"}
    assert len(paires) > 5000


def test_aucun_verdict_ne_change_en_silence(paires):
    changes = []
    for p in paires:
        maintenant = verdicts(p["a"], p["b"], p["album_a"], p["album_b"])
        for question, attendu in p["verdicts"].items():
            if maintenant[question] != attendu:
                changes.append(
                    f"{question}: « {p['a']} » / « {p['b']} » {attendu} → {maintenant[question]}"
                )
    assert not changes, f"{len(changes)} verdict(s) changé(s) :\n" + "\n".join(changes[:40])


# Les 28 confirmations Kworb sont une VÉRITÉ humaine (même morceau), mais leur
# clé est un titre déjà NORMALISÉ (« flamenco bonus track », pas « Flamenco -
# Bonus Track ») : les séparateurs perdus, aucun descripteur n'est lisible, et
# le juge n'en reconnaît que 2 (mesuré le 2026-10-12). Elles restent dans le
# corpus, verdicts figés, en attendant le titre Kworb BRUT (chantier E).
