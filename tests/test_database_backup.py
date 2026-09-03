"""Filet de sécurité DB : `DatabaseBackupManager`.

La règle projet « toujours passer par le système de backup avant une opération
destructive » (reset, merge, migration Alembic) n'était vérifiée par AUCUN test :
le module était à 0 % de couverture. Or un backup qui ne restaure pas ne se
découvre qu'au moment où on en a besoin.

Tout se joue sur des bases SQLite jetables en `tmp_path` — aucun accès à
data/music_credits.db.
"""

import os
import sqlite3

import pytest

from src.utils import database_backup as db_mod
from src.utils.database_backup import DatabaseBackupManager


def _make_db(path, artists=("Jul",)):
    """Petite base réaliste : la table `artists` est celle que vérifie le backup."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO artists (name) VALUES (?)", [(a,) for a in artists])
    conn.commit()
    conn.close()
    return path


def _read_artists(path):
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute("SELECT name FROM artists ORDER BY id")]
    finally:
        conn.close()


@pytest.fixture
def manager(tmp_path):
    db = _make_db(tmp_path / "music.db")
    return DatabaseBackupManager(str(db), str(tmp_path / "backups"))


class TestCreation:
    def test_dossier_de_backup_cree_a_l_instanciation(self, tmp_path):
        cible = tmp_path / "pas" / "encore" / "la"
        DatabaseBackupManager(str(tmp_path / "music.db"), str(cible))
        assert cible.is_dir()

    def test_backup_nominal(self, manager):
        path = manager.create_backup("before_fetch")
        assert path is not None
        assert path.exists()
        assert path.name.startswith("backup_before_fetch_")
        assert path.suffix == ".db"
        assert _read_artists(path) == ["Jul"]

    def test_operation_par_defaut(self, manager):
        assert manager.create_backup().name.startswith("backup_manual_")

    def test_le_backup_est_une_copie_independante(self, manager):
        """Une écriture sur la base source après coup ne doit pas atteindre le backup."""
        path = manager.create_backup("avant")
        conn = sqlite3.connect(manager.db_path)
        conn.execute("INSERT INTO artists (name) VALUES ('Nekfeu')")
        conn.commit()
        conn.close()
        assert _read_artists(manager.db_path) == ["Jul", "Nekfeu"]
        assert _read_artists(path) == ["Jul"]

    def test_base_absente_rend_none(self, tmp_path):
        """Régression AUDIT.md §4 : le backup pré-fetch retournait None en silence
        parce que le chemin par défaut ne pointait sur rien. Le None reste, mais
        il doit venir d'une base réellement absente."""
        mgr = DatabaseBackupManager(str(tmp_path / "jamais_creee.db"), str(tmp_path / "backups"))
        assert mgr.create_backup("x") is None

    def test_base_sans_table_refusee(self, tmp_path):
        """Un fichier SQLite vide passe l'integrity_check mais n'a rien à sauver."""
        vide = tmp_path / "vide.db"
        sqlite3.connect(vide).close()
        mgr = DatabaseBackupManager(str(vide), str(tmp_path / "backups"))
        assert mgr.create_backup("x") is None

    def test_backup_corrompu_est_supprime(self, manager, monkeypatch):
        """Si la copie produit un fichier illisible, on ne laisse pas traîner un
        backup inutilisable qui ferait croire à une sauvegarde valide."""

        def _copie_pourrie(src, dest):
            dest.write_bytes(b"ceci n'est pas une base SQLite")

        monkeypatch.setattr(DatabaseBackupManager, "_sqlite_copy", staticmethod(_copie_pourrie))
        assert manager.create_backup("x") is None
        assert list(manager.backup_dir.glob("backup_*.db")) == []

    def test_integrite_compromise(self, manager, monkeypatch):
        """Un PRAGMA integrity_check qui ne répond pas « ok » invalide le backup.

        Cas difficile à provoquer avec un vrai fichier (une corruption franche
        lève une exception au lieu de répondre), d'où la connexion factice : ce
        qu'on vérifie ici, c'est la lecture du verdict, pas SQLite.
        """

        class _Cursor:
            def execute(self, sql):
                return self

            def fetchone(self):
                return ("*** in database main *** Page 4 is never used",)

            def fetchall(self):
                return []

        class _Conn:
            def cursor(self):
                return _Cursor()

            def close(self):
                pass

        monkeypatch.setattr(db_mod.sqlite3, "connect", lambda path: _Conn())
        assert manager._verify_backup(manager.db_path) is False

    def test_erreur_de_copie_rend_none(self, manager, monkeypatch):
        def _boom(src, dest):
            raise OSError("disque plein")

        monkeypatch.setattr(DatabaseBackupManager, "_sqlite_copy", staticmethod(_boom))
        assert manager.create_backup("x") is None


class TestRotation:
    def _semer(self, manager, nombre):
        """`nombre` backups aux mtimes strictement croissants (backup_0 = le plus vieux)."""
        for i in range(nombre):
            p = manager.backup_dir / f"backup_seed{i}_2026090{i}_120000.db"
            _make_db(p)
            os.utime(p, (1_700_000_000 + i * 60, 1_700_000_000 + i * 60))

    def test_garde_les_n_plus_recents(self, manager):
        self._semer(manager, 5)
        manager._cleanup_old_backups(keep=2)
        restants = sorted(p.name for p in manager.backup_dir.glob("backup_*.db"))
        assert restants == ["backup_seed3_20260903_120000.db", "backup_seed4_20260904_120000.db"]

    def test_ne_supprime_rien_sous_le_seuil(self, manager):
        self._semer(manager, 3)
        manager._cleanup_old_backups(keep=10)
        assert len(list(manager.backup_dir.glob("backup_*.db"))) == 3

    def test_creation_plafonne_a_dix(self, manager):
        self._semer(manager, 12)
        manager.create_backup("nouveau")
        # 12 semés + 1 créé = 13, ramenés à 10 par le nettoyage automatique.
        assert len(list(manager.backup_dir.glob("backup_*.db"))) == 10

    def test_erreur_de_nettoyage_silencieuse(self, manager):
        """Un nettoyage qui échoue ne doit pas faire perdre le backup tout juste créé."""
        manager.backup_dir = _DossierCasse()
        manager._cleanup_old_backups(keep=1)  # ne lève pas


