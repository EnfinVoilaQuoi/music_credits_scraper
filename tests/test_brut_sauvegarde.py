"""Copies du brut (`src/persistence/brut_sauvegarde.py`, 2026-10-20)."""

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import pytest

from src.persistence import brut, brut_sauvegarde
from src.persistence.brut_sauvegarde import GARDEES, copies, sauvegarder

T0 = datetime(2026, 10, 20, 5, 0, 0)


@pytest.fixture
def source(tmp_path):
    chemin = tmp_path / "brut.db"
    with closing(sqlite3.connect(chemin)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE brut (id INTEGER PRIMARY KEY, corps BLOB)")
        conn.execute("INSERT INTO brut (corps) VALUES (x'789c')")
        conn.commit()
    return chemin


def _lignes(chemin):
    with closing(sqlite3.connect(chemin)) as conn:
        return conn.execute("SELECT corps FROM brut").fetchall()


def test_la_copie_est_une_base_lisible_identique(source, tmp_path):
    cible = sauvegarder(source, tmp_path / "copies", maintenant=T0)

    assert cible.name == "brut_20261020_050000.db"
    assert _lignes(cible) == _lignes(source)
    assert not list((tmp_path / "copies").glob("*.part"))


def test_au_plus_une_copie_par_semaine(source, tmp_path):
    dossier = tmp_path / "copies"
    sauvegarder(source, dossier, maintenant=T0)

    assert sauvegarder(source, dossier, maintenant=T0 + timedelta(days=6)) is None
    assert sauvegarder(source, dossier, maintenant=T0 + timedelta(days=7)) is not None
    assert sauvegarder(source, dossier, maintenant=T0 + timedelta(days=7, hours=1), forcer=True)


def test_seules_les_deux_dernieres_restent(source, tmp_path):
    dossier = tmp_path / "copies"
    (dossier).mkdir()
    (dossier / "brut_illisible.db").write_bytes(b"")  # pas une copie : jamais supprimé
    for semaine in range(4):
        sauvegarder(source, dossier, maintenant=T0 + timedelta(weeks=semaine))

    restantes = [q for q, _ in copies(dossier)]
    assert len(restantes) == GARDEES == 2
    assert restantes == [T0 + timedelta(weeks=3), T0 + timedelta(weeks=2)]
    assert (dossier / "brut_illisible.db").exists()


def test_sans_place_rien_n_est_ecrit(source, tmp_path, monkeypatch):
    monkeypatch.setattr(
        brut_sauvegarde.shutil, "disk_usage", lambda _d: type("U", (), {"free": 10})()
    )
    with pytest.raises(OSError, match="espace insuffisant"):
        sauvegarder(source, tmp_path / "copies", maintenant=T0)
    assert copies(tmp_path / "copies") == []


def test_sans_brut_rien_a_copier(tmp_path):
    assert sauvegarder(tmp_path / "absent.db", tmp_path / "copies", maintenant=T0) is None


class TestFinDeRun:
    def test_sans_dossier_regle_rien_n_est_ecrit_et_le_run_le_dit(self, source, monkeypatch):
        monkeypatch.setattr(brut, "CHEMIN", source)
        ligne = brut_sauvegarde.sauvegarder_en_fin_de_run()
        assert ligne.startswith("⚠️ brut non sauvegardé")

    def test_avec_dossier_la_copie_est_faite(self, source, tmp_path, monkeypatch):
        monkeypatch.setattr(brut, "CHEMIN", source)
        monkeypatch.setattr("src.config.BRUT_BACKUP_DIR", tmp_path / "copies")

        ligne = brut_sauvegarde.sauvegarder_en_fin_de_run()

        assert ligne.startswith("💾 brut copié")
        assert len(copies(tmp_path / "copies")) == 1
        # La semaine suivante seulement : un second run ne recopie pas.
        assert brut_sauvegarde.sauvegarder_en_fin_de_run() is None

    def test_une_copie_impossible_ne_leve_pas(self, source, tmp_path, monkeypatch):
        monkeypatch.setattr(brut, "CHEMIN", source)
        monkeypatch.setattr("src.config.BRUT_BACKUP_DIR", tmp_path / "copies")
        monkeypatch.setattr(
            brut_sauvegarde.shutil, "disk_usage", lambda _d: type("U", (), {"free": 10})()
        )
        assert brut_sauvegarde.sauvegarder_en_fin_de_run().startswith("⚠️ copie du brut impossible")

    def test_pas_de_brut_rien_a_dire(self, tmp_path, monkeypatch):
        monkeypatch.setattr(brut, "CHEMIN", tmp_path / "absent.db")
        assert brut_sauvegarde.sauvegarder_en_fin_de_run() is None


def test_meme_disque():
    from pathlib import Path

    assert brut_sauvegarde.meme_disque(Path("C:/a/b"), Path("c:/x"))
    assert not brut_sauvegarde.meme_disque(Path("C:/a"), Path("D:/a"))


def test_la_copie_dit_sa_progression(source, tmp_path, monkeypatch):
    """Une ligne par tranche de pages : le journal du run bouge pendant les
    ~18 min de copie vers le disque externe (sinon le lot la croit figée)."""
    with closing(sqlite3.connect(source)) as conn:
        conn.executemany("INSERT INTO brut (corps) VALUES (?)", [(b"x" * 4000,)] * 20)
        conn.commit()
    monkeypatch.setattr(brut_sauvegarde, "_PAGES_PAR_TRANCHE", 2)
    lignes = []

    cible = sauvegarder(source, tmp_path / "copies", maintenant=T0, progres=lignes.append)

    assert len(lignes) > 2 and lignes[-1].startswith("   … brut : ")
    assert _lignes(cible) == _lignes(source)
