"""Fixtures partagées.

`data_manager` : DataManager branché sur une base SQLite TEMPORAIRE (base
vierge à chaque test), avec le chargement des certifications neutralisé
(il importerait les CSV SNEP réels de data/ — lent et non hermétique).

Ces tests de comportement serviront de filet de sécurité pour la migration
SQLAlchemy 2.0 + Alembic : ils ne testent que l'API publique (save/get),
jamais le SQL.

`load_fixture` / `load_fixture_json` : chargement des pages réelles
enregistrées dans tests/fixtures/ (capture : scripts/capture_fixtures.py).
Skip propre si la fixture n'a pas encore été capturée — la suite reste verte
sur un clone frais.
"""

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(relpath: str) -> str:
    """Contenu texte d'une fixture enregistrée (skip si absente)."""
    path = FIXTURES_DIR / relpath
    if not path.exists():
        pytest.skip(f"fixture manquante: {relpath} — lancer scripts/capture_fixtures.py")
    return path.read_text(encoding="utf-8")


def load_fixture_json(relpath: str) -> dict:
    """Fixture JSON parsée (skip si absente)."""
    return json.loads(load_fixture(relpath))


@pytest.fixture(autouse=True)
def _oracle_spotify_hors_ligne(monkeypatch):
    """AUCUN test ne parle à Spotify.

    Le garde-fou de justesse d'un `spotify_id` (2026-09-08) interroge la page
    `/embed/` quand aucun lecteur ne lui est injecté. Branché tel quel, il rendait
    la suite NON HERMÉTIQUE — exactement le défaut relevé sur le repli LLM, où un
    test partait soit en timeout de 34 s, soit en vraie inférence.

    Le lecteur rend `None`, ce qui signifie « on ne conclut pas » : un ID non
    vérifiable n'est pas un ID fautif, donc le comportement par défaut des tests
    est inchangé. Un test qui veut EXERCER le garde-fou injecte son propre
    lecteur (ou appelle `identite_concorde`, qui est pur).
    """
    monkeypatch.setattr("src.utils.spotify_identity.lire_identite_http", lambda spotify_id: None)


@pytest.fixture
def data_manager(tmp_path, monkeypatch):
    # DataManager n'importe plus les certifications au démarrage (elles vivent
    # dans certif_snep.csv) : seule la base à isoler reste à monkeypatcher.
    import src.utils.data_manager as dm_mod

    db_file = tmp_path / "test_music_credits.db"
    monkeypatch.setattr(dm_mod, "DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    return dm_mod.DataManager()
