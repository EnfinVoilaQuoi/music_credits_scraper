"""Tests du modèle brut+clean BRMA (brma_raw.csv accumulé → certif_brma.csv dérivé).

Vérifie que le brut accumule par dédup EXACTE (aucune perte) et que le clean en
est dérivé par la dédup métier + tri de `_dedup_df`.
"""

import pandas as pd

from src.utils.update_brma import UltratopUpdater


def _updater(tmp_path) -> UltratopUpdater:
    return UltratopUpdater(
        database_path=str(tmp_path / "certif_brma.csv"), output_dir=str(tmp_path)
    )


def _cert(artist, title, level="Or", date="2020-01-01", category="single"):
    return {
        "artist": artist,
        "title": title,
        "category": category,
        "certification_level": level,
        "certification_date": date,
    }


class TestBrmaRaw:
    def test_load_raw_seeds_from_clean(self, tmp_path):
        # Pas de brma_raw.csv → le brut est seedé depuis le clean existant.
        db = tmp_path / "certif_brma.csv"
        pd.DataFrame([_cert("A", "T")]).to_csv(db, index=False, encoding="utf-8-sig")
        u = _updater(tmp_path)
        raw = u._load_raw()
        assert len(raw) == 1
        assert raw.iloc[0]["artist"] == "A"

    def test_clean_from_dedup_et_tri(self, tmp_path):
        u = _updater(tmp_path)
        raw = pd.DataFrame(
            [
                _cert("A", "T"),
                _cert("A", "T"),  # doublon exact
                _cert("A", "T", level=""),  # niveau vide → collapsé (contrepartie renseignée)
                _cert("B", "U", level="Platine", date="2021-01-01"),
            ]
        )
        clean = u._clean_from(raw)
        assert len(clean) == 2
        assert list(clean["artist"]) == ["B", "A"]  # tri date décroissante

    def test_niveaux_distincts_conserves(self, tmp_path):
        # Règle JOURNAL : ne jamais supprimer un palier réel.
        u = _updater(tmp_path)
        raw = pd.DataFrame(
            [
                _cert("A", "T", level="Platine", date="2020-01-01"),
                _cert("A", "T", level="Double Platine", date="2021-01-01"),
            ]
        )
        clean = u._clean_from(raw)
        assert set(clean["certification_level"]) == {"Platine", "Double Platine"}

    def test_save_accumule_brut_et_derive_clean(self, tmp_path):
        u = _updater(tmp_path)
        u.save_updated_database(
            [_cert("A", "T"), _cert("A", "T"), _cert("B", "U", date="2021-01-01")]
        )
        raw = pd.read_csv(tmp_path / "brma_raw.csv")
        clean = pd.read_csv(tmp_path / "certif_brma.csv")
        assert len(raw) == 2  # brut : dédup EXACTE (les 2 A/T identiques → 1)
        assert len(clean) == 2  # clean : 2 certifs distinctes

    def test_metadata_horodatee_meme_sans_nouveaute(self, tmp_path):
        # Fraîcheur = dernière VÉRIFICATION : un run sans nouvelle certif doit
        # quand même rafraîchir metadata.json (sinon la GUI affiche une MàJ périmée).
        import json

        u = _updater(tmp_path)
        u.save_updated_database([_cert("A", "T")])  # 1er run : crée brut + metadata
        meta_path = tmp_path / "metadata.json"
        assert meta_path.exists()

        # Simuler une MàJ ancienne
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["last_update"] = "2000-01-01T00:00:00"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        # Run SANS nouvelle certif → l'horodatage est tout de même rafraîchi
        u.save_updated_database([])
        meta2 = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta2["last_update"] != "2000-01-01T00:00:00"
        assert meta2["new_records_added"] == 0
        assert meta2["total_records"] == 1
