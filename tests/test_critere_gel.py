"""Critère de fin du gel, quatre volets (décision utilisateur 2026-11-22)."""

from datetime import datetime

from src.services import critere_gel as cg


def _audit(date, cas, perimetre=cg.BASE_ENTIERE):
    return {"date": date, "perimetre": perimetre, "cas": cas}


def _cas(famille, pistes, nature="decision", artiste="A"):
    return {"famille": famille, "artiste": artiste, "pistes": pistes, "nature": nature}


def test_un_invariant_en_erreur_fait_tomber_le_volet():
    v = cg.volet_invariants(_audit("2026-11-26", [_cas("refus_vivant", [1], "erreur")]))
    assert v.tenu is False and v.valeur == "1"
    v = cg.volet_invariants(_audit("2026-11-26", [_cas("lrc_trop_long", [1], "erreur")]))
    assert v.tenu is True


def test_les_regressions_hors_fenetre_ne_comptent_pas():
    hausses = [
        {"date": "2026-11-01T10:00:00", "nouveaux": [_cas("x", [1], "erreur")]},
        {"date": "2026-11-20T10:00:00", "nouveaux": [_cas("y", [2], "information")]},
    ]
    assert cg.volet_regressions(hausses, datetime(2026, 11, 26)).tenu is True
    hausses.append({"date": "2026-11-25T10:00:00", "nouveaux": [_cas("z", [3])]})
    assert cg.volet_regressions(hausses, datetime(2026, 11, 26)).valeur == "1"


def test_l_age_d_une_decision_est_celui_de_sa_premiere_apparition():
    vieux = _cas("revue:bpm", [2, 1])
    audits = [
        _audit("2026-11-01T00:00:00", [vieux]),
        _audit("2026-11-10T00:00:00", [_cas("revue:bpm", [1, 2]), _cas("revue:x", [9])]),
        # Un audit d'artiste ne date rien.
        _audit("2026-10-01T00:00:00", [_cas("revue:y", [5])], perimetre="Booba"),
        _audit("2026-11-26T00:00:00", [_cas("revue:bpm", [1, 2]), _cas("revue:y", [5])]),
    ]
    v = cg.volet_delai(audits)
    assert v.valeur == "1"  # revue:bpm, vu le 1/11 ; revue:y vu le 26/11 seulement
    assert v.tenu is False


def test_un_cycle_qui_ouvre_plus_qu_il_ne_ferme_ne_tient_pas():
    audits = [
        _audit("2026-11-20T00:00:00", [_cas("a", [1])]),
        _audit("2026-11-26T00:00:00", [_cas("b", [2]), _cas("c", [3])]),
    ]
    v = cg.volet_delai(audits)
    assert v.valeur == "0" and v.tenu is False
    assert "1 fermées, 2 ouvertes" in v.detail[-1]


def test_deux_cas_sans_fiche_d_un_artiste_restent_deux():
    a = {**_cas("revue:deezer_version", []), "libelle": "X (Remix)"}
    b = {**_cas("revue:deezer_version", []), "libelle": "Y (Live)"}
    audits = [
        _audit("2026-11-20T00:00:00", [a]),
        _audit("2026-11-26T00:00:00", [a, b]),
    ]
    v = cg.volet_delai(audits)
    assert "0 fermées, 1 ouvertes" in v.detail[-1]


def test_le_temoin_non_rempli_ou_sans_seuil_n_est_jamais_tenu():
    assert cg.volet_temoin(None, {"duree": 0.02}).tenu is None
    dep = {"total": {"verifiees": 10}, "duree": {"taux": 0.1}}
    assert cg.volet_temoin(dep, None).tenu is None
    assert cg.volet_temoin(dep, {"duree": 0.05}).tenu is False
    assert cg.volet_temoin(dep, {"duree": 0.2}).tenu is True


def test_le_rapport_dit_incomplet_quand_un_volet_n_est_pas_mesure():
    volets = [cg.Volet("a", True, "0"), cg.Volet("b", None, "non mesuré")]
    assert "INCOMPLET" in cg.rapport(volets)
    assert "NON TENU" in cg.rapport(volets + [cg.Volet("c", False, "1")])
