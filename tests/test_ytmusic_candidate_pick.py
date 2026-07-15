"""Départage des homonymes par recouvrement d'albums (étape 4 du durcissement).

`_pick_best_candidate` confronte les albums de chaque canal candidat à la colonne
`album` de la base : le candidat au recouvrement STRICTEMENT le plus fort gagne,
même s'il n'est pas premier dans l'ordre de `yt.search`. Égalité ou base sans
albums → fallback statu quo (premier candidat avec albums).
"""

import src.utils.update_ytmusic as mod
from src.utils.title_matching import normalize_title as _n


class _API:
    def __init__(self, albums_by_cid):
        self.albums_by_cid = albums_by_cid

    def get_artist_info(self, cid):
        return {"albums": self.albums_by_cid.get(cid, []), "monthly_listeners": None}


def _albums(*titles):
    return [{"title": t, "browseId": f"B_{t}"} for t in titles]


# ── Score pur ────────────────────────────────────────────────────────────────


def test_score_compte_albums_communs_normalises():
    db = {_n("Bar"), _n("Baz")}
    assert mod._score_candidate_albums(["Foo", "Bar", "Baz"], db) == 2


def test_score_zero_sans_recouvrement():
    assert mod._score_candidate_albums(["Foo"], {_n("Bar")}) == 0


# ── _pick_best_candidate ─────────────────────────────────────────────────────


def test_homonyme_second_avec_albums_communs_retenu():
    # UC2 est SECOND dans l'ordre mais recouvre l'album de la base → retenu.
    api = _API({"UC1": _albums("Foo", "Bar"), "UC2": _albums("RealAlbum")})
    candidates = [("UC1", "X"), ("UC2", "X")]
    cid, info = mod._pick_best_candidate(api, candidates, {_n("RealAlbum")})
    assert cid == "UC2"
    assert info["albums"] == _albums("RealAlbum")


def test_egalite_de_score_retombe_sur_statu_quo():
    # Les deux recouvrent 1 album → pas de gagnant net → premier avec albums.
    api = _API({"UC1": _albums("Shared"), "UC2": _albums("Shared")})
    candidates = [("UC1", "X"), ("UC2", "X")]
    cid, _ = mod._pick_best_candidate(api, candidates, {_n("Shared")})
    assert cid == "UC1"


def test_base_sans_albums_statu_quo_premier_avec_albums():
    # db_norm_albums vide → dégénère : premier candidat AVEC albums (UC2).
    api = _API({"UC1": [], "UC2": _albums("Whatever")})
    candidates = [("UC1", "X"), ("UC2", "X")]
    cid, _ = mod._pick_best_candidate(api, candidates, set())
    assert cid == "UC2"


def test_aucun_candidat_avec_albums_prend_le_premier():
    api = _API({"UC1": [], "UC2": []})
    candidates = [("UC1", "X"), ("UC2", "X")]
    cid, info = mod._pick_best_candidate(api, candidates, set())
    assert cid == "UC1"
    assert info["albums"] == []


def test_candidats_vides_retourne_none():
    assert mod._pick_best_candidate(_API({}), [], set()) == (None, None)


def test_candidat_unique_score_nul_conserve():
    # Un seul candidat sans album commun : conservé (le gate d'identité protège l'aval).
    api = _API({"UC1": _albums("Foo")})
    cid, _ = mod._pick_best_candidate(api, [("UC1", "X")], {_n("Bar")})
    assert cid == "UC1"
