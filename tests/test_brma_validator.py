"""Validateur du CSV BRMA / Ultratop (`brma_validator`).

Même famille que `snep_validator` (cf. test_snep_validator.py) mais sur un
format différent : séparateur VIRGULE, colonnes NOMMÉES, dates ISO, niveaux au
multiplicateur (« 2x Platine »). Les écarts entre les deux validateurs sont
volontaires et documentés ici — notamment la CATÉGORIE dans la clé de dédup
(un même nom peut être certifié en single ET en album) et le titre vide traité
en warning (c'est la signature d'une compilation).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta

import pytest

from src.utils import brma_validator as bv
from src.utils.brma_validator import format_report, validate_brma_csv

HEADER = "artist,title,category,certification_level,certification_date,year_page,detail_url"


def _ligne(
    artist="Angèle",
    title="Balance ton quoi",
    cat="singles",
    lvl="Or",
    date="2019-05-01",
    year="2019",
    url="https://ultratop.be/x",
):
    return ",".join([artist, title, cat, lvl, date, year, url])


def _ecrire(path, lignes, header=HEADER):
    path.write_text(header + "\n" + "\n".join(lignes) + "\n", encoding="utf-8-sig")
    return path


def _iso(decalage_jours=0):
    return (datetime.now() - timedelta(days=decalage_jours)).strftime("%Y-%m-%d")


@pytest.fixture
def csv_sain(tmp_path):
    return _ecrire(tmp_path / "certif_brma.csv", [_ligne(date=_iso())])


class TestChargement:
    def test_fichier_absent(self, tmp_path):
        rapport = validate_brma_csv(tmp_path / "fantome.csv")
        assert rapport["ok"] is False
        assert "introuvable" in rapport["errors"][0]

    def test_colonnes_manquantes_bloquent(self, tmp_path):
        """Sans les colonnes attendues, on s'arrête net : analyser au hasard
        produirait un rapport faussement rassurant."""
        p = _ecrire(tmp_path / "c.csv", ["Angèle,Or"], header="artist,certification_level")
        rapport = validate_brma_csv(p)
        assert "Colonnes manquantes" in rapport["errors"][0]
        assert "title" in rapport["errors"][0]
        assert rapport["stats"] == {}  # rien n'a été analysé

    def test_encodage_de_repli(self, tmp_path):
        p = tmp_path / "c.csv"
        p.write_bytes((HEADER + "\n" + _ligne(artist="Angèle") + "\n").encode("cp1252"))
        assert validate_brma_csv(p)["stats"]["n_rows"] == 1

    def test_aucun_encodage_ne_passe(self, tmp_path, monkeypatch):
        p = _ecrire(tmp_path / "c.csv", [_ligne()])

        def _refuse(*a, **k):
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "illisible")

        monkeypatch.setattr(bv.pd, "read_csv", _refuse)
        assert "Encodage illisible" in validate_brma_csv(p)["errors"][0]


class TestChampsCritiques:
    def test_artiste_vide_est_bloquant(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(artist="")])
        rapport = validate_brma_csv(p)
        assert rapport["empty_critical"] == 1
        assert rapport["ok"] is False

    def test_titre_vide_n_est_pas_bloquant(self, tmp_path):
        """Une compilation porte son nom dans `artist` et n'a pas de titre :
        c'est une particularité d'Ultratop, pas une anomalie."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(title="", date=_iso())])
        rapport = validate_brma_csv(p)
        assert rapport["empty_titles"] == 1
        assert rapport["ok"] is True

    def test_niveau_vide_n_est_pas_bloquant(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl="", date=_iso())])
        rapport = validate_brma_csv(p)
        assert rapport["empty_levels"] == 1
        assert rapport["ok"] is True


