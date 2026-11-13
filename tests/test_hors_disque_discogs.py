"""R3 (décision utilisateur 2026-11-13) : 💿 hors du disque — ✓ Normal quand
l'édition physique Discogs du disque liste la fiche."""

from __future__ import annotations

from src.models import Artist, Track
from src.services import politiques_revue as pol
from src.services import revue

_A = Artist(name="Dosseh")


def _juger(titre="Tout est neuf", parution="VIDALO$$A", discogs_id=12225158, parution_discogs=None):
    t = Track(title=titre, artist=_A)
    t.id, t.discogs_id = 1, discogs_id
    cas = revue.Cas("hors_du_disque", 1, titre, "", 0, {"parution": parution}, "k")
    lue = (
        parution_discogs
        if parution_discogs is not None
        else {
            "title": "Vidalo$$a",
            "tracklist": [{"title": "Intro"}, {"title": "Tout Est Neuf"}],
        }
    )
    return pol.arbitrage_hors_disque(cas, t, None, lire_discogs=lambda _id: lue)


def test_l_edition_physique_la_liste_normal():
    arb = _juger()
    assert arb.action == "normal" and "12225158" in arb.raison


def test_absente_de_l_edition_physique_l_humain():
    assert (
        _juger(parution_discogs={"title": "Vidalo$$a", "tracklist": [{"title": "Intro"}]}) is None
    )


def test_une_autre_parution_discogs_ne_prouve_rien():
    autre = {"title": "Tout est neuf (Single)", "tracklist": [{"title": "Tout est neuf"}]}
    assert _juger(parution_discogs=autre) is None


def test_sans_parution_discogs_l_humain():
    assert _juger(discogs_id=None) is None
    assert _juger(parution_discogs={}) is None


def test_le_brut_est_lu_en_lecture_seule_et_isole_en_test():
    # `conftest` isole le brut : aucune parution gardée, rien n'est prouvé.
    t = Track(title="Tout est neuf", artist=_A)
    t.id, t.discogs_id = 1, 12225158
    cas = revue.Cas("hors_du_disque", 1, "Tout est neuf", "", 0, {"parution": "VIDALO$$A"}, "k")
    assert pol.arbitrage_hors_disque(cas, t, None) is None
