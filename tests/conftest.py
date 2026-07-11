"""Fixtures partagées.

`data_manager` : DataManager branché sur une base SQLite TEMPORAIRE (base
vierge à chaque test), avec le chargement des certifications neutralisé
(il importerait les CSV SNEP réels de data/ — lent et non hermétique).

Ces tests de comportement serviront de filet de sécurité pour la migration
SQLAlchemy 2.0 + Alembic : ils ne testent que l'API publique (save/get),
jamais le SQL.
"""

import pytest


@pytest.fixture
def data_manager(tmp_path, monkeypatch):
    import src.api.snep_certifications as snep_mod
    import src.utils.certification_enricher as ce_mod
    import src.utils.data_manager as dm_mod

    db_file = tmp_path / "test_music_credits.db"
    monkeypatch.setattr(dm_mod, "DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr(dm_mod.DataManager, "_initialize_certifications", lambda self: None)
    monkeypatch.setattr(ce_mod, "CertificationEnricher", lambda: None)
    monkeypatch.setattr(snep_mod, "get_snep_manager", lambda: None)
    return dm_mod.DataManager()