class TestReferentiel:
    @pytest.mark.parametrize("niveau", ["Or", "platine", "Double Platine", "Diamant"])
    def test_niveaux_valides(self, tmp_path, niveau):
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl=niveau)])
        assert validate_brma_csv(p)["invalid_levels"] == []

    @pytest.mark.parametrize("niveau", ["2x Platine", "12x Platine", "3 x Or", "2x Diamant"])
    def test_multiplicateurs_reconnus(self, tmp_path, niveau):
        """Ultratop note les paliers élevés en multiplicateur : ce sont des
        niveaux VALIDES, pas des valeurs hors référentiel."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl=niveau)])
        assert validate_brma_csv(p)["invalid_levels"] == []

    def test_niveau_inconnu(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl="Argent")])
        rapport = validate_brma_csv(p)
        assert rapport["invalid_levels"] == ["Argent"]
        assert rapport["ok"] is False

    def test_multiplicateur_sur_un_niveau_inconnu(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl="2x Argent")])
        assert validate_brma_csv(p)["invalid_levels"] == ["2x Argent"]

    def test_categorie_inconnue(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(cat="mixtapes")])
        rapport = validate_brma_csv(p)
        assert rapport["invalid_categories"] == ["mixtapes"]
        assert rapport["ok"] is False

    def test_casse_indifferente(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(cat="SINGLES", lvl="OR", date=_iso())])
        rapport = validate_brma_csv(p)
        assert rapport["invalid_categories"] == []
        assert rapport["invalid_levels"] == []


class TestDoublons:
    def test_doublon_exact(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(), _ligne()])
        rapport = validate_brma_csv(p)
        assert rapport["stats"]["duplicates"] == 1
        assert len(rapport["duplicates"]) == 1
        assert rapport["ok"] is False

    def test_meme_titre_en_single_et_en_album(self, tmp_path):
        """Écart ASSUMÉ avec le validateur SNEP : la catégorie fait partie de la
        clé BRMA. Un même nom certifié en single ET en album (cas Amy Macdonald
        « This Is The Life ») n'est pas un doublon."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(cat="singles"), _ligne(cat="albums")])
        assert validate_brma_csv(p)["stats"]["duplicates"] == 0

    def test_paliers_differents_conserves(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl="Or"), _ligne(lvl="Platine")])
        assert validate_brma_csv(p)["stats"]["duplicates"] == 0

    def test_artiste_vide_exclu(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(artist=""), _ligne(artist="")])
        assert validate_brma_csv(p)["stats"]["duplicates"] == 0

    def test_exemples_plafonnes(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne() for _ in range(20)])
        rapport = validate_brma_csv(p)
        assert rapport["stats"]["duplicates"] == 19
        assert len(rapport["duplicates"]) == 12


