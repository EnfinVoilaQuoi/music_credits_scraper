"""Projets d'éditions (e67) : les règles du regroupement, sur les cas réels de
la simulation du 2026-11-20 (plan `projets_albums_2026-11-20.md`)."""

from src.utils.projets_disques import (
    Edition,
    cas_a_trancher,
    edition_de_reference,
    meme_projet,
    regrouper,
)
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
        # Kid Cudi, Man On The Moon (18) / (Int'l Version) (16) : 13 communes.
        a = _ed(1, "Man On The Moon: The End of Day", range(18))
        b = _ed(
            2, "Man On The Moon: The End Of Day (Int'l Version)", list(range(13)) + [100, 101, 102]
        )
        assert meme_projet(a, b) == "commune"

    def test_dans_le_doute_un_nom_contenu_reste_separe(self):
        # Décision utilisateur 2026-11-20 : « dans le doute je préfère séparer
        # qu'assembler » — au panneau, pas réunies d'office.
        cas = [
            # Swift Guad : un tome contenu dans son double album.
            (_ed(1, "Vice & Vertu Vol.3", range(24)), _ed(2, "Vertu, vol. 3", range(11))),
            # Fababy : une partie d'un tome.
            (
                _ed(1, "La symphonie des chargeurs, Vol. 2", range(28)),
                _ed(2, "La Symphonie des chargeurs 2 (Partie 2)", range(14)),
            ),
        ]
        for a, b in cas:
            assert meme_projet(a, b) is None, (a.titre, b.titre)
            assert [c.editions for c in cas_a_trancher([a, b], regrouper([a, b]))] == [(1, 2)]

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
        # Dosseh : Deezer omet le volume (17 fiches, 15 communes). Pas une
        # suite ; mais pas réunies d'office non plus (« Bolide » / « Bolide 2 »,
        # dans le doute on sépare) : le panneau tranche.
        a = _ed(1, "Bolide", range(17))
        b = _ed(2, "Bolide Vol. 2", list(range(15)) + [100, 101])
        assert meme_projet(a, b) is None
        assert regrouper([a, b]).suites == []


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
        # Le coffret n'est plus réuni à aucun des deux (nom contenu : doute).
        assert r.a_trancher == []


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


class TestCasATrancher:
    def test_titres_differents_aux_memes_fiches_vont_au_panneau(self):
        # Josman : HHHH (EP de 5) contenu dans HHHH² (11) — deux projets
        # (décision utilisateur), mais rien ne le dit sans l'humain.
        a = _ed(1, "HHHH", range(5), record_type="ep")
        b = _ed(2, "HHHH²", range(11), record_type="album")
        (cas,) = cas_a_trancher([a, b], regrouper([a, b]))
        assert cas.editions == (1, 2) and "titres différents" in cas.motif

    def test_une_edition_cumulee_reste_separee_sans_le_panneau(self):
        eds = [
            _ed(1, "Abysses", range(15)),
            _ed(2, "Ivresse des profondeurs", range(100, 107)),
            _ed(3, "Abysses & Ivresse des profondeurs", list(range(15)) + list(range(100, 107))),
        ]
        cas = cas_a_trancher(eds, regrouper(eds))
        # Un coffret est son propre projet (2026-11-25).
        assert cas == [] and _projets(*eds) == [[1], [2], [3]]

    def test_une_suite_ou_un_single_n_y_vont_pas(self):
        eds = [
            _ed(1, "Chambre 140 (Part. 2)", range(22)),
            _ed(2, "Chambre 140 (Part. 3)", range(32)),
            _ed(3, "Bloody Sunday", [0, 1], record_type="single"),
        ]
        assert cas_a_trancher(eds, regrouper(eds)) == []

    def test_la_decision_humaine_reunit_et_desarme_la_garde(self):
        eds = [
            _ed(1, "Abysses", range(15)),
            _ed(2, "Ivresse des profondeurs", range(100, 107)),
            _ed(3, "Abysses & Ivresse des profondeurs", list(range(15)) + list(range(100, 107))),
        ]
        r = regrouper(eds, frozenset({frozenset((1, 3)), frozenset((2, 3))}))
        assert r.projets == [[1, 2, 3]] and r.a_trancher == []


