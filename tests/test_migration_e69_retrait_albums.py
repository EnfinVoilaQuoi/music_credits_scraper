"""e69 — la table `albums` disparaît (les projets la remplacent, e67-e68) ; le
retour en arrière la recrée vide. Garde : plus aucune requête sur `albums`
dans `src/` (les migrations exceptées)."""

import re
import sqlite3
from contextlib import closing
from pathlib import Path

from tests.test_migration_e67_projets import _base_avant, _migrer

SRC = Path(__file__).resolve().parent.parent / "src"


def _tables(db):
    with closing(sqlite3.connect(db)) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_la_table_disparait_puis_revient_vide(tmp_path):
    db = _base_avant(tmp_path)
    _migrer(db, "e69_retrait_albums")
    assert "albums" not in _tables(db) and "projets" in _tables(db)
    _migrer(db, "e68_projets_nature_streams", descendre=True)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM albums").fetchone() == (0,)


def test_plus_aucune_requete_sur_albums_dans_src():
    requete = re.compile(r"\b(FROM|INTO|UPDATE|JOIN|TABLE)\s+albums\b", re.IGNORECASE)
    fautifs = [
        f"{p.relative_to(SRC).as_posix()}:{i}"
        for p in SRC.rglob("*.py")
        # Les migrations historiques (user_version, avant Alembic) restent.
        if p.name != "legacy_migrations.py"
        for i, ligne in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if requete.search(ligne)
    ]
    assert fautifs == []