class TestRestauration:
    def test_aller_retour(self, manager):
        path = manager.create_backup("avant_merge")
        conn = sqlite3.connect(manager.db_path)
        conn.execute("DELETE FROM artists")
        conn.commit()
        conn.close()
        assert _read_artists(manager.db_path) == []

        assert manager.restore_backup(path) is True
        assert _read_artists(manager.db_path) == ["Jul"]

    def test_backup_de_securite_avant_ecrasement(self, manager):
        """La base courante est elle-même sauvée avant d'être écrasée : une
        restauration sur le mauvais backup reste rattrapable."""
        path = manager.create_backup("avant")
        conn = sqlite3.connect(manager.db_path)
        conn.execute("INSERT INTO artists (name) VALUES ('Nekfeu')")
        conn.commit()
        conn.close()

        manager.restore_backup(path)
        securite = manager.db_path.with_suffix(".db.before_restore")
        assert securite.exists()
        assert _read_artists(securite) == ["Jul", "Nekfeu"]  # l'état d'AVANT restauration

    def test_backup_introuvable(self, manager, tmp_path):
        assert manager.restore_backup(tmp_path / "fantome.db") is False

    def test_base_cible_inexistante(self, tmp_path):
        """Restaurer sur une installation neuve : pas de backup de sécurité à faire."""
        source = _make_db(tmp_path / "source.db")
        cible = tmp_path / "pas_encore.db"
        mgr = DatabaseBackupManager(str(cible), str(tmp_path / "backups"))
        assert mgr.restore_backup(source) is True
        assert _read_artists(cible) == ["Jul"]
        assert not cible.with_suffix(".db.before_restore").exists()

    def test_base_restauree_corrompue(self, manager, monkeypatch):
        path = manager.create_backup("avant")
        monkeypatch.setattr(DatabaseBackupManager, "_verify_backup", lambda self, p: False)
        assert manager.restore_backup(path) is False

    def test_erreur_de_copie(self, manager, monkeypatch):
        path = manager.create_backup("avant")
        monkeypatch.setattr(
            DatabaseBackupManager,
            "_sqlite_copy",
            staticmethod(lambda src, dest: (_ for _ in ()).throw(OSError("io"))),
        )
        assert manager.restore_backup(path) is False


class TestInventaire:
    def test_liste_du_plus_recent_au_plus_ancien(self, manager):
        for i, nom in enumerate(
            ["backup_vieux_20260901_120000.db", "backup_neuf_20260902_120000.db"]
        ):
            p = manager.backup_dir / nom
            _make_db(p)
            os.utime(p, (1_700_000_000 + i * 60, 1_700_000_000 + i * 60))

        noms = [b["name"] for b in manager.list_backups()]
        assert noms == ["backup_neuf_20260902_120000.db", "backup_vieux_20260901_120000.db"]

    def test_nom_d_operation_extrait(self, manager):
        """L'opération est ce qui reste une fois le préfixe et l'horodatage retirés —
        y compris quand elle contient elle-même des underscores."""
        manager.create_backup("before_fetch_tracks")
        assert manager.list_backups()[0]["operation"] == "before_fetch_tracks"

    def test_champs_exposes(self, manager):
        manager.create_backup("x")
        info = manager.list_backups()[0]
        assert set(info) == {"path", "name", "size_mb", "created", "operation"}
        assert info["size_mb"] > 0

    def test_liste_vide(self, manager):
        assert manager.list_backups() == []

    def test_liste_en_erreur(self, manager):
        manager.backup_dir = _DossierCasse()
        assert manager.list_backups() == []

    def test_stats(self, manager):
        manager.create_backup("un")
        manager.create_backup("deux")
        stats = manager.get_backup_stats()
        assert stats["count"] == 2
        assert stats["total_size_mb"] > 0
        assert stats["latest"].startswith("backup_")
        assert stats["backup_dir"] == str(manager.backup_dir)

    def test_stats_sans_backup(self, manager):
        stats = manager.get_backup_stats()
        assert stats["count"] == 0
        assert stats["latest"] is None

    def test_stats_en_erreur(self, manager):
        manager.backup_dir = _DossierCasse()
        assert manager.get_backup_stats() == {}


class _DossierCasse:
    """Faux dossier dont le parcours échoue (disque HS, permission refusée…)."""

    def glob(self, pattern):
        raise OSError("dossier illisible")


def test_instance_globale_partagee(tmp_path, monkeypatch):
    created = []

    class _Manager(DatabaseBackupManager):
        def __init__(self):
            super().__init__(str(tmp_path / "music.db"), str(tmp_path / "backups"))
            created.append(self)

    monkeypatch.setattr(db_mod, "DatabaseBackupManager", _Manager)
    monkeypatch.setattr(db_mod, "_backup_manager", None)

    first = db_mod.get_backup_manager()
    assert db_mod.get_backup_manager() is first
    assert len(created) == 1
