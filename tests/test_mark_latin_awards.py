"""Réparation du programme des awards latins (`scripts/mark_latin_awards.py`).

Le corpus RIAA historique a été collecté sans distinguer les deux programmes :
un Oro latin y est écrit « Gold », indiscernable d'un Gold américain. Repérable
à l'œil : 110 lignes aux multiplicateurs absurdes. Mesuré pour de vrai : 1 597.

Le script n'extrapole pas — il demande au site la liste des awards latins (onglet
dédié) et descend jusqu'à la TIMELINE de chacun, qui donne chaque palier avec sa
date, soit exactement la granularité du corpus. Ces tests tiennent les trois
décisions de cet appariement, sans réseau.
"""

import pandas as pd
import pytest

from scripts.mark_latin_awards import (
    _LATIN_VERS_US,
    _US_VERS_LATIN,
    _traduire,
    index_latin,
    reparer,
)

COLONNES = [
    "Artist",
    "Title",
    "Certification_Date",
    "Certification_Type",
    "Award_Programme",
    "Units",
]


def _ligne(artiste="LUIS FONSI", titre="DESPACITO", date="June 8, 2017", niveau="55x Platinum"):
    return {
        "Artist": artiste,
        "Title": titre,
        "Certification_Date": date,
        "Certification_Type": niveau,
        "Award_Programme": "US",
        "Units": "55000000",
    }


def _award(artiste="LUIS FONSI", titre="DESPACITO", paliers=(("55x Platino", "June 8, 2017"),)):
    return {
        "artist": artiste,
        "title": titre,
        "award_level": paliers[0][0],
        "certification_date": paliers[0][1],
        "history": [
            {"certification_level": niveau, "certification_date": date} for niveau, date in paliers
        ],
    }


class TestTraduction:
    """Le NUMÉRO de palier est commun aux deux échelles ; seul le mot change."""

    @pytest.mark.parametrize(
        ("latin", "us"),
        [
            ("Oro", "Gold"),
            ("Platino", "Platinum"),
            ("Diamante", "Diamond"),
            ("15x Platino", "15x Platinum"),
        ],
    )
    def test_aller_retour(self, latin, us):
        assert _traduire(latin, _LATIN_VERS_US) == us
        assert _traduire(us, _US_VERS_LATIN) == latin

    def test_un_libelle_inconnu_est_rendu_tel_quel(self):
        assert _traduire("Ruby", _LATIN_VERS_US) == "Ruby"

    def test_la_forme_est_canonisee_au_passage(self):
        """Le corpus dit « 15x Multi-Platinum » ; la clé doit tomber juste."""
        assert _traduire("15X MULTI-PLATINUM", _US_VERS_LATIN) == "15x Platino"


class TestIndex:
    """Les événements sont indexés par (TITRE, DATE), l'artiste reste à côté.

    Il ne peut pas entrer dans la clé : le corpus dit « LUIS FONSI » là où le
    site dit « LUIS FONSI & DADDY YANKEE ». Il est donc comparé à part, en mots
    entiers — jamais par sous-chaîne nue.
    """

    def test_chaque_palier_de_la_timeline_est_date(self):
        evenements, paliers = index_latin(
            [_award(paliers=(("2x Platino", "June 8, 2017"), ("Oro", "January 3, 2016")))]
        )

        assert evenements[("DESPACITO", "2017-06-08")] == ["LUIS FONSI"]
        assert ("LUIS FONSI", "DESPACITO", "2016-01-03", "Gold") in paliers

    def test_un_award_sans_timeline_garde_sa_ligne_principale(self):
        award = _award()
        award["history"] = []
        evenements, paliers = index_latin([award])

        assert evenements == {("DESPACITO", "2017-06-08"): ["LUIS FONSI"]}
        assert paliers == {("LUIS FONSI", "DESPACITO", "2017-06-08", "55x Platinum")}

    def test_les_paliers_sans_date_sont_ecartes(self):
        """Sans date, la clé ne discrimine plus rien : mieux vaut ne rien dire."""
        assert index_latin([_award(paliers=(("Oro", ""),))]) == ({}, set())

    def test_un_artiste_credite_differemment_est_reconnu(self):
        """Le cas Despacito : « LUIS FONSI » dans le corpus, « LUIS FONSI &
        DADDY YANKEE » sur le site. Sans cette souplesse, 58 lignes restaient
        étiquetées US — dont les six paliers de Despacito."""
        df = pd.DataFrame([_ligne(niveau="55x Multi-Platinum")], columns=COLONNES)
        cles = index_latin(
            [
                _award(
                    artiste="LUIS FONSI & DADDY YANKEE",
                    paliers=(("55x Platino", "June 8, 2017"),),
                )
            ]
        )

        repare, rapport = reparer(df, *cles)

        assert rapport["marquees"] == 1
        assert repare.loc[0, "Certification_Type"] == "55x Platino"

    def test_un_homonyme_partiel_nest_PAS_reconnu(self):
        """La souplesse s'arrête aux mots entiers (règle projet, test structurel
        dédié) : « FONSI » ne doit pas matcher « ALFONSINA »."""
        df = pd.DataFrame([_ligne(artiste="ALFONSINA", niveau="Platinum")], columns=COLONNES)
        cles = index_latin([_award(artiste="FONSI", paliers=(("Platino", "June 8, 2017"),))])

        _repare, rapport = reparer(df, *cles)

        assert rapport["marquees"] == 0