class TestVolumesDuDisque:
    """« Vol. N », « Volume N », « Vol.N » et « N » numérotent le même tome
    (11 doublons d'édition au panneau le 2026-11-20) ; deux numéros, jamais."""

    def test_graphies_du_meme_tome(self):
        from src.utils.version_descriptors import meme_disque

        for a, b in [
            ("Summer Stories Kushtape Volume 2", "Summer Stories Kushtape, Vol. 2"),
            ("ASADACHI TAPE Vol. 3", "ASADACHI TAPE 3"),
            ("Volume 10", "Vol. 10"),
        ]:
            assert meme_disque(a, b) and memes_parties_de_disque(a, b), (a, b)

    def test_deux_tomes_restent_deux(self):
        for a, b in [
            ("Summer Crack, Vol. 1", "Summer Crack, Vol. 2"),
            ("Untold Story", "Untold Story Vol 2"),
            ("Summer Crack, Vol. 1", "Summer Crack"),
        ]:
            assert not memes_parties_de_disque(a, b), (a, b)
        # « LP 2 » reste le nom du disque.
        assert memes_parties_de_disque("The Marshall Mathers LP 2", "The Marshall Mathers LP2")

    def test_le_doublon_d_edition_est_reuni_d_office(self):
        a = _ed(1, "Summer Stories Kushtape Volume 2", range(11))
        b = _ed(2, "Summer Stories Kushtape, Vol. 2", range(11))
        assert _projets(a, b) == [[1, 2]]


class TestGraphieDuNom:
    """Deux éditions au même nom à la GRAPHIE près sont un même projet, sans
    l'humain (décision utilisateur 2026-11-24 ; simulé sur toute la base :
    9 regroupements, tous justes, panneau 172 → 162)."""

    def test_graphies_d_un_meme_nom(self):
        from src.utils.version_descriptors import meme_disque_a_la_graphie_pres

        for a, b in [
            ("Or Noir Part II", "Or Noir Part 2 (Deluxe)"),
            ("Or Noir 3", "Or Noir Part 3"),
            ("Double Fuck", "Double F**k"),
            ("Ce n’est que le début", "Ce n'est que l'début"),
            ("Johnny Niuuum ne meurt jamais", "Johnny Niuum ne meurt jamais"),
            ("Ballon d’or", "Ballon d'##"),
            ("Immortel 2", "Immortel II (Édition collector)"),
        ]:
            assert meme_disque_a_la_graphie_pres(a, b), (a, b)

    def test_ce_qui_n_est_pas_une_graphie(self):
        from src.utils.version_descriptors import meme_disque_a_la_graphie_pres

        for a, b in [
            ("Hôtel sans *****", "Hôtel sans étoile"),  # 5 masques, 6 lettres
            ("Too Good", "To God"),  # une lettre doublée n'est pas réduite
            ("Or Noir", "Or Noir Part 2"),  # une suite
            ("Or Noir Part 2", "Or Noir Part 3"),
            ("Official Mixtape", "IAM Official Mixtape"),
            ("Capitale du crime Vol. 4", "CDC4"),
        ]:
            assert not meme_disque_a_la_graphie_pres(a, b), (a, b)

    def test_reunis_sans_le_panneau(self):
        a = _ed(1, "Double Fuck", range(16))
        b = _ed(2, "Double F**k", range(16))
        assert meme_projet(a, b) == "contenue"
        c = _ed(3, "Or Noir Part II", range(10))
        d = _ed(4, "Or Noir Part 2", range(1, 11))
        assert meme_projet(c, d) == "commune"


def test_l_edition_de_base_n_est_pas_une_edition_augmentee():
    # IAM : la Collector est datée comme l'original (1997) ; Logic : la deluxe mal datée.
    a = _ed(1, "L’école du micro d’argent (Edition Collector)", range(58), date="1997-01-01")
    b = _ed(2, "L’école du micro d’argent", range(19), date="1997-01-01")
    c = _ed(3, "Under Pressure (Deluxe Version)", range(3), date="2014-04-08")
    d = _ed(4, "Under Pressure", range(13), date="2014-09-16")
    assert edition_de_reference([a, b]) == 2 and edition_de_reference([c, d]) == 4
    # « Enna Boost » datée avant « Enna » (PLK) : l'album de base reste « Enna ».
    e = _ed(5, "Enna", range(20), date="2020-08-28")
    f = _ed(6, "Enna Boost", [*range(18), *range(100, 111)], date="2020-07-10")
    assert edition_de_reference([e, f]) == 5


