"""Copie hors disque des sauvegardes de la base (2026-10-20)."""

import os
from datetime import datetime, timedelta

import pytest

from src.utils import sauvegardes_hors_disque as mod
from src.utils.sauvegardes_hors_disque import planifier, selection, synchroniser

T0 = datetime(2026, 10, 20, 12, 0, 0)  # un mardi


def test_la_derniere_de_chaque_jour_puis_de_chaque_semaine():
    dates = [
        T0 - timedelta(hours=1),  # 0 : aujourd'hui, la dernière
        T0 - timedelta(hours=3),  # 1 : aujourd'hui, plus ancienne → non
        T0 - timedelta(days=1),  # 2 : hier
        T0 - timedelta(days=10),  # 3 : semaine ISO 41, la dernière
        T0 - timedelta(days=11),  # 4 : même semaine → non
        T0 - timedelta(days=24),  # 5 : semaine 39 (dans les 4 semaines)
        T0 - timedelta(days=40),  # 6 : hors fenêtre → non
    ]
    assert selection(dates, T0) == {0, 2, 3, 5}


def _sauvegarde(dossier, nom, quand, taille=10):
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / nom
    chemin.write_bytes(b"x" * taille)
    os.utime(chemin, (quand.timestamp(), quand.timestamp()))
    return chemin


def test_le_dossier_externe_est_le_miroir_de_la_selection(tmp_path):
    src, dest = tmp_path / "backups", tmp_path / "D"
    _sauvegarde(src, "backup_a_1.db", T0 - timedelta(hours=1))
    _sauvegarde(src, "backup_a_2.db", T0 - timedelta(hours=2))  # pas la dernière du jour
    sortie = _sauvegarde(dest, "backup_vieille.db", T0 - timedelta(days=60))
    etranger = _sauvegarde(dest, "notes.txt", T0 - timedelta(days=60))

    plan = synchroniser(src, dest, maintenant=T0, appliquer=True)

    assert [p.name for p in plan.a_copier] == ["backup_a_1.db"]
    assert sorted(p.name for p in dest.iterdir()) == ["backup_a_1.db", "notes.txt"]
    assert not sortie.exists() and etranger.exists()
    # Date de fichier conservée : la sélection suivante la reconnaît.
    assert (dest / "backup_a_1.db").stat().st_mtime == pytest.approx(
        (T0 - timedelta(hours=1)).timestamp()
    )
    assert planifier(src, dest, T0).a_copier == []


def test_une_copie_chassee_du_disque_principal_reste_dehors(tmp_path):
    """La rotation de `data/backups` a pu la retirer : sélectionnée, elle reste."""
    src, dest = tmp_path / "backups", tmp_path / "D"
    _sauvegarde(src, "backup_neuve.db", T0 - timedelta(hours=1))
    _sauvegarde(dest, "backup_semaine.db", T0 - timedelta(days=10))

    plan = planifier(src, dest, T0)

    assert plan.a_retirer == [] and [p.name for p in plan.deja_la] == ["backup_semaine.db"]


def test_a_blanc_rien_n_est_ecrit(tmp_path):
    src, dest = tmp_path / "backups", tmp_path / "D"
    _sauvegarde(src, "backup_a.db", T0 - timedelta(hours=1))

    plan = synchroniser(src, dest, maintenant=T0)

    assert len(plan.a_copier) == 1 and not dest.exists()


def test_sans_place_rien_n_est_copie_ni_retire(tmp_path, monkeypatch):
    src, dest = tmp_path / "backups", tmp_path / "D"
    _sauvegarde(src, "backup_a.db", T0 - timedelta(hours=1))
    vieille = _sauvegarde(dest, "backup_vieille.db", T0 - timedelta(days=60))
    monkeypatch.setattr(mod.shutil, "disk_usage", lambda _d: type("U", (), {"free": 1})())

    with pytest.raises(OSError, match="espace insuffisant"):
        synchroniser(src, dest, maintenant=T0, appliquer=True)
    assert vieille.exists()


def test_fin_de_run_sans_dossier_ne_fait_rien():
    assert mod.synchroniser_en_fin_de_run() is None


def test_une_copie_longue_dit_sa_progression(tmp_path, monkeypatch):
    """Le journal du run bouge pendant la copie (sinon le lot la croit figée)."""
    monkeypatch.setattr(mod, "PAS_PROGRES", 1024 * 1024)
    src, dest = tmp_path / "backups", tmp_path / "D"
    fichier = _sauvegarde(src, "backup_a_1.db", T0 - timedelta(hours=1))
    fichier.write_bytes(b"x" * (3 * 1024 * 1024 + 10))
    os.utime(fichier, (fichier.stat().st_atime, (T0 - timedelta(hours=1)).timestamp()))
    lignes = []

    synchroniser(src, dest, maintenant=T0, appliquer=True, progres=lignes.append)

    assert len(lignes) == 3 and "3 / 3 Mo" in lignes[-1]
    assert (dest / "backup_a_1.db").read_bytes() == fichier.read_bytes()
    assert (dest / "backup_a_1.db").stat().st_mtime == pytest.approx(
        (T0 - timedelta(hours=1)).timestamp()
    )
