"""Cassettes : ce qu'un VRAI run a lu, rejoué (chantier B, 2026-10-12).

Chaque `tests/fixtures/cassettes/deezer_*.json` porte les entrées réelles du
classement des écarts Deezer d'un artiste (disques et pistes lus, fiches
réduites à leur identité — aucune parole) et le résultat de l'enregistrement.
Une règle qui change ce classement sur des données réelles est VUE ici ; un
changement voulu se réenregistre (`python scripts/cassette_deezer.py <artiste>`).
Kworb (`scripts/cassette_kworb.py`) et SongBPM (`scripts/cassette_songbpm.py`,
toutes les pages de résultats du brut) suivent le même principe.
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


KWORB = sorted(DOSSIER.glob("kworb_*.json"))


@pytest.mark.parametrize("fichier", KWORB, ids=lambda f: f.stem)
def test_le_rapprochement_kworb_rejoue_donne_le_meme_resultat(fichier):
    """Kworb (2026-10-13) : chaque ligne de la page va à la même fiche, par la
    même voie, que dans le run enregistré (`scripts/cassette_kworb.py`)."""
    cassette = json.loads(fichier.read_text(encoding="utf-8"))
    obtenu = cassettes.rejouer_kworb(cassette)
    attendu = cassette["attendu"]
    cle = lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True)  # noqa: E731
    en_moins = sorted({cle(x) for x in attendu} - {cle(x) for x in obtenu})
    en_plus = sorted({cle(x) for x in obtenu} - {cle(x) for x in attendu})
    assert not en_moins and not en_plus, "\n".join(
        [f"- {x}" for x in en_moins[:10]] + [f"+ {x}" for x in en_plus[:10]]
    )


def test_une_cassette_kworb_ne_porte_que_l_identite_des_fiches():
    for fichier in KWORB:
        cassette = json.loads(fichier.read_text(encoding="utf-8"))
        for fiche in cassette["base"]:
            assert set(fiche) <= {*cassettes.CHAMPS_KWORB, "spotify_id_entries"}


SONGBPM = DOSSIER / "songbpm.json"


def _cassette_songbpm():
    return json.loads(SONGBPM.read_text(encoding="utf-8"))


def _ecart(attendu, obtenu):
    cle = lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True)  # noqa: E731
    return sorted({cle(x) for x in attendu} - {cle(x) for x in obtenu}), sorted(
        {cle(x) for x in obtenu} - {cle(x) for x in attendu}
    )


def test_le_choix_songbpm_rejoue_donne_le_meme_resultat():
    """SongBPM (2026-10-25) : sur chaque page de résultats gardée au brut, la
    même fiche retient le même résultat, à la même place, que dans le run
    enregistré (`scripts/cassette_songbpm.py`)."""
    cassette = _cassette_songbpm()
    en_moins, en_plus = _ecart(cassette["attendu"], cassettes.rejouer_songbpm(cassette))
    assert not en_moins and not en_plus, "\n".join(
        [f"- {x}" for x in en_moins[:10]] + [f"+ {x}" for x in en_plus[:10]]
    )


def test_la_cassette_songbpm_a_des_dents(monkeypatch):
    """Un juge qui oublie la VERSION (et la partie) change des choix réels :
    « Solo Dolo, Pt. IV » prendrait « Solo Dolo (Nightmare) »."""
    from src.scrapers.songbpm_scraper_v2 import SongBPMScraper

    monkeypatch.setattr(SongBPMScraper, "meme_version", lambda self, a, b: True)
    # La garde du titre CONTENU refuserait aussi la page : seul le juge de la
    # version est éprouvé ici.
    monkeypatch.setattr(SongBPMScraper, "titre_contenu_non_corrobore", lambda *_a, **_k: None)
    cassette = _cassette_songbpm()
    _, en_plus = _ecart(cassette["attendu"], cassettes.rejouer_songbpm(cassette))
    assert any("Solo Dolo, Pt. IV" in x and "nightmare" in x.lower() for x in en_plus)


def test_une_cassette_songbpm_ne_porte_que_l_identite_des_fiches():
    for page in _cassette_songbpm()["pages"]:
        for fiche in page["fiches"]:
            assert set(fiche) == set(cassettes.CHAMPS_SONGBPM)