class TestRegles20261125:
    """Panneau 💽 : 162 cas → 92 (simulé sur toute la base le 2026-11-25)."""

    def _cas(self, *editions):
        eds = list(editions)
        r = regrouper(eds)
        return r, [c.editions for c in cas_a_trancher(eds, r)]

    def test_la_meme_liste_sous_deux_noms_est_un_projet(self):
        a = Edition(1, "Vulgaire, Violent et Ravi D’être Là", pistes=frozenset(range(18)))
        b = Edition(2, "VVRDL", pistes=frozenset(range(1, 19)))
        assert meme_projet(a, b) == "meme_liste"

    def test_une_version_du_disque_n_est_pas_la_meme_liste(self):
        a = Edition(1, "Artefact", pistes=frozenset(range(12)))
        b = Edition(2, "Artefact (Instrumentale)", pistes=frozenset(range(12)))
        assert meme_projet(a, b) != "meme_liste"
        single = Edition(3, "VVRDL", record_type="single", pistes=frozenset(range(12)))
        assert meme_projet(a, single) is None

    def test_deux_listes_a_moitie_communes_restent_separees(self):
        a = Edition(1, "Les fées", pistes=frozenset(range(17)))
        b = Edition(2, "Les monstres", pistes=frozenset(range(4, 18)))
        assert meme_projet(a, b) is None

    def test_le_coffret_est_separe_de_ses_disques(self):
        box = Edition(1, "Rimes essentielles", pistes=frozenset(range(12)))
        v1 = Edition(2, "Première vague", pistes=frozenset(range(6)))
        v2 = Edition(3, "Deuxième vague", pistes=frozenset(range(6, 12)))
        r, cas = self._cas(box, v1, v2)
        assert r.projets == [[1], [2], [3]] and cas == []

    def test_une_edition_augmentee_n_est_pas_un_coffret(self):
        deluxe = Edition(1, "Futur 2.0 (Deluxe)", pistes=frozenset(range(25)))
        futur = Edition(2, "Futur", pistes=frozenset(range(17)))
        ajout = Edition(3, "Futur 2.0 - EP", pistes=frozenset(range(17, 25)))
        assert (1, 2) in self._cas(deluxe, futur, ajout)[1]

    def test_le_maxi_est_separe_de_son_album(self):
        album = Edition(1, "Shoot for the Stars (Deluxe)", pistes=frozenset(range(34)))
        maxi = Edition(
            2,
            "For The Night",
            "ep",
            pistes=frozenset(range(6)),
            titres_des_pistes=frozenset({"For the Night (feat. Lil Baby)"}),
        )
        assert self._cas(album, maxi)[1] == []

    def test_des_releves_partiels_du_meme_nom_sont_un_projet(self):
        a = Edition(1, "Under Pressure", pistes=frozenset(range(13)))
        b = Edition(2, "Under Pressure (Deluxe Version)", pistes=frozenset({20, 21, 22}))
        c = Edition(3, "Under Pressure (Deluxe)", pistes=frozenset([*range(12), 20, 21, 22]))
        r, cas = self._cas(a, b, c)
        assert r.projets == [[1, 2, 3]] and cas == []


class TestEditionAugmentee:
    """Règle 4 (décision utilisateur 2026-11-25) : l'album de base et ses
    éditions font un projet, le studio choisit l'édition."""

    def test_une_edition_augmentee_rejoint_l_album(self):
        base = _ed(1, "Enna", range(20), date="2020-08-28")
        boost = _ed(2, "Enna Boost", [*range(18), *range(100, 111)], date="2020-07-10")
        assert _projets(base, boost) == [[1, 2]]
        assert cas_a_trancher([base, boost], regrouper([base, boost])) == []

    def test_pas_a_plus_de_trois_ans(self):
        a = _ed(1, "Flashback", range(4), date="2009-12-29")
        b = _ed(2, "Flashback (mixtape retrospective 2006-2017)", range(12), date="2019-06-27")
        assert _projets(a, b) == [[1], [2]]

    def test_pas_un_coffret_ni_une_version_ni_une_autre_partie(self):
        d = "2024-06-28"
        box = _ed(1, "Abysses & Ivresse des profondeurs", range(22), date=d)
        a = _ed(2, "Abysses", range(15), date=d)
        i = _ed(3, "Ivresse des profondeurs", range(15, 22), date=d)
        assert _projets(box, a, i) == [[1], [2], [3]]
        instru = _ed(4, "Artefact (Instrumentale)", range(12), date=d)
        orig = _ed(5, "Artefact", range(12), date=d)
        eds = [instru, orig]
        assert cas_a_trancher(eds, regrouper(eds)) == []
        assert _projets(
            _ed(6, "Chambre 140 (Part. 2)", range(22), date=d),
            _ed(7, "Chambre 140 (Part. 3)", range(32), date=d),
        ) == [[6], [7]]

    def test_une_edition_augmentee_recolle_ses_deux_moities(self):
        base = _ed(1, "Music to Be Murdered By", range(20), date="2020-01-17")
        face_b = _ed(2, "Music to Be Murdered By: Side B", range(20, 36), date="2020-12-18")
        deluxe = _ed(
            3,
            "Music To Be Murdered By - Side B (Deluxe Edition)",
            range(36),
            date="2020-01-17",
        )
        assert _projets(base, face_b, deluxe) == [[1, 2, 3]]
