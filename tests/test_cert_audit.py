"""Tests de `CertMatcher.audit_artist_certifications` (porté hors DB).

Construit un matcher SANS charger les données réelles (via __new__) et lui
injecte une df contrôlée, pour tester le filtrage SNEP + détection
orphelins/matches indépendamment des fichiers de certifs.
"""

import pandas as pd

from src.utils.cert_matcher import CertMatcher
from src.utils.cert_normalize import normalize_text as N


def _matcher_with(rows) -> CertMatcher:
    m = CertMatcher.__new__(CertMatcher)  # bypass __init__ (pas de chargement)
    m._norm = N
    m.df = pd.DataFrame(rows)
    m._cache_artiste = {}  # posé par __init__, que ce harnais court-circuite
    return m


def _row(body, artist, title, cat="single", level="Or", date="2020-01-01"):
    return {
        "body": body,
        "artist_clean": N(artist),
        "title_clean": N(title),
        "cat": cat,
        "title": title,
        "level": level,
        "date": date,
    }


def test_filtre_snep_uniquement():
    m = _matcher_with(
        [
            _row("SNEP", "Jul", "Bande organisée"),
            _row("RIAA", "Jul", "Autre"),  # ignoré (pas SNEP)
        ]
    )
    res = m.audit_artist_certifications("Jul", ["Bande organisée"], [])
    assert res["total"] == 1
    assert res["matched_tracks"] == 1
    assert res["orphans"] == []


def test_orphelin_detecte():
    m = _matcher_with(
        [
            _row("SNEP", "Jul", "Bande organisée"),
            _row("SNEP", "Jul", "Titre Absent XYZ"),
        ]
    )
    res = m.audit_artist_certifications("Jul", ["Bande organisée"], [])
    assert res["total"] == 2
    assert res["matched_tracks"] == 1
    assert len(res["orphans"]) == 1
    assert res["orphans"][0]["title"] == "Titre Absent XYZ"


def test_artiste_mot_entier():
    # 'IAM' ne doit pas matcher 'WILLIAMS' (filtre mot entier)
    m = _matcher_with([_row("SNEP", "Williams", "Chanson")])
    res = m.audit_artist_certifications("IAM", ["Chanson"], [])
    assert res["total"] == 0


def test_titre_sous_chaine_intra_mot_reste_orphelin():
    """Le filtre ARTISTE comptait déjà en mots entiers ; le rapprochement de
    TITRE, lui, se faisait par sous-chaîne nue. Une certification « Ares » était
    donc déclarée rattachée à notre morceau « La paresse » — et l'audit annonçait
    zéro orphelin, soit l'inverse de ce qu'on lui demande."""
    m = _matcher_with([_row("SNEP", "Jul", "Ares")])
    res = m.audit_artist_certifications("Jul", ["La paresse"], [])
    assert res["matched_tracks"] == 0
    assert [o["title"] for o in res["orphans"]] == ["Ares"]


def test_titre_inclus_en_mot_entier_reste_rattache():
    """Le relâchement utile survit : une certification au titre nu se rattache
    à notre morceau qui le porte augmenté d'une mention."""
    m = _matcher_with([_row("SNEP", "Jul", "Bande organisée")])
    res = m.audit_artist_certifications("Jul", ["Bande organisée (Remix)"], [])
    assert res["matched_tracks"] == 1
    assert res["orphans"] == []


def test_album_compare_aux_albums():
    m = _matcher_with([_row("SNEP", "Jul", "Mon Album", cat="album", level="Platine")])
    res = m.audit_artist_certifications("Jul", ["un morceau"], ["Mon Album"])
    assert res["matched_albums"] == 1
    assert res["orphans"] == []


def test_matcher_vide():
    m = _matcher_with([])
    res = m.audit_artist_certifications("Jul", ["x"], [])
    assert res["total"] == 0
    assert res["orphans"] == []


def test_certif_au_titre_tout_symbole_ni_matchee_ni_orpheline():
    """« ÷ » se normalise en chaîne vide : impossible de la rapprocher de quoi
    que ce soit, mais l'accuser d'être orpheline serait faux. Elle est comptée
    au total et écartée du verdict."""
    m = _matcher_with(
        [
            _row("SNEP", "Ed Sheeran", "÷", cat="album"),
            _row("SNEP", "Ed Sheeran", "Shape of You"),
        ]
    )
    res = m.audit_artist_certifications("Ed Sheeran", ["Shape of You"], [])
    assert res["total"] == 2
    assert res["matched_tracks"] == 1
    assert res["orphans"] == []


def test_les_groupes_de_l_artiste_sont_audites_avec_lui():
    """2026-10-10 : l'audit n'interrogeait que le nom principal — les certifs
    d'IAM restaient hors audit pour Shurik'n, dont la discographie RÉUNIE
    contient pourtant les morceaux du groupe."""
    m = _matcher_with(
        [
            _row("SNEP", "Shurik'n", "Où je vis", cat="album"),
            _row("SNEP", "IAM", "Petit frère"),
            _row("SNEP", "IAM", "Absent du corpus"),
        ]
    )
    res = m.audit_artist_certifications(
        "Shurik'n", ["Petit frère"], ["Où je vis"], autres_noms=["IAM"]
    )
    assert res["total"] == 3
    assert res["matched"] == 2
    assert [(o["title"], o["nom"]) for o in res["orphans"]] == [("Absent du corpus", "IAM")]


def test_une_certif_vue_par_deux_noms_compte_une_fois():
    m = _matcher_with([_row("SNEP", "Kanye West & Kid Cudi", "Reborn")])
    res = m.audit_artist_certifications("Kanye West", ["Reborn"], [], autres_noms=["Kid Cudi"])
    assert res["total"] == 1
