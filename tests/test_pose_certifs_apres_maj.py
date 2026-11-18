"""Pose des certifs NOUVELLES après une mise à jour globale (2026-11-18).

La mise à jour globale remplissait le corpus sans rien poser : une certif
arrivée pendant le passage d'un autre artiste attendait le prochain passage du
sien (audit du 2026-11-18 : 12 `certif_non_reliee` en erreur). Désormais les
artistes qu'une certif nouvelle crédite EN TÊTE sont reposés tout de suite.
"""

from types import SimpleNamespace

import src.utils.cert_matcher as module_matcher
from src.models import Artist
from src.services import artiste, certifs


def _certif(artiste_, titre, body="SNEP"):
    return {
        "body": body,
        "artist_name": artiste_,
        "title": titre,
        "category": "single",
        "certification": "Or",
        "certification_date": "2026-11-01",
        "country": "FR",
    }


class TestArtistesCredites:
    def test_seul_l_interprete_en_tete_est_credite(self):
        nouvelles = [_certif("NAS / N.T.M.", "Affirmative Action")]
        noms = {"Suprême NTM": ["Suprême NTM", "N.T.M."], "Nas": ["Nas"], "Booba": ["Booba"]}
        assert sorted(certifs.artistes_credites(nouvelles, noms)) == ["Nas", "Suprême NTM"]

    def test_par_egalite_jamais_par_mot_contenu(self):
        nouvelles = [_certif("TAYLOR SWIFT", "Shake It Off")]
        assert certifs.artistes_credites(nouvelles, {"Swift Guad": ["Swift Guad", "Swift"]}) == []

    def test_rien_de_nouveau_rien_a_poser(self):
        assert certifs.artistes_credites([], {"Booba": ["Booba"]}) == []


class _Matcher:
    def __init__(self, corpus):
        self.corpus = corpus

    def toutes(self):
        return list(self.corpus)


def test_seuls_les_artistes_d_une_certif_nouvelle_sont_reposes(monkeypatch):
    ancienne = _certif("BOOBA", "DKR")
    nouvelle = _certif("EVERLAST", "What It's Like")
    monkeypatch.setattr(module_matcher, "reset_cert_matcher", lambda: None)
    monkeypatch.setattr(module_matcher, "get_cert_matcher", lambda: _Matcher([ancienne]))
    avant = certifs.cles_du_corpus()
    monkeypatch.setattr(module_matcher, "get_cert_matcher", lambda: _Matcher([ancienne, nouvelle]))

    artistes = {n: Artist(name=n) for n in ("Booba", "Everlast")}
    dm = SimpleNamespace(get_artist_names=lambda: list(artistes), get_artist_by_name=artistes.get)
    runtime = SimpleNamespace(data_manager=dm)
    monkeypatch.setattr(certifs, "noms_d_audit_pour", lambda rt, a: ([a.name], []))
    monkeypatch.setattr(artiste, "charger", lambda rt, nom: artistes[nom])
    poses = []
    monkeypatch.setattr(
        certifs,
        "appliquer",
        lambda rt, a: poses.append(a.name) or certifs.BilanCertifs(rapport="1 morceau"),
    )

    lignes = certifs.reposer_apres_maj(runtime, avant)

    assert poses == ["Everlast"]
    assert lignes == ["Everlast : 1 morceau"]


def test_corpus_inchange_rien_n_est_reouvert(monkeypatch):
    corpus = [_certif("BOOBA", "DKR")]
    monkeypatch.setattr(module_matcher, "reset_cert_matcher", lambda: None)
    monkeypatch.setattr(module_matcher, "get_cert_matcher", lambda: _Matcher(corpus))
    avant = certifs.cles_du_corpus()
    dm = SimpleNamespace(get_artist_names=lambda: (_ for _ in ()).throw(AssertionError))
    assert certifs.reposer_apres_maj(SimpleNamespace(data_manager=dm), avant) == []