class TestReparation:
    def test_marque_retraduit_et_recalcule(self):
        """Trois écritures, pas une : sans quoi une ligne « LATIN » annoncerait
        « Gold » à 500 000 unités — l'erreur qu'on répare."""
        df = pd.DataFrame([_ligne(niveau="Platinum")], columns=COLONNES)
        evenements, paliers = index_latin([_award(paliers=(("Platino", "June 8, 2017"),))])

        repare, rapport = reparer(df, evenements, paliers)

        assert rapport["marquees"] == 1
        assert repare.loc[0, "Award_Programme"] == "LATIN"
        assert repare.loc[0, "Certification_Type"] == "Platino"
        assert repare.loc[0, "Units"] == "60000"

    def test_une_ligne_non_appariee_reste_intacte(self):
        """Le point délicat : un morceau peut être certifié dans les DEUX
        programmes. Seules les lignes reconnues sont retournées."""
        df = pd.DataFrame([_ligne(niveau="Platinum", date="March 1, 2020")], columns=COLONNES)
        evenements, paliers = index_latin([_award(paliers=(("Platino", "June 8, 2017"),))])

        repare, rapport = reparer(df, evenements, paliers)

        assert rapport["marquees"] == 0
        assert repare.loc[0, "Award_Programme"] == "US"
        assert repare.loc[0, "Certification_Type"] == "Platinum"

    def test_les_lignes_deja_latines_ne_sont_pas_retouchees(self):
        df = pd.DataFrame(
            [{**_ligne(niveau="Platino"), "Award_Programme": "LATIN"}], columns=COLONNES
        )

        _repare, rapport = reparer(df, *index_latin([_award()]))

        assert rapport["deja_latines"] == 1
        assert rapport["marquees"] == 0

    def test_les_paliers_deplies_par_la_collecte_sont_attrapes(self):
        """La raison d'être de la clé « événement ».

        « NICKY JAM — EL AMANTE » occupe 14 lignes dans le corpus (Gold,
        Platinum, 2x … 13x), toutes datées du même jour, alors que la timeline du
        site n'a qu'UNE étape (13x Platino). Ces paliers intermédiaires n'ont
        jamais été des événements : ils appartiennent au même award latin. Une
        clé au palier près en laisserait 13 sur 14 étiquetés « US ».
        """
        df = pd.DataFrame(
            [
                _ligne(artiste="NICKY JAM", titre="EL AMANTE", niveau=n, date="September 18, 2017")
                for n in ("13x Platinum", "2x Platinum", "Platinum", "Gold")
            ],
            columns=COLONNES,
        )
        cles = index_latin(
            [
                _award(
                    artiste="NICKY JAM",
                    titre="EL AMANTE",
                    paliers=(("13x Platino", "September 18, 2017"),),
                )
            ]
        )

        repare, rapport = reparer(df, *cles)

        assert rapport["marquees"] == 4
        assert rapport["corroborees"] == 1  # seul le 13x est confirmé par le site
        assert list(repare["Certification_Type"]) == ["13x Platino", "2x Platino", "Platino", "Oro"]
        assert list(repare["Units"]) == ["780000", "120000", "60000", "30000"]

    def test_le_multiplicateur_est_conserve(self):
        df = pd.DataFrame([_ligne(niveau="55x Multi-Platinum")], columns=COLONNES)
        cles = index_latin([_award(paliers=(("55x Platino", "June 8, 2017"),))])

        repare, _rapport = reparer(df, *cles)

        assert repare.loc[0, "Certification_Type"] == "55x Platino"
        assert repare.loc[0, "Units"] == str(55 * 60_000)  # et non 55 millions
