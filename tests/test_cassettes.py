"""Cassettes : ce qu'un VRAI run a lu, rejoué (chantier B, 2026-10-12).

Chaque `tests/fixtures/cassettes/deezer_*.json` porte les entrées réelles du
classement des écarts Deezer d'un artiste (disques et pistes lus, fiches
réduites à leur identité — aucune parole) et le résultat de l'enregistrement.
Une règle qui change ce classement sur des données réelles est VUE ici ; un
changement voulu se réenregistre (`python scripts/cassette_deezer.py <artiste>`).
"""

import json
from pathlib import Path

import pytest

from src.services import cassettes

DOSSIER = Path(__file__).parent / "fixtures" / "cassettes"
FICHIERS = sorted(DOSSIER.glob("deezer_*.json"))


def test_il_y_a_au_moins_une_cassette():
    assert FICHIERS


@pytest.mark.parametrize("fichier", FICHIERS, ids=lambda f: f.stem)
def test_le_classement_rejoue_donne_le_meme_resultat(fichier):
    cassette = json.loads(fichier.read_text(encoding="utf-8"))
    obtenu = cassettes.rejouer(cassette)
    attendu = cassette["attendu"]
    en_moins = sorted(set(attendu["ecarts"]) - set(obtenu["ecarts"]))
    en_plus = sorted(set(obtenu["ecarts"]) - set(attendu["ecarts"]))
    assert (
        not en_moins and not en_plus
    ), f"{len(en_moins)} écart(s) disparu(s), {len(en_plus)} apparu(s) :\n" + "\n".join(
        [f"- {x}" for x in en_moins[:10]] + [f"+ {x}" for x in en_plus[:10]]
    )
    assert obtenu["editions_jumelles"] == attendu["editions_jumelles"]


def test_une_cassette_ne_porte_jamais_de_paroles():
    for fichier in FICHIERS:
        cassette = json.loads(fichier.read_text(encoding="utf-8"))
        for fiche in cassette["base"]:
            assert set(fiche) <= set(cassettes.CHAMPS_FICHE)
