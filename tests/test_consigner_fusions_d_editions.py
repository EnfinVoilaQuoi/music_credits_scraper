"""`scripts/consigner_fusions_d_editions.py` : les fusions d'édition sans ligne
`fusionner_dans` (2026-11-22)."""

from scripts.consigner_fusions_d_editions import a_consigner

EPISODE = (24794, "Sofiane", "93 Empire", 2817838, "Épisode 6 / 93 Empire", 2973005)


def test_une_fusion_non_consignee_est_proposee():
    (cas,) = a_consigner([EPISODE], set())
    assert (cas["artiste"], cas["absorbee"], cas["gardee"]) == (
        "Sofiane",
        {"genius_id": 2973005},
        {"genius_id": 2817838},
    )


def test_une_fusion_consignee_pour_l_artiste_ne_l_est_plus():
    assert a_consigner([EPISODE], {("Sofiane", "g2973005")}) == []


def test_consignee_chez_un_autre_artiste_reste_a_consigner():
    """La consignation est PAR artiste (`genius_ids_absorbes(artiste)`) : celle
    de Travis Scott ne vaut pas pour la ligne de JACKBOYS."""
    assert len(a_consigner([EPISODE], {("Kalash Criminel", "g2973005")})) == 1


def test_une_entree_par_artiste_et_page_absorbee():
    assert len(a_consigner([EPISODE, EPISODE], set())) == 1


def test_une_edition_de_la_meme_page_n_est_pas_une_fusion():
    meme = (1, "Sofiane", "93 Empire", 2817838, "93 Empire", 2817838)
    assert a_consigner([meme], set()) == []
