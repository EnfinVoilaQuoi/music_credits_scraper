"""Ressorties à la graphie près (2026-11-26) : une mixtape lue par Genius,
ressortie des années plus tard sur les plateformes, où Deezer a créé une
seconde fiche sous une autre graphie du titre (« Hémophile » / « Hémophiles »
de Kaaris sur Z.E.R.O). Mesuré sur toute la base : 50 paires d'un même disque
rapprochées sur 73 candidates, toutes des graphies d'un même morceau."""

import pytest

from src.services import revue
from src.utils.version_descriptors import meme_titre_a_la_graphie_pres
from tests.test_revue import _ctx, _t


@pytest.mark.parametrize(
    "a, b",
    [
        ("Hémophile", "Hémophiles"),
        ("Épouventail", "Épouvantail"),
        ("Espoir et peine", "Espoirs et peines"),
        ("Noms d’oiseau", "Noms d'oiseaux"),
        ("J’viens de l’incendie", "J'viens d' l'incendie"),
        ("D’où je viens", "D'où j'viens"),
        ("Rue des bergères", "Rue de bergères"),
        ("De l’Opéra à la Plaine 2", "De l'opéra à la plaine II"),
        ("Cauchemar En Ville Part I", "Cauhemar en ville Part. 1"),
        ("Rouge Mississippi", "Rouge Mississipi"),
        ("Un jour de paix", "Jour de paix (feat. Black Rénégat)"),
    ],
)
def test_meme_morceau_a_la_graphie_pres(a, b):
    assert meme_titre_a_la_graphie_pres(a, b)
    assert meme_titre_a_la_graphie_pres(b, a)


@pytest.mark.parametrize(
    "a, b",
    [
        ("IO Love", "No Love"),  # l'initiale d'un mot
        ("Puissance 10", "Puissance"),  # un nombre de plus
        ("Poignée de punchlines 2.0", "Poignée de punchlines 2"),
        ("Troupeau d’buffles", "Troupeau d'buffles (remix)"),  # une version
        ("The Return of Chip Douglas", "The Return of Chip Douglas (Demo)"),
        ("On est encore là", "On est encore là, Pt. 1"),  # une partie
        ("Le billet de banque", "Le billet en banque"),
        ("Sushi", "Sashi"),  # trop court pour juger une lettre
    ],
)
def test_ce_qui_n_est_pas_une_graphie(a, b):
    assert not meme_titre_a_la_graphie_pres(a, b)


def _f(titre, tid, duree, genius_id=None, **kw):
    t = _t(titre, tid, duration=duree, genius_id=genius_id, **kw)
    if genius_id:
        t.genius_url = f"https://genius.com/{tid}"
    return t


def test_la_ressortie_sur_le_meme_disque_est_au_panneau():
    page = _f("Hémophile", 1, None, genius_id=11, album="Z.E.R.O")
    fiche = _f("Hémophiles", 2, 214, deezer_id=8, album="Z.E.R.O")
    motif, preuves = revue.page_et_fiche_plateforme(fiche, _ctx(page, fiche))
    assert "graphie" in motif and preuves["autres"] == [1]


def test_pas_d_un_disque_a_l_autre_ni_au_meme_identifiant():
    page = _f("Hémophile", 1, None, genius_id=11, album="Z.E.R.O")
    ailleurs = _f("Hémophiles", 2, 214, deezer_id=8, album="Les princes de la ville")
    assert revue.page_et_fiche_plateforme(ailleurs, _ctx(page, ailleurs)) is None
    page2 = _f("Hémophile", 3, None, genius_id=12, album="Z.E.R.O", deezer_id=9)
    meme_id = _f("Hémophiles", 4, 214, deezer_id=9, album="Z.E.R.O")
    assert revue.page_et_fiche_plateforme(meme_id, _ctx(page2, meme_id)) is None


def test_la_ressortie_a_la_graphie_pres_est_dans_la_liste_du_disque():
    """« Espoir et peine » de Swift Guad (C.P.C.D.M.C Vol. 2) : la ressortie
    Deezer du disque l'écrit « Espoirs et peines » — la fiche est sur le
    disque, pas « hors du disque »."""
    from src.utils.appartenance_disque import dans_la_liste

    fiche = {"title": "Espoir et peine"}
    liste = [{"id": "1", "isrc": None, "title": "Espoirs et peines"}]
    assert dans_la_liste(fiche, set(), set(), liste)
    assert not dans_la_liste(
        {"title": "Puissance 10"}, set(), set(), [{**liste[0], "title": "Puissance"}]
    )
