"""`scripts/corriger_artiste_des_versions.py` : le nom de l'original rétabli dans
les relations de version écrites par `ecarts_deezer` avant le 2026-11-22."""

from scripts.corriger_artiste_des_versions import interprete, relations_corrigees


def test_le_nom_du_createur_devient_celui_de_l_original():
    relations = [
        {
            "type": "version_of",
            "title": "Un ange dans le ciel",
            "artist": "Suprême NTM",
            "url": None,
            "track_id": 31136,
        }
    ]
    corrigees = relations_corrigees(relations, "Suprême NTM", {31136: "Kool Shen"})
    assert corrigees == [{**relations[0], "artist": "Kool Shen"}]


def test_rien_ne_change_quand_l_original_est_a_l_artiste():
    relations = [{"type": "version_of", "artist": "Swing", "track_id": 652}]
    assert relations_corrigees(relations, "Swing", {652: "Swing"}) is None


def test_une_relation_de_genius_sans_track_id_n_est_pas_touchee():
    relations = [{"type": "remix_of", "artist": "Swing", "url": "https://genius.com/x"}]
    assert relations_corrigees(relations, "Swing", {652: "Dajak"}) is None


def test_un_autre_nom_que_celui_de_la_fiche_n_est_pas_touche():
    relations = [{"type": "version_of", "artist": "Dajak", "track_id": 652}]
    assert relations_corrigees(relations, "Swing", {652: "Nas"}) is None


def test_l_interprete_est_le_principal_sinon_le_proprietaire():
    assert interprete("Nas", "Suprême NTM") == "Nas"
    assert interprete(None, "Kool Shen") == "Kool Shen"
