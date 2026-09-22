"""Le contexte de validation : deux lectures par ARTISTE, pas par morceau."""

from types import SimpleNamespace

from src.services.validation import construire_contexte
from src.utils.title_matching import cle_album


class _DM:
    def __init__(self, albums=(), parutions=(), erreur=None):
        self._albums = list(albums)
        self._parutions = list(parutions)
        self._erreur = erreur
        self.appels = 0

    def get_albums_for_artist(self, artist_id):
        self.appels += 1
        if self._erreur:
            raise self._erreur
        return self._albums

    def get_release_tracks_for_artist(self, artist_id, scope="own"):
        self.appels += 1
        assert scope == "own"
        return self._parutions


def _artist(id_=1):
    return SimpleNamespace(id=id_, name="A2H", tracks=[])


def test_les_types_d_albums_sont_indexes_par_cle():
    dm = _DM(albums=[{"title": "Vol. 1", "record_type": "ep"}, {"title": "Autre"}])
    ctx = construire_contexte(dm, _artist(), {7})
    assert ctx.types_par_album[cle_album("Vol.1")] == "ep"
    # Album connu mais NON typé : présent avec None — c'est ce qui permet de
    # dire « je ne sais pas » plutôt que d'exiger à tort.
    assert ctx.types_par_album[cle_album("Autre")] is None
    assert ctx.desactives == frozenset({7})


def test_le_type_le_plus_exigeant_gagne_pour_un_morceau_sur_deux_parutions():
    """Un titre sorti en single PUIS mis sur l'album EST sur l'album."""
    dm = _DM(
        parutions=[
            {"track_id": 5, "record_type": "single"},
            {"track_id": 5, "record_type": "album"},
            {"track_id": 6, "record_type": "album"},
            {"track_id": 6, "record_type": "single"},
        ]
    )
    ctx = construire_contexte(dm, _artist())
    assert ctx.types_par_morceau[5] == "album" and ctx.types_par_morceau[6] == "album"


def test_deux_lectures_seulement():
    dm = _DM(albums=[{"title": "X", "record_type": "album"}])
    construire_contexte(dm, _artist())
    assert dm.appels == 2


def test_sans_artiste_le_contexte_reste_utilisable():
    ctx = construire_contexte(_DM(), None, {3})
    assert ctx.desactives == frozenset({3}) and ctx.types_par_album == {}


def test_une_lecture_ratee_n_exige_rien_et_ne_casse_pas():
    """Sans types, la règle dit « je ne sais pas » — jamais un faux ⚠️."""
    ctx = construire_contexte(_DM(erreur=RuntimeError("base fermée")), _artist())
    assert ctx.types_par_album == {}
