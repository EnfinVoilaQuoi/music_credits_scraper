"""La piste YouTube Music derrière une durée `ytmusic` (2026-11-24) : le hit
de la recherche par le titre, relu dans le brut."""

from src.api.ytmusic_brut import hit_de_la_duree


def _hit(vid, titre, duree):
    return {"videoId": vid, "title": titre, "duration_seconds": duree}


def test_le_hit_au_titre_et_a_la_duree():
    resultats = [_hit("a", "Enfants terribles", 232), _hit("b", "Enfants Terribles", 248)]
    assert hit_de_la_duree(resultats, "Enfants terribles", 248)["videoId"] == "b"


def test_un_autre_titre_a_la_meme_duree_n_est_pas_le_hit():
    assert hit_de_la_duree([_hit("a", "Autre chose", 248)], "Enfants terribles", 248) is None


def test_deux_hits_possibles_on_ne_devine_pas():
    resultats = [_hit("a", "Enfants terribles", 248), _hit("b", "Enfants terribles", 248)]
    assert hit_de_la_duree(resultats, "Enfants terribles", 248) is None