class TestDates:
    def test_plage_couverte(self, tmp_path):
        p = _ecrire(
            tmp_path / "c.csv",
            [_ligne(date="2019-05-01"), _ligne(title="B", date="2021-03-15")],
        )
        rapport = validate_brma_csv(p)
        assert rapport["date_range"] == "01/05/2019 → 15/03/2021"
        assert rapport["latest_date"] == "15/03/2021"

    def test_date_illisible(self, tmp_path):
        """Le format BRMA est ISO : une date au format français est illisible."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(date="01/05/2019"), _ligne(title="B")])
        assert validate_brma_csv(p)["date_parse_failures"] == 1

    def test_base_en_retard(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(date=_iso(90))])
        rapport = validate_brma_csv(p)
        assert any("possiblement en retard" in w for w in rapport["warnings"])

    def test_base_a_jour(self, tmp_path):
        """Seuil BRMA à 45 jours : Ultratop publie moins souvent que la SNEP."""
        p = _ecrire(tmp_path / "c.csv", [_ligne(date=_iso(30))])
        assert not any("en retard" in w for w in validate_brma_csv(p)["warnings"])

    def test_aucune_date_valide(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(date="")])
        rapport = validate_brma_csv(p)
        assert rapport["date_range"] is None
        assert rapport["per_year"] == {}


class TestCouvertureTemporelle:
    def _annee(self, annee, nombre, mois=1):
        return [_ligne(title=f"T{annee}_{i}", date=f"{annee}-{mois:02d}-15") for i in range(nombre)]

    def test_comptes_par_annee(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", self._annee(2018, 2) + self._annee(2020, 3))
        assert validate_brma_csv(p)["per_year"] == {2018: 2, 2019: 0, 2020: 3}

    def test_annee_absente_signalee_aux_bords(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", self._annee(2018, 2) + self._annee(2022, 2))
        assert validate_brma_csv(p)["missing_years"] == [2019, 2021]

    def test_annee_peu_active_non_scannee(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", self._annee(2020, 5))
        assert validate_brma_csv(p)["month_gaps"] == []

    def test_annee_active_scannee(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", self._annee(2020, 30))
        assert validate_brma_csv(p)["month_gaps"] == [f"2020-{m:02d}" for m in range(2, 13)]

    def test_mois_futurs_ignores(self, tmp_path):
        """On ne reproche pas à Ultratop de n'avoir pas encore certifié demain.

        L'année en cours est rendue « active » (12 certifs en janvier) pour
        qu'elle soit scannée : seuls les mois DÉJÀ ÉCOULÉS doivent manquer.
        """
        annee = datetime.now().year
        p = _ecrire(tmp_path / "c.csv", self._annee(annee, 30))
        gaps = validate_brma_csv(p)["month_gaps"]
        mois_signales = [int(g.split("-")[1]) for g in gaps]
        assert mois_signales  # au moins un mois écoulé sans certif
        assert max(mois_signales) <= datetime.now().month

    def test_mois_faibles(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", self._annee(2020, 2, mois=3) + self._annee(2020, 5, mois=4))
        rapport = validate_brma_csv(p, recent_years=(2020,))
        assert rapport["low_months"] == ["2020-03 (2)"]
        assert rapport["stats"]["count_2020"] == 7


class TestRapport:
    def test_erreur_court_circuite(self, tmp_path):
        texte = format_report(validate_brma_csv(tmp_path / "fantome.csv"))
        assert "introuvable" in texte
        assert "Verdict" not in texte

    def test_verdict(self, csv_sain, tmp_path):
        assert "RAS" in format_report(validate_brma_csv(csv_sain))
        p = _ecrire(tmp_path / "c.csv", [_ligne(), _ligne()])
        assert "anomalies" in format_report(validate_brma_csv(p))

    def test_sections(self, tmp_path):
        p = _ecrire(
            tmp_path / "c.csv",
            [
                _ligne(artist="", title="", lvl="", cat="mixtapes"),
                _ligne(lvl="Argent", date="pas-une-date"),
                _ligne(),
                _ligne(),
            ],
        )
        texte = format_report(validate_brma_csv(p))
        for attendu in (
            "Artiste vide",
            "Niveau de certification vide",
            "Dates illisibles",
            "Doublons exacts",
            "Catégories hors référentiel",
            "Niveaux hors référentiel",
        ):
            assert attendu in texte, attendu

    def test_sections_temporelles(self, tmp_path):
        lignes = [_ligne(title=f"T{i}", date=f"2020-01-{i + 1:02d}") for i in range(30)]
        lignes += [_ligne(title="vieux", date="2018-06-15")]
        texte = format_report(validate_brma_csv(_ecrire(tmp_path / "c.csv", lignes)))
        assert "Années ENTIÈREMENT absentes" in texte
        assert "Mois SANS certification" in texte
        assert "Comptes par année" in texte

    def test_troncature_des_longues_listes(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(lvl=f"Niveau{i}") for i in range(20)])
        assert "autre(s)" in format_report(validate_brma_csv(p))

    def test_mois_faibles_affiches(self, tmp_path):
        p = _ecrire(tmp_path / "c.csv", [_ligne(date="2020-03-01")])
        texte = format_report(validate_brma_csv(p, recent_years=(2020,)))
        assert "Mois à faible couverture" in texte

    def test_tableau_par_annee_se_replie(self, tmp_path):
        lignes = [
            _ligne(title="vieux", date="2000-06-15"),
            _ligne(title="recent", date="2020-06-15"),
        ]
        bloc = format_report(validate_brma_csv(_ecrire(tmp_path / "c.csv", lignes)))
        bloc = bloc.split("Comptes par année")[1]
        assert len([ln for ln in bloc.splitlines() if ":" in ln]) > 1


class TestCLI:
    def test_chemin_par_defaut(self):
        chemin = bv._default_csv_path()
        assert chemin.name == "certif_brma.csv"
        assert chemin.parts[-3:-1] == ("certifications", "brma")

    def test_main_avec_chemin(self, csv_sain, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["brma_validator", str(csv_sain)])
        bv.main()
        assert "VALIDATION DU CSV BRMA" in capsys.readouterr().out

    def test_main_sans_argument(self, monkeypatch, capsys):
        vu = {}
        monkeypatch.setattr(bv, "validate_brma_csv", lambda p: vu.setdefault("path", p) or {})
        monkeypatch.setattr(bv, "format_report", lambda r: "stub")
        monkeypatch.setattr(sys, "argv", ["brma_validator"])
        bv.main()
        capsys.readouterr()
        assert vu["path"].name == "certif_brma.csv"
