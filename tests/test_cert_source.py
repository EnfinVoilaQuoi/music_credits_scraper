"""Tests du protocole CertificationSource + fraîcheur normalisée (phase E7f).

`read_freshness` est PUR (lecture de fichiers) : on l'exerce sur des sidecars
temporaires (formats SNEP/RIAA modernes ET ancien format BRMA sans `updates`).
Les 3 adaptateurs sont vérifiés conformes au Protocol (name, capabilities, API).
"""

import json

from src.enrichment.base import Capability
from src.enrichment.cert_source import (
    CertificationSource,
    all_certification_sources,
    read_freshness,
)


def _write(dir_path, meta: dict | None, clean: bool):
    meta_path = dir_path / "metadata.json"
    clean_path = dir_path / "certif.csv"
    if meta is not None:
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    if clean:
        clean_path.write_text("artist,title\n", encoding="utf-8")
    return meta_path, clean_path


class TestReadFreshness:
    def test_updates_moderne_globale_et_artiste(self, tmp_path):
        meta, clean = _write(
            tmp_path,
            {
                "last_update": "2026-07-13T11:00:00",
                "count": 100,
                "updates": {
                    "MIGRATION": "2026-07-12T10:00:00",
                    "GLOBAL": "2026-07-13T11:00:00",
                    "ARTIST": "2026-07-14T09:00:00",
                },
            },
            clean=True,
        )
        fresh = read_freshness(meta, clean)
        assert fresh["available"] is True
        # MàJ NON-artiste la plus récente (GLOBAL > MIGRATION), pas ARTIST.
        assert fresh["last_global"] == "2026-07-13T11:00:00"
        assert fresh["last_artist"] == "2026-07-14T09:00:00"
        assert fresh["count"] == 100

    def test_ancien_format_sans_updates(self, tmp_path):
        # BRMA legacy : pas de `updates`, `total_records` au lieu de `count`.
        meta, clean = _write(
            tmp_path,
            {"last_update": "2026-06-29T18:00:00", "total_records": 6006},
            clean=True,
        )
        fresh = read_freshness(meta, clean)
        assert fresh["last_global"] == "2026-06-29T18:00:00"
        assert fresh["last_artist"] is None
        assert fresh["count"] == 6006

    def test_recherche_artiste_ne_passe_pas_pour_globale(self, tmp_path):
        # Seule une récup ARTIST tracée → pas de MàJ globale (fix JOURNAL).
        meta, clean = _write(
            tmp_path,
            {"last_update": "x", "last_source": "ARTIST", "updates": {"ARTIST": "2026-07-14"}},
            clean=True,
        )
        fresh = read_freshness(meta, clean)
        assert fresh["last_global"] is None
        assert fresh["last_artist"] == "2026-07-14"

    def test_clean_absent_indisponible(self, tmp_path):
        meta, clean = _write(tmp_path, {"last_update": "2026-01-01"}, clean=False)
        fresh = read_freshness(meta, clean)
        assert fresh["available"] is False
        assert fresh["last_global"] == "2026-01-01"

    def test_meta_absent_tout_none(self, tmp_path):
        _, clean = _write(tmp_path, meta=None, clean=True)
        fresh = read_freshness(tmp_path / "metadata.json", clean)
        assert fresh["available"] is True
        assert fresh["last_global"] is None
        assert fresh["count"] is None


class TestAdapters:
    def test_les_trois_sources_conformes_au_protocole(self):
        sources = all_certification_sources()
        assert {s.name for s in sources} == {"SNEP", "BRMA", "RIAA"}
        for s in sources:
            assert isinstance(s, CertificationSource)
            assert s.capabilities == {Capability.CERTS}
            assert s.clean_path.name.endswith(".csv")
            s.close()  # no-op, ne lève pas

    def test_freshness_renvoie_le_contrat(self):
        # Sur la vraie arbo data/ : au minimum la forme du dict est respectée.
        for s in all_certification_sources():
            fresh = s.freshness()
            assert set(fresh) == {"available", "last_global", "last_artist", "count"}
