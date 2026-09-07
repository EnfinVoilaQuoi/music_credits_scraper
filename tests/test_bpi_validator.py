"""Validateur du CSV BPI.

Ce qu'on vérifie ici, au-delà des cas nominaux : que le validateur et le
nettoyeur partagent leur RÉFÉRENTIEL. Le 2026-09-06, `riaa_validator` gardait sa
propre liste de niveaux, restée au vocabulaire américain, et déclarait
« anomalies » sur 61 awards latins parfaitement valides pendant que le nettoyeur
disait le fichier propre. Un verdict ne se calcule qu'à UN endroit.
"""

import pandas as pd
import pytest

from src.utils.bpi_validator import format_report, validate_bpi_csv

COLONNES = [
    "artist",
    "title",
    "category",
    "certification_level",
    "certification_date",
    "units",
    "format_id",
    "artist_id",
    "title_id",
]


def _ecrire(tmp_path, lignes):
    chemin = tmp_path / "certif_bpi.csv"
    pd.DataFrame(lignes, columns=COLONNES).to_csv(chemin, index=False, encoding="utf-8-sig")
    return chemin


def _ligne(**kw):
    base = {
        "artist": "A",
        "title": "T",
        "category": "Single",
        "certification_level": "Gold",
        "certification_date": "2020-01-01",
        "units": "400000",
        "format_id": "2",
        "artist_id": "10",
        "title_id": "1",
    }
    return {**base, **kw}


class TestNominal:
    def test_un_csv_sain_est_ras(self, tmp_path):
        rapport = validate_bpi_csv(_ecrire(tmp_path, [_ligne()]))
        assert rapport["ok"] is True
        assert rapport["stats"]["n_rows"] == 1
        assert "RAS" in format_report(rapport)

    def test_fichier_absent(self, tmp_path):
        rapport = validate_bpi_csv(tmp_path / "nexiste_pas.csv")
        assert rapport["errors"]
        assert "❌" in format_report(rapport)

    def test_colonne_requise_manquante(self, tmp_path):
        chemin = tmp_path / "certif_bpi.csv"
        pd.DataFrame([{"artist": "A"}]).to_csv(chemin, index=False)
        assert "Colonnes manquantes" in validate_bpi_csv(chemin)["errors"][0]


class TestReferentiels:
    def test_le_vocabulaire_bpi_entier_est_accepte(self, tmp_path):
        """Silver / Gold / Platinum / Nx Platinum — y compris le 26x du site.

        Garde-fou GÉNÉRAL plutôt que des cas un par un : c'est ce qui attrape une
        clé masquée dans une table de correspondance, là où tester chaque valeur
        connue ne montre que ce qu'on avait déjà en tête.
        """
        lignes = [
            _ligne(certification_level=lvl, title_id=str(i), units="")
            for i, lvl in enumerate(
                ["Silver", "Gold", "Platinum", "2x Platinum", "26x Platinum", "Multi-Platinum"]
            )
        ]
        assert validate_bpi_csv(_ecrire(tmp_path, lignes))["invalid_levels"] == []

    def test_un_niveau_hors_vocabulaire_est_signale(self, tmp_path):
        rapport = validate_bpi_csv(_ecrire(tmp_path, [_ligne(certification_level="Bronze")]))
        assert rapport["invalid_levels"] == ["Bronze"]
        assert rapport["ok"] is False

    def test_4x_gold_n_existe_pas(self, tmp_path):
        """Seul le platine se multiplie — et un commentaire n'est pas une
        contrainte : `riaa_units("4x Gold")` rendait 2 000 000 alors que le
        module disait déjà que ce palier n'existe pas."""
        assert validate_bpi_csv(_ecrire(tmp_path, [_ligne(certification_level="4x Gold")]))[
            "invalid_levels"
        ] == ["4x Gold"]

    def test_une_categorie_inconnue_est_signalee(self, tmp_path):
        rapport = validate_bpi_csv(_ecrire(tmp_path, [_ligne(category="Cassette")]))
        assert rapport["invalid_categories"] == ["Cassette"]

    @pytest.mark.parametrize(
        "cat,unites", [("Album", "100000"), ("Single", "400000"), ("Music DVDs", "25000")]
    )
    def test_les_unites_dependent_du_format(self, tmp_path, cat, unites):
        rapport = validate_bpi_csv(_ecrire(tmp_path, [_ligne(category=cat, units=unites)]))
        assert rapport["units_mismatch"] == []

    def test_des_unites_perimees_sont_signalees(self, tmp_path):
        """Un Gold d'album à 400 000, c'est le barème du SINGLE : la ligne a été
        écrite avec le mauvais format, et le CSV le traînerait en silence."""
        rapport = validate_bpi_csv(_ecrire(tmp_path, [_ligne(category="Album", units="400000")]))
        assert rapport["units_mismatch"]
        assert rapport["ok"] is False


class TestDoublons:
    def test_meme_identite_meme_palier_meme_date(self, tmp_path):
        rapport = validate_bpi_csv(_ecrire(tmp_path, [_ligne(), _ligne()]))
        assert rapport["stats"]["duplicates"] == 1
        assert rapport["ok"] is False

    def test_paliers_differents_ne_sont_PAS_des_doublons(self, tmp_path):
        """La dédup est additive : c'est l'historique d'un titre réhaussé."""
        lignes = [
            _ligne(certification_level="Gold", certification_date="2015-12-04", units=""),
            _ligne(certification_level="Platinum", certification_date="2016-06-24", units=""),
            _ligne(certification_level="2x Platinum", certification_date="2026-08-28", units=""),
        ]
        assert validate_bpi_csv(_ecrire(tmp_path, lignes))["stats"]["duplicates"] == 0

    def test_lignes_sans_identite_de_source(self, tmp_path):
        rapport = validate_bpi_csv(
            _ecrire(tmp_path, [_ligne(format_id="0", artist_id="0", title_id="0")])
        )
        assert rapport["sans_identite"] == 1
        assert "sans identité de source" in format_report(rapport)


class TestCouverture:
    def test_le_rapport_avertit_sur_les_trous_anciens(self, tmp_path):
        """Chez la BPI, un mois vide n'est pas forcément une lacune.

        Le rapport DOIT le dire : la fenêtre de dates du site ne rend que la
        dernière certification, donc les titres réhaussés ont quitté leur mois
        d'origine. Sans cet avertissement, l'utilisateur relancerait des
        rescrapes mensuels qui ne rapporteraient jamais rien.
        """
        # Une année assez dense pour que les trous mensuels soient calculés,
        # avec janvier laissé vide.
        lignes = [
            _ligne(certification_date=f"2020-{m:02d}-15", title_id=str(i), units="")
            for i in range(40)
            for m in [(i % 11) + 2]
        ]
        rapport = validate_bpi_csv(_ecrire(tmp_path, lignes))
        assert rapport["month_gaps"] == ["2020-01"]
        texte = format_report(rapport)
        assert "DERNIÈRE certification" in texte
        assert "--full" in texte
