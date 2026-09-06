"""Validateur du CSV RIAA (`riaa_validator`).

Troisième de la famille (cf. test_snep_validator.py, test_brma_validator.py),
sur le format américain : colonnes en Capitales, dates « October 17, 2017 »,
niveaux « 4x Multi-Platinum ». Deux particularités à ne pas confondre avec les
autres validateurs — une date ABSENTE compte ici comme illisible, et la
catégorie n'entre pas dans le verdict (seuls les niveaux ont un référentiel).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta

import pytest

from src.utils import riaa_validator as rv
from src.utils.riaa_validator import format_report, validate_riaa_csv

HEADER = "Artist,Title,Certification_Date,Label,Format_Type,Certification_Type"


def _ligne(
    artist="DRAKE",
    title="VIEWS",
    date="January 5, 2018",
    label="OVO",
    fmt="ALBUM",
    lvl="Diamond",
):
    return ",".join([artist, title, f'"{date}"', label, fmt, lvl])


def _ecrire(path, lignes, header=HEADER):
    path.write_text(header + "\n" + "\n".join(lignes) + "\n", encoding="utf-8-sig")
    return path


def _us(decalage_jours=0):
    return (datetime.now() - timedelta(days=decalage_jours)).strftime("%B %d, %Y")


@pytest.fixture
def csv_sain(tmp_path):
    return _ecrire(tmp_path / "certif_riaa.csv", [_ligne(date=_us())])


class TestHelpers:
    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("October 17, 2017", "2017-10-17"),
            ("Oct 17, 2017", "2017-10-17"),
            ("10/17/2017", "2017-10-17"),
            ("2017-10-17", "2017-10-17"),
            ("", ""),
            ("None", ""),
            ("17 octobre 2017", ""),  # format inconnu → vide, PAS la chaîne d'origine
        ],
    )
    def test_conversion_de_date(self, entree, attendu):
        assert rv._to_iso(entree) == attendu

    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("4x Multi-Platinum", "4X PLATINUM"),
            ("4x MultiPlatinum", "4X PLATINUM"),
            ("Multi-Platinum", "PLATINUM"),
            ("Gold", "GOLD"),
            ("  Diamond  ", "DIAMOND"),
        ],
    )
    def test_normalisation_de_niveau(self, entree, attendu):
        assert rv._level_norm(entree) == attendu

    @pytest.mark.parametrize(
        "niveau",
        ["Gold", "platinum", "Diamond", "Multi-Platinum", "4x Multi-Platinum", "2x Platinum"],
    )
    def test_niveaux_connus(self, niveau):
        assert rv._level_known(niveau) is True

    @pytest.mark.parametrize("niveau", ["Silver", "Bronze", "4x Gold", ""])
    def test_niveaux_inconnus(self, niveau):
        assert rv._level_known(niveau) is False


class TestChargement:
    def test_fichier_absent(self, tmp_path):
        rapport = validate_riaa_csv(tmp_path / "fantome.csv")
        assert rapport["ok"] is False
        assert "introuvable" in rapport["errors"][0]

    def test_colonnes_manquantes_bloquent(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", ["DRAKE,Gold"], header="Artist,Certification_Type")
        rapport = validate_riaa_csv(p)
        assert "Colonnes manquantes" in rapport["errors"][0]

    def test_colonnes_insensibles_a_la_casse(self, tmp_path):
        """Le schéma RIAA a changé de casse au fil des exports : les deux passent."""
        p = _ecrire(
            tmp_path / "c.csv",
            ['DRAKE,VIEWS,"January 5, 2018",OVO,ALBUM,Diamond'],
            header="artist,title,certification_date,label,format_type,certification_type",
        )
        rapport = validate_riaa_csv(p)
        assert rapport["errors"] == []
        assert rapport["stats"]["n_rows"] == 1

    def test_colonne_optionnelle_absente(self, tmp_path):
        """Format_Type n'est pas requis : son absence donne une colonne vide,
        pas un crash."""
        p = _ecrire(
            tmp_path / "c.csv",
            ['DRAKE,VIEWS,"January 5, 2018",Diamond'],
            header="Artist,Title,Certification_Date,Certification_Type",
        )
        rapport = validate_riaa_csv(p)
        assert rapport["errors"] == []
        assert rapport["formats"] == {}

    def test_aucun_encodage_ne_passe(self, tmp_path, monkeypatch):
        p = _ecrire(tmp_path / "c.csv", [_ligne()])

        def _refuse(*a, **k):
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "illisible")

        monkeypatch.setattr(rv.pd, "read_csv", _refuse)
        assert "Encodage illisible" in validate_riaa_csv(p)["errors"][0]

    def test_encodage_de_repli(self, tmp_path):
        p = tmp_path / "c.csv"
        p.write_bytes((HEADER + "\n" + _ligne(artist="BEYONCÉ") + "\n").encode("cp1252"))
        assert validate_riaa_csv(p)["stats"]["n_rows"] == 1


class TestChampsCritiques:
    def test_artiste_vide_bloquant(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(artist="")])
        rapport = validate_riaa_csv(p)
        assert rapport["empty_critical"] == 1
        assert rapport["ok"] is False

    def test_titre_vide_informatif(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(title="", date=_us())])
        rapport = validate_riaa_csv(p)
        assert rapport["empty_titles"] == 1
        assert rapport["ok"] is True

    def test_niveau_vide_non_bloquant(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl="", date=_us())])
        rapport = validate_riaa_csv(p)
        assert rapport["empty_levels"] == 1
        assert rapport["ok"] is True


class TestReferentiel:
    def test_niveau_inconnu(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl="Silver")])
        rapport = validate_riaa_csv(p)
        assert rapport["invalid_levels"] == ["Silver"]
        assert rapport["ok"] is False

    def test_multiplicateur_valide(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl="4x Multi-Platinum", date=_us())])
        rapport = validate_riaa_csv(p)
        assert rapport["invalid_levels"] == []
        assert rapport["ok"] is True

    def test_formats_recenses(self, tmp_path):
        p = _ecrire(
            tmp_path / "c.csv",
            [_ligne(fmt="ALBUM"), _ligne(title="B", fmt="SINGLE"), _ligne(title="C", fmt="ALBUM")],
        )
        assert validate_riaa_csv(p)["formats"] == {"ALBUM": 2, "SINGLE": 1}


class TestDoublons:
    def test_doublon_exact(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(), _ligne()])
        rapport = validate_riaa_csv(p)
        assert rapport["stats"]["duplicates"] == 1
        assert rapport["ok"] is False

    def test_niveau_normalise_avant_comparaison(self, tmp_path):
        """« 4x Multi-Platinum » et « 4x MultiPlatinum » sont le MÊME palier
        écrit de deux façons : c'est un doublon, pas deux certifications."""
        p = _ecrire(
            tmp_path / "c.csv",
            [_ligne(lvl="4x Multi-Platinum"), _ligne(lvl="4x MultiPlatinum")],
        )
        assert validate_riaa_csv(p)["stats"]["duplicates"] == 1

    def test_dates_equivalentes_normalisees(self, tmp_path):
        """La clé porte la date ISO : « January 5, 2018 » et « 01/05/2018 »
        désignent le même jour."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(date="January 5, 2018"), _ligne(date="01/05/2018")])
        assert validate_riaa_csv(p)["stats"]["duplicates"] == 1

    def test_formats_differents_conserves(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(fmt="ALBUM"), _ligne(fmt="SINGLE")])
        assert validate_riaa_csv(p)["stats"]["duplicates"] == 0

    def test_artiste_vide_exclu(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(artist=""), _ligne(artist="")])
        assert validate_riaa_csv(p)["stats"]["duplicates"] == 0

    def test_exemples_plafonnes(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne() for _ in range(20)])
        rapport = validate_riaa_csv(p)
        assert rapport["stats"]["duplicates"] == 19
        assert len(rapport["duplicates"]) == 12


class TestDates:
    def test_plage_couverte(self, tmp_path):
        p = _ecrire(
            tmp_path / "c.csv",
            [_ligne(date="January 5, 2018"), _ligne(title="B", date="March 15, 2021")],
        )
        rapport = validate_riaa_csv(p)
        assert rapport["date_range"] == "05/01/2018 → 15/03/2021"
        assert rapport["latest_date"] == "15/03/2021"

    def test_date_absente_compte_comme_illisible(self, tmp_path):
        """Écart ASSUMÉ avec le validateur SNEP : côté RIAA une case vide et une
        date incompréhensible sont traitées pareil (toutes deux donnent un ISO
        vide) — c'est une donnée qu'on n'a pas datée, dans les deux cas."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(date=""), _ligne(title="B", date="hier")])
        assert validate_riaa_csv(p)["date_parse_failures"] == 2

    def test_base_a_backfiller(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(date=_us(120))])
        assert any("backfiller" in w for w in validate_riaa_csv(p)["warnings"])

    def test_base_a_jour(self, tmp_path):
        """Seuil RIAA à 60 jours : les publications américaines sont espacées."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(date=_us(30))])
        assert validate_riaa_csv(p)["warnings"] == []

    def test_aucune_date_valide(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(date="")])
        rapport = validate_riaa_csv(p)
        assert rapport["date_range"] is None
        assert rapport["per_year"] == {}


class TestCouvertureTemporelle:
    def _annee(self, annee, nombre, mois=1):
        return [_ligne(title=f"T{annee}_{i}", date=f"{annee}-{mois:02d}-15") for i in range(nombre)]

    def test_comptes_par_annee(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", self._annee(2018, 2) + self._annee(2020, 3))
        assert validate_riaa_csv(p)["per_year"] == {2018: 2, 2019: 0, 2020: 3}

    def test_annees_absentes_aux_bords(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", self._annee(2018, 2) + self._annee(2022, 2))
        assert validate_riaa_csv(p)["missing_years"] == [2019, 2021]

    def test_annee_peu_active_non_scannee(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", self._annee(2020, 5))
        assert validate_riaa_csv(p)["month_gaps"] == []

    def test_annee_active_scannee(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", self._annee(2020, 30))
        assert validate_riaa_csv(p)["month_gaps"] == [f"2020-{m:02d}" for m in range(2, 13)]

    def test_mois_futurs_ignores(self, tmp_path):
        annee = datetime.now().year
        p = _ecrire(tmp_path / "c.csv", self._annee(annee, 30))
        mois = [int(g.split("-")[1]) for g in validate_riaa_csv(p)["month_gaps"]]
        assert mois
        assert max(mois) <= datetime.now().month

    def test_mois_faibles_et_comptes_recents(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", self._annee(2020, 2, mois=3) + self._annee(2020, 5, mois=4))
        rapport = validate_riaa_csv(p, recent_years=(2020,))
        assert rapport["low_months"] == ["2020-03 (2)"]
        assert rapport["stats"]["count_2020"] == 7


class TestRapport:
    def test_erreur_court_circuite(self, tmp_path):
        texte = format_report(validate_riaa_csv(tmp_path / "fantome.csv"))
        assert "introuvable" in texte
        assert "Verdict" not in texte

    def test_verdict(self, csv_sain, tmp_path):
        assert "RAS" in format_report(validate_riaa_csv(csv_sain))
        p = _ecrire(tmp_path / "c.csv", [_ligne(), _ligne()])
        assert "anomalies" in format_report(validate_riaa_csv(p))

    def test_sections(self, tmp_path):
        p = _ecrire(
            tmp_path / "c.csv",
            [
                _ligne(artist="", title="", lvl=""),
                _ligne(lvl="Silver", date=""),
                _ligne(),
                _ligne(),
            ],
        )
        texte = format_report(validate_riaa_csv(p))
        for attendu in (
            "Artiste vide",
            "Titre vide",
            "Niveau vide",
            "Dates illisibles",
            "Doublons exacts",
            "Niveaux hors référentiel",
            "Formats (info)",
        ):
            assert attendu in texte, attendu

    def test_sections_temporelles(self, tmp_path):
        lignes = [_ligne(title=f"T{i}", date=f"2020-01-{i + 1:02d}") for i in range(30)]
        lignes += [_ligne(title="vieux", date="2018-06-15")]
        texte = format_report(validate_riaa_csv(_ecrire(tmp_path / "c.csv", lignes)))
        assert "Années ENTIÈREMENT absentes" in texte
        assert "Mois SANS certification" in texte
        assert "Comptes par année" in texte

    def test_troncature_des_longues_listes(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl=f"Niveau{i}") for i in range(20)])
        assert "autre(s)" in format_report(validate_riaa_csv(p))

    def test_mois_faibles_affiches(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(date="2020-03-01")])
        assert "Mois à faible couverture" in format_report(
            validate_riaa_csv(p, recent_years=(2020,))
        )

    def test_tableau_par_annee_se_replie(self, tmp_path):
        lignes = [_ligne(title="v", date="2000-06-15"), _ligne(title="r", date="2020-06-15")]
        bloc = format_report(validate_riaa_csv(_ecrire(tmp_path / "c.csv", lignes)))
        bloc = bloc.split("Comptes par année")[1]
        assert len([ln for ln in bloc.splitlines() if ":" in ln]) > 1


class TestCLI:
    def test_chemin_par_defaut(self):
        chemin = rv._default_csv_path()
        assert chemin.name == "certif_riaa.csv"
        assert chemin.parts[-3:-1] == ("certifications", "riaa")

    def test_main_avec_chemin(self, csv_sain, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["riaa_validator", str(csv_sain)])
        rv.main()
        assert "VALIDATION DU CSV RIAA" in capsys.readouterr().out

    def test_main_sans_argument(self, monkeypatch, capsys):
        vu = {}
        monkeypatch.setattr(rv, "validate_riaa_csv", lambda p: vu.setdefault("path", p) or {})
        monkeypatch.setattr(rv, "format_report", lambda r: "stub")
        monkeypatch.setattr(sys, "argv", ["riaa_validator"])
        rv.main()
        capsys.readouterr()
        assert vu["path"].name == "certif_riaa.csv"
