"""L'ancien scraper écrivait les awards LATINS en vocabulaire US : la même
certification existait deux fois, la ligne US au barème US (16,7× le latin).
Mesuré le 2026-10-11 : ~2 250 lignes, 8,4 Md d'unités fausses."""

import pandas as pd

from src.utils.update_riaa import _ecarter_jumelles_latines


def _ligne(niveau, programme, famille="", sortie="", titre="HABLAME BAJITO"):
    return {
        "Artist": "ABRAHAM MATEO",
        "Title": titre,
        "Certification_Date": "2018-03-01",
        "Format_Type": "SINGLE",
        "Release_Date": sortie,
        "Certification_Type": niveau,
        "Award_Programme": programme,
        "Award_Family": famille,
    }


def test_la_jumelle_us_d_un_award_latin_est_ecartee():
    df = pd.DataFrame([_ligne("Platinum", "US"), _ligne("Platino", "LATIN", famille="LA")])
    gardees, ecartees = _ecarter_jumelles_latines(df)
    assert list(gardees["Certification_Type"]) == ["Platino"]
    assert list(ecartees["Certification_Type"]) == ["Platinum"]


def test_le_diamant_canonise_retrouve_sa_jumelle():
    df = pd.DataFrame([_ligne("10x Platinum", "US"), _ligne("10x Platino", "LATIN")])
    assert len(_ecarter_jumelles_latines(df)[1]) == 1


def test_sans_jumelle_ou_avec_famille_la_ligne_us_reste():
    df = pd.DataFrame(
        [
            _ligne("Platinum", "US", titre="SOLO"),
            _ligne("Platinum", "US", famille="DI"),
            _ligne("Platino", "LATIN"),
            _ligne("2x Platinum", "US"),
        ]
    )
    gardees, ecartees = _ecarter_jumelles_latines(df)
    assert ecartees.empty and len(gardees) == 4
