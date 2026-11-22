"""`scripts/corriger_artiste_des_versions.py` : le nom de l'original rétabli dans
les relations de version écrites par `ecarts_deezer` avant le 2026-11-22."""

from scripts.corriger_artiste_des_versions import (
    interprete,
    originaux_douteux,
    relations_corrigees,
)


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


def test_sans_page_genius_le_principal_ne_fait_pas_foi():
    """2026-11-22 : « Dimanche 31 » d'Anton Serra, sans page, porte en principal
    l'artiste du disque Deezer où il apparaît (Oster Lapwass)."""
    assert interprete("Oster Lapwass", "Anton Serra", page=False) == "Anton Serra"


def test_un_original_a_role_secondaire_est_douteux_quand_l_artiste_a_le_sien():
    """« The Return Of Chip Douglas » de Nuevo (USA), produit par Kid Cudi (#3960),
    à côté du sien (#3638) : la relation vise la mauvaise fiche, on ne la renomme
    pas. Sans fiche propre au même titre, le rôle secondaire ne rend rien douteux."""
    fiches = [
        (3960, "Nuevo (USA)", "Kid Cudi", 1, None, "Producer", 20, "The Return Of Chip Douglas"),
        (3638, None, "Kid Cudi", 2, None, None, 20, "The Return of Chip Douglas"),
        (32681, "Napoleon Da Legend", "Akhenaton", 3, None, "Producer", 7, "The End"),
    ]
    assert originaux_douteux(fiches) == {3960}
