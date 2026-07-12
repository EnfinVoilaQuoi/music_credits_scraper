"""Tests du modèle brut+clean RIAA (riaa_raw.csv accumulé → certif_riaa.csv dérivé).

Les fonctions de update_riaa écrivent dans des fichiers module-niveau : on
monkeypatch les chemins vers tmp_path pour tester sans toucher aux vraies données.
"""

import pandas as pd
import pytest

import src.utils.update_riaa as u


@pytest.fixture
def riaa_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "_RIAA_DIR", tmp_path)
    monkeypatch.setattr(u, "CERTIF_CSV", tmp_path / "certif_riaa.csv")
    monkeypatch.setattr(u, "RIAA_RAW", tmp_path / "riaa_raw.csv")
    monkeypatch.setattr(u, "RIAA_META", tmp_path / "metadata.json")
    return tmp_path


def _row(artist="A", title="T", level="Gold", date="2020-01-01", fmt="SINGLE"):
    return {
        "Artist": artist,
        "Title": title,
        "Certification_Type": level,
        "Certification_Date": date,
        "Format_Type": fmt,
    }


def test_merge_accumule_brut_et_derive_clean(riaa_tmp):
    total, added = u._merge_certif_csv(
        [_row("A", "T"), _row("A", "T"), _row("B", "U", date="2021-01-01")]
    )
    raw = pd.read_csv(riaa_tmp / "riaa_raw.csv")
    clean = pd.read_csv(riaa_tmp / "certif_riaa.csv")
    assert len(raw) == 2  # brut : dédup EXACTE (2 A/T identiques → 1)
    assert len(clean) == 2
    assert total == 2


def test_clean_retire_les_vides(riaa_tmp):
    u._merge_certif_csv([_row("A", "T"), _row("A", "")])  # titre vide
    raw = pd.read_csv(riaa_tmp / "riaa_raw.csv").fillna("")
    clean = pd.read_csv(riaa_tmp / "certif_riaa.csv").fillna("")
    assert len(raw) == 2  # brut garde tout
    assert len(clean) == 1  # clean retire l'entrée à titre vide


def test_niveaux_distincts_conserves(riaa_tmp):
    # Règle JOURNAL : ne jamais supprimer un palier réel.
    u._merge_certif_csv(
        [
            _row("A", "T", level="Platinum", date="2020-01-01"),
            _row("A", "T", level="2x Platinum", date="2021-01-01"),
        ]
    )
    clean = pd.read_csv(riaa_tmp / "certif_riaa.csv")
    assert len(clean) == 2


def test_meta_ecrite(riaa_tmp):
    u._merge_certif_csv([_row("A", "T")])
    assert (riaa_tmp / "metadata.json").exists()


def test_clean_certif_csv_derive_du_brut(riaa_tmp):
    pd.DataFrame([_row("A", "T"), _row("A", "T")]).to_csv(
        riaa_tmp / "riaa_raw.csv", index=False, encoding="utf-8-sig"
    )
    before, after = u.clean_certif_csv()
    clean = pd.read_csv(riaa_tmp / "certif_riaa.csv")
    assert after == 1  # dédup depuis le brut
    assert len(clean) == 1
