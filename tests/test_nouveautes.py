"""« Y a-t-il des titres que nous n'avons pas ? » — un appel, une fois par jour.

Aucun test ne parle à Genius : le client est un faux qui COMPTE ses appels,
c'est lui qui gèle le plafond journalier.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import requests

from src.services import nouveautes
from src.utils import nouveautes_cache


@pytest.fixture(autouse=True)
def _cache_isole(tmp_path, monkeypatch):
    """Le cache est un FICHIER : sans redirection, un test écrirait dans
    `data/` réel (défaut mesuré le 2026-09-03 sur les fixtures dataviz)."""
    monkeypatch.setattr(nouveautes_cache, "FICHIER", tmp_path / "nouveautes_cache.json")


class _Genius:
    def __init__(self, songs=None, erreur=None):
        self._songs = songs if songs is not None else []
        self._erreur = erreur
        self.appels = 0

    def artist_songs(self, artist_id, sort=None, per_page=None, page=None):
        self.appels += 1
        self.demande = {"id": artist_id, "sort": sort, "per_page": per_page, "page": page}
        if self._erreur:
            raise self._erreur
        return {"songs": self._songs}


def _runtime(genius, tracks=()):
    return SimpleNamespace(
        genius_api=SimpleNamespace(genius=genius),
        data_manager=SimpleNamespace(get_artist_tracks=lambda aid: list(tracks)),
    )


def _artist(genius_id=1236609, tracks=None):
    return SimpleNamespace(id=7, name="Isha", genius_id=genius_id, tracks=tracks)


def _song(sid, titre):
    return {"id": sid, "title": titre, "primary_artist": {"name": "ISHA"}}


def test_titres_inconnus_sont_des_nouveautes():
    genius = _Genius([_song(1, "Durag"), _song(2, "Karma"), _song(3, "Inédit")])
    base = [SimpleNamespace(genius_id=1), SimpleNamespace(genius_id=2)]
    v = nouveautes.verifier(_runtime(genius, base), _artist())
    assert [n.titre for n in v.nouveautes] == ["Inédit"]
    assert v.concluante and v.nombre == 1 and not v.depuis_le_cache
    # UN appel, page 1, triée par date de sortie.
    assert genius.appels == 1 and genius.demande["page"] == 1
    assert genius.demande["sort"] == "release_date"


def test_rien_a_signaler_rend_une_liste_vide():
    genius = _Genius([_song(1, "Durag")])
    v = nouveautes.verifier(_runtime(genius, [SimpleNamespace(genius_id=1)]), _artist())
    assert v.nouveautes == () and v.concluante
    assert "Aucun titre" in nouveautes.resume(v, "Isha")


def test_la_discographie_chargee_sert_de_reference():
    """La vérification tombe souvent AVANT le chargement des morceaux ; quand
    ils sont là, on ne relit pas la base pour rien."""
    genius = _Genius([_song(1, "Durag")])
    rt = _runtime(genius, tracks=[])
    v = nouveautes.verifier(rt, _artist(tracks=[SimpleNamespace(genius_id=1)]))
    assert v.nouveautes == ()


def test_cache_evite_un_second_appel_dans_la_journee():
    genius = _Genius([_song(9, "Nouveau")])
    rt, art = _runtime(genius), _artist()
    premiere = nouveautes.verifier(rt, art)
    seconde = nouveautes.verifier(rt, art)
    assert genius.appels == 1
    assert seconde.depuis_le_cache and seconde.nombre == premiere.nombre == 1
    assert [n.titre for n in seconde.nouveautes] == ["Nouveau"]


def test_force_ignore_le_cache():
    genius = _Genius([_song(9, "Nouveau")])
    rt, art = _runtime(genius), _artist()
    nouveautes.verifier(rt, art)
    nouveautes.verifier(rt, art, force=True)
    assert genius.appels == 2


def test_le_cache_perime_repart_sur_le_reseau():
    genius = _Genius([_song(9, "Nouveau")])
    rt, art = _runtime(genius), _artist()
    nouveautes.verifier(rt, art)
    nouveautes.verifier(rt, art, maintenant=datetime.now() + timedelta(hours=25))
    assert genius.appels == 2


def test_genius_injoignable_ne_ment_pas():
    """Une liste vide AVEC un motif : le badge dit « impossible », pas « 0 »."""
    genius = _Genius(erreur=requests.RequestException("timeout"))
    v = nouveautes.verifier(_runtime(genius), _artist())
    assert v.nouveautes == () and not v.concluante
    assert "injoignable" in v.motif
    assert nouveautes.resume(v).startswith("Vérification impossible")


def test_un_artiste_sans_identifiant_genius_ne_conclut_pas():
    genius = _Genius([_song(1, "X")])
    v = nouveautes.verifier(_runtime(genius), _artist(genius_id=None))
    assert not v.concluante and genius.appels == 0


def test_resume_cite_cinq_titres_et_compte_le_reste():
    genius = _Genius([_song(i, f"T{i}") for i in range(1, 9)])
    v = nouveautes.verifier(_runtime(genius), _artist())
    texte = nouveautes.resume(v, "Isha")
    assert texte.startswith("8 titre(s)") and "et 3 autre(s)" in texte
