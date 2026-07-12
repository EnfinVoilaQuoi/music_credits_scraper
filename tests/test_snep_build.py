"""Tests du builder SNEP (brut certif-.csv → certif_snep.csv canonique).

Couvre le mapping/nettoyage (canonical_rows_from_raw), la fusion accumulante
(merge_canonical) et les invariants du fichier canonique committé.
"""

from pathlib import Path

from src.config import DATA_PATH
from src.utils.cert_normalize import normalize_text
from src.utils.snep_build import (
    CANONICAL_COLUMNS,
    canonical_rows_from_raw,
    merge_canonical,
    read_canonical_csv,
    read_raw_snep_csv,
)

RAW_HEADER = (
    "Interprète;Titre;Éditeur / Distributeur;Catégorie;Certification;Date de sortie;Date de constat"
)


def _write_raw(tmp_path, *lines) -> Path:
    p = tmp_path / "certif-.csv"
    p.write_text("\n".join([RAW_HEADER, *lines]), encoding="utf-8")
    return p


class TestCanonicalRowsFromRaw:
    def test_mapping_et_dates(self, tmp_path):
        raw = _write_raw(
            tmp_path, "Jul;Bande organisée;Label X;Single;Diamant;01/09/2020;15/12/2021"
        )
        rows = canonical_rows_from_raw(read_raw_snep_csv(raw))
        assert len(rows) == 1
        r = rows[0]
        assert r["artist"] == "Jul"
        assert r["title"] == "Bande organisée"
        assert r["publisher"] == "Label X"
        assert r["category"] == "Singles"  # "Single" → "Singles"
        assert r["certification"] == "Diamant"
        assert r["release_date"] == "2020-09-01"
        assert r["certification_date"] == "2021-12-15"

    def test_espaces_normalises_et_publisher_vide(self, tmp_path):
        raw = _write_raw(tmp_path, "  Aya   Nakamura ;Djadja;;Singles;Or;;10/01/2019")
        (r,) = canonical_rows_from_raw(read_raw_snep_csv(raw))
        assert r["artist"] == "Aya Nakamura"
        assert r["title"] == "Djadja"
        assert r["publisher"] == ""
        assert r["release_date"] == ""
        assert r["certification_date"] == "2019-01-10"

    def test_niveau_inconnu_ou_vide_saute(self, tmp_path):
        raw = _write_raw(
            tmp_path,
            "X;Sans niveau;;Singles;;;",  # certification vide → sauté
            "Y;Niveau exotique;;Singles;Titane;;",  # niveau inconnu → sauté
            "Z;Ok;;Singles;Platine;;01/01/2020",  # valide
        )
        rows = canonical_rows_from_raw(read_raw_snep_csv(raw))
        assert [r["title"] for r in rows] == ["Ok"]


class TestMergeCanonical:
    def _row(self, artist, title, cert, cdate, publisher=""):
        return {
            "artist": artist,
            "title": title,
            "publisher": publisher,
            "category": "Singles",
            "certification": cert,
            "release_date": "",
            "certification_date": cdate,
        }

    def test_meme_cle_date_recente_gagne(self):
        base = [self._row("A", "T", "Or", "2020-01-01", publisher="Old")]
        new = [self._row("A", "T", "Or", "2021-01-01", publisher="New")]
        (m,) = merge_canonical(base, new)
        assert m["certification_date"] == "2021-01-01"  # date la plus récente
        assert m["publisher"] == "Old"  # première occurrence gagne pour les champs

    def test_date_plus_ancienne_ignoree(self):
        base = [self._row("A", "T", "Or", "2021-01-01")]
        new = [self._row("A", "T", "Or", "2019-01-01")]
        (m,) = merge_canonical(base, new)
        assert m["certification_date"] == "2021-01-01"

    def test_cle_differente_ajoutee(self):
        base = [self._row("A", "T1", "Or", "2020-01-01")]
        new = [self._row("A", "T2", "Or", "2020-01-01")]
        m = merge_canonical(base, new)
        assert [r["title"] for r in m] == ["T1", "T2"]

    def test_niveau_distingue_les_cles(self):
        base = [self._row("A", "T", "Or", "2020-01-01")]
        new = [self._row("A", "T", "Diamant", "2020-01-01")]
        m = merge_canonical(base, new)
        assert {r["certification"] for r in m} == {"Or", "Diamant"}


class TestFichierCanoniqueCommitte:
    """Invariants du certif_snep.csv versionné (généré par la migration)."""

    def _path(self):
        return Path(DATA_PATH) / "certifications" / "snep" / "certif_snep.csv"

    def test_colonnes_et_non_vide(self):
        p = self._path()
        if not p.exists():
            import pytest

            pytest.skip("certif_snep.csv pas encore généré (scripts/migrate_snep_to_csv.py)")
        rows = read_canonical_csv(p)
        assert rows, "certif_snep.csv vide"
        assert list(rows[0].keys()) == CANONICAL_COLUMNS

    def test_pas_de_doublon_de_cle(self):
        p = self._path()
        if not p.exists():
            import pytest

            pytest.skip("certif_snep.csv pas encore généré")
        rows = read_canonical_csv(p)
        keys = [
            (normalize_text(r["artist"]), normalize_text(r["title"]), r["certification"])
            for r in rows
        ]
        assert len(keys) == len(
            set(keys)
        ), "doublon de clé (artist_clean, title_clean, certification)"
