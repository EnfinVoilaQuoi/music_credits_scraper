"""Répartition des cas de l'audit (2026-11-10, vague 4) : un cas
d'enregistrement compté une fois, une erreur que le panneau montre déjà
comptée une fois, le libellé sans nature, deux ISRC et deux compteurs
différents = deux enregistrements."""

from __future__ import annotations

from src.services import audit as au
from src.utils import meme_enregistrement as me


def _pistes(*couples):
    return [{"id": i, "genius_id": g} for i, g in couples]


class TestUnCasParEnregistrement:
    def test_deux_lignes_soeurs_comptent_une_fois(self):
        """« Le vent souffle sur le temps » chez Furax Barbarossa et Scylla ;
        « 93 Empire » chez Kalash Criminel et Sofiane (paire de paires)."""
        pistes = _pistes((23346, 9), (23659, 9), (27111, 1), (27112, 2), (24794, 1), (24795, 2))
        cas = [
            au.Cas("duree_paroles", "Furax", [23346], "a"),
            au.Cas("duree_paroles", "Scylla", [23659], "b"),
            au.Cas("id_partage", "Kalash", [27111, 27112], "c", au.DECISION),
            au.Cas("id_partage", "Sofiane", [24794, 24795], "d", au.DECISION),
        ]
        assert [x.libelle for x in au.un_cas_par_enregistrement(cas, pistes)] == ["a", "c"]

    def test_deux_cas_de_la_meme_fiche_restent_deux(self):
        """Deux sources en échec sur une même fiche : deux cas."""
        cas = [
            au.Cas("echec_mal_classe", "Isha", [1], "lrclib"),
            au.Cas("echec_mal_classe", "Isha", [1], "genius"),
        ]
        assert len(au.un_cas_par_enregistrement(cas, _pistes((1, 5)))) == 2

    def test_fiches_sans_page_et_familles_par_ligne(self):
        pistes = _pistes((1, None), (2, None), (3, 7), (4, 7))
        cas = [
            au.Cas("duree_paroles", "A", [1], "sans page 1"),
            au.Cas("duree_paroles", "B", [2], "sans page 2"),
            au.Cas("certif_version", "A", [3], "certif A"),
            au.Cas("certif_version", "B", [4], "certif B"),
            au.Cas("soeurs_divergentes", "A", [3], "inv A"),
            au.Cas("soeurs_divergentes", "B", [4], "inv B"),
            au.Cas("duree_paroles", "A", [3], "x", au.INDECIDABLE),
            au.Cas("duree_paroles", "B", [4], "y", au.ERREUR),
        ]
        assert len(au.un_cas_par_enregistrement(cas, pistes)) == 8


class TestMontresParLePanneau:
    def test_l_erreur_que_le_panneau_montre_n_est_pas_comptee_deux_fois(self):
        cas = [
            au.Cas("id_partage", "Kalash", [23848, 23852], "Tigini (Remix)"),
            au.Cas("revue:id_catalogue_partage", "Kalash", [23848], "x", au.DECISION),
            au.Cas("lrc_version", "The Game", [7564, 9909], "LOUIE BAGS"),
            au.Cas("revue:lrc_autre_fiche", "The Game", [7564], "y", au.DECISION),
        ]
        au.montres_par_le_panneau(cas)
        assert [x.nature for x in cas[::2]] == [au.INFORMATION, au.INFORMATION]
        assert "montré par le panneau (id_catalogue_partage)" in cas[0].libelle

    def test_un_autre_sujet_ou_un_cas_hors_critere_ne_suffit_pas(self):
        cas = [
            au.Cas("id_partage", "A", [1, 2], "id"),
            au.Cas("revue:bpm", "A", [1], "bpm", au.DECISION),
            au.Cas("lrc_version", "A", [3, 4], "lrc"),
            au.Cas("revue:lrc_autre_fiche", "A", [3], "z", au.INDECIDABLE),
            au.Cas("meme_enregistrement", "A", [5, 6], "m", au.DECISION),
            au.Cas("revue:doublon", "A", [5], "d", au.DECISION),
        ]
        au.montres_par_le_panneau(cas)
        assert cas[0].nature == au.ERREUR and cas[2].nature == au.ERREUR
        assert cas[4].nature == au.DECISION


def test_le_libelle_d_une_famille_ne_dit_pas_sa_nature():
    """Le bloc du tableau dit la nature : « DÉCISION — … » s'affichait dans le
    bloc « pour information » (hors_du_disque, meme_enregistrement)."""
    assert not [f for f, lib in au.FAMILLES.items() if lib.upper().startswith("DÉCISION")]


class TestDeuxEnregistrements:
    def test_interlude_reloaded(self):
        """Vald : ISRC différents, 4,0 M contre 2,8 M — un remix, pas une fusion."""
        assert me.deux_enregistrements({"FRDFG2500270"}, {"FRDFG2500140"}, 4_033_947, 2_790_089)
        assert me.verdict({}, 0.99, True, 1, True) == me.DEUX_ENREGISTREMENTS

    def test_un_seul_compteur_ou_un_isrc_commun_ne_suffit_pas(self):
        """« WTC » / « Astronaut pussy » : un seul compteur, la décision reste."""
        assert not me.deux_enregistrements({"FR5R00904812"}, {"USYBL1301287"}, None, 4_906_330)
        assert not me.deux_enregistrements({"X1"}, {"X1", "X2"}, 10, 1_000)
        assert not me.deux_enregistrements(None, {"X2"}, 10, 1_000)

    def test_deux_compteurs_presque_egaux_sont_egaux(self):
        assert not me.deux_enregistrements({"A"}, {"B"}, 1_000_000, 1_010_000)
        assert me.verdict({}, 0.99, True, 1, False) == me.MEME
