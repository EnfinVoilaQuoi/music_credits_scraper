"""Projets d'éditions (e67) : les règles du regroupement, sur les cas réels de
la simulation du 2026-11-20 (plan `projets_albums_2026-11-20.md`)."""

from src.utils.projets_disques import Edition, edition_de_reference, meme_projet, regrouper
from src.utils.version_descriptors import memes_parties_de_disque


def _ed(i, titre, pistes, **kw):
    return Edition(id=i, titre=titre, pistes=frozenset(pistes), **kw)


def _projets(*editions):
    return regrouper(list(editions)).projets


class TestRegles:
    def test_une_edition_bonus_aux_memes_fiches_est_le_meme_projet(self):
        # Josman, M.A.N (Deezer 302453847) et M.A.N (Bonus) (308500177).
        a = _ed(1, "M.A.N (Black Roses & Lost Feelings)", range(19), record_type="album")
        b = _ed(2, "M.A.N (Black Roses & Lost Feelings) (Bonus)", range(19), record_type="album")
        assert _projets(a, b) == [[1, 2]]

    def test_une_reedition_contenant_l_original_est_le_meme_projet(self):
        a = _ed(1, "Temps mort", range(17))
        b = _ed(2, "Temps mort (Réédition)", range(20))
        assert meme_projet(a, b) == "contenue"

    def test_regle_des_80_pour_cent_au_meme_nom(self):
        # PLK, Enna (20) / Enna Boost (29) : 18 communes.
        a = _ed(1, "Enna", range(20))
        b = _ed(2, "Enna Boost", list(range(18)) + list(range(100, 111)))
        assert meme_projet(a, b) == "commune"

    def test_meme_code_barres(self):
        a = _ed(1, "A V L D", [1], upc="0859733291991")
        b = _ed(2, "AVLD", [2], upc="859733291991")
        assert _projets(a, b) == [[1, 2]]

    def test_jamais_au_seul_titre(self):
        assert _projets(_ed(1, "Ipséité", [1, 2, 3]), _ed(2, "Ipséité", [4, 5, 6])) == [[1], [2]]


class TestJamaisEnsemble:
    def test_un_single_ne_rejoint_pas_l_album(self):
        a = _ed(1, "Bloody Sunday", [1, 2], record_type="single")
        b = _ed(2, "Bloody Sunday", range(14), record_type="album")
        assert meme_projet(a, b) is None

    def test_un_live_ne_rejoint_pas_le_studio(self):
        a = _ed(1, "Paradisiaque", range(12), live=False)
        b = _ed(2, "Paradisiaque (Live)", range(12), live=True)
        assert meme_projet(a, b) is None

    def test_un_best_of_n_est_pas_une_edition(self):
        a = _ed(1, "Immortel", range(32), record_type="ep")
        b = _ed(2, "Immortel (Mini Best Of)", range(5), record_type="ep")
        assert meme_projet(a, b) is None


class TestSuites:
    """Décision utilisateur 2026-11-20 : une suite qui reprend les précédentes
    (HHHH / HHHH², Stamina / Memento, Chambre 140) est un AUTRE projet."""

    def test_chambre_140_part_2_et_part_3(self):
        a = _ed(1, "Chambre 140 (Part. 2)", range(22))
        b = _ed(2, "Chambre 140 (Part. 3)", range(32))
        r = regrouper([a, b])
        assert r.projets == [[1], [2]] and r.suites == [(1, 2)]

    def test_or_noir_part_2_deluxe_va_avec_part_2(self):
        # « Part 2 Deluxe combine Part 1 et Part 2 ; À la barrière, présent
        # sur les 2 parts, n'y est qu'une fois » (utilisateur, 2026-11-20).
        part1 = _ed(1, "Or Noir", range(18), record_type="album")
        part2 = _ed(2, "Or Noir Part 2", [0] + list(range(100, 109)), record_type="album")
        deluxe = _ed(3, "Or Noir Part 2 (Deluxe)", list(range(18)) + list(range(100, 109)))
        assert _projets(part1, part2, deluxe) == [[1], [2, 3]]

    def test_un_nombre_du_nom_n_est_pas_une_partie(self):
        assert memes_parties_de_disque("The Marshall Mathers LP 2", "The Marshall Mathers LP2")
        assert not memes_parties_de_disque("Or Noir", "Or Noir Part 2")
        assert not memes_parties_de_disque("Untold Story", "Untold Story Vol 2")
        a = _ed(1, "The Marshall Mathers LP 2 (Expanded Edition)", range(27))
        b = _ed(2, "The Marshall Mathers LP2", range(15))
        assert meme_projet(a, b) == "contenue"

    def test_memes_fiches_ne_font_jamais_une_suite(self):
        # Dosseh : Deezer omet le volume (17 fiches, 15 communes).
        a = _ed(1, "Bolide", range(17))
        b = _ed(2, "Bolide Vol. 2", list(range(15)) + [100, 101])
        assert meme_projet(a, b) == "commune"


class TestEditionCumulee:
    def test_un_coffret_ne_recolle_pas_deux_disques(self):
        # Scylla : Abysses (2012), Ivresse des profondeurs (2024), et le
        # coffret qui les cumule.
        abysses = _ed(1, "Abysses", range(15))
        ivresse = _ed(2, "Ivresse des profondeurs", range(100, 107))
        coffret = _ed(
            3, "Abysses & Ivresse des profondeurs", list(range(15)) + list(range(100, 107))
        )
        r = regrouper([abysses, ivresse, coffret])
        assert r.projets == [[1], [2], [3]]
        assert r.a_trancher == [[1, 2, 3]]


def test_edition_de_reference_la_plus_ancienne_hors_live():
    eds = [
        _ed(5, "X (Deluxe)", [1], date="2020-05-01"),
        _ed(3, "X", [1], date="2020-01-10"),
        _ed(2, "X (Live)", [1], date="2019-01-01", live=True),
        _ed(1, "X sans date", [1]),
    ]
    assert edition_de_reference(eds) == 3


def test_une_apparition_ne_rejoint_jamais_un_disque_de_l_artiste():
    a = _ed(1, "Bitume Caviar (vol.1)", range(15))
    b = _ed(2, "Bitume Caviar (vol.1)", range(15), scope="appearance")
    assert _projets(a, b) == [[1], [2]]
