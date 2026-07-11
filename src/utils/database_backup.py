"""
Module de backup automatique de la base de données
Crée des backups avant les opérations critiques
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from src.config import DATA_DIR, DATABASE_URL

logger = logging.getLogger(__name__)

# Chemins par défaut alignés sur config.py — l'ancien défaut relatif
# ("music_credits.db" depuis le cwd) ne pointait sur rien : le backup
# pré-fetch de retrieval.py retournait None en silence (AUDIT.md §4).
_DEFAULT_DB_PATH = DATABASE_URL.replace("sqlite:///", "")
_DEFAULT_BACKUP_DIR = DATA_DIR / "backups"


class DatabaseBackupManager:
    """Gestionnaire de backups de la base de données"""

    def __init__(self, db_path: str = _DEFAULT_DB_PATH, backup_dir: str = _DEFAULT_BACKUP_DIR):
        self.db_path = Path(db_path)
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _sqlite_copy(src: Path, dest: Path) -> None:
        """
        Copie une base SQLite via l'API de backup native (sqlite3.Connection.backup) :
        cohérente même si la base source est en cours d'écriture, contrairement
        à une copie de fichier (shutil) qui peut capturer un état intermédiaire.
        """
        src_conn = sqlite3.connect(src)
        dest_conn = sqlite3.connect(dest)
        try:
            with dest_conn:
                src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
            src_conn.close()

    def create_backup(self, operation_name: str = "manual") -> Path | None:
        """
        Crée un backup de la base de données

        Args:
            operation_name: Nom de l'opération (ex: "before_fetch_tracks")

        Returns:
            Path du fichier de backup créé ou None en cas d'erreur
        """
        try:
            if not self.db_path.exists():
                logger.warning(f"Base de données {self.db_path} n'existe pas encore")
                return None

            # Nom du backup avec timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"backup_{operation_name}_{timestamp}.db"
            backup_path = self.backup_dir / backup_name

            # Copier la base de données (API backup SQLite — safe à chaud)
            self._sqlite_copy(self.db_path, backup_path)

            # Vérifier l'intégrité du backup
            if self._verify_backup(backup_path):
                logger.info(f"✅ Backup créé avec succès: {backup_path}")

                # Nettoyer les anciens backups (garder les 10 derniers)
                self._cleanup_old_backups(keep=10)

                return backup_path
            else:
                logger.error(f"❌ Backup corrompu: {backup_path}")
                backup_path.unlink()
                return None

        except Exception as e:
            logger.error(f"❌ Erreur lors de la création du backup: {e}")
            return None

    def _verify_backup(self, backup_path: Path) -> bool:
        """Vérifie l'intégrité d'un backup"""
        try:
            conn = sqlite3.connect(backup_path)
            cursor = conn.cursor()

            # Vérifier l'intégrité globale de la base
            cursor.execute("PRAGMA integrity_check")
            integrity = cursor.fetchone()[0]

            if integrity != "ok":
                logger.warning(f"Intégrité compromise: {integrity}")
                conn.close()
                return False

            # Vérifier que les tables principales existent
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

            # Au minimum, on doit avoir la table artists
            # (tracks et credits peuvent être vides au début)
            has_artists_table = "artists" in tables

            if not has_artists_table:
                logger.debug("Table 'artists' manquante dans le backup")

            conn.close()

            # Accepter le backup même s'il est vide (nouvelle installation)
            return len(tables) > 0

        except Exception as e:
            logger.error(f"Erreur vérification backup: {e}")
            return False

    def _cleanup_old_backups(self, keep: int = 10):
        """Nettoie les anciens backups en ne gardant que les N derniers"""
        try:
            backups = sorted(self.backup_dir.glob("backup_*.db"), key=lambda p: p.stat().st_mtime)

            # Supprimer les plus anciens si on en a trop
            if len(backups) > keep:
                for backup in backups[:-keep]:
                    backup.unlink()
                    logger.debug(f"Ancien backup supprimé: {backup.name}")

        except Exception as e:
            logger.error(f"Erreur nettoyage backups: {e}")

    def restore_backup(self, backup_path: Path) -> bool:
        """
        Restaure un backup

        Args:
            backup_path: Chemin du backup à restaurer

        Returns:
            True si la restauration a réussi
        """
        try:
            if not backup_path.exists():
                logger.error(f"Backup introuvable: {backup_path}")
                return False

            # Créer un backup de sécurité de la base actuelle
            if self.db_path.exists():
                safety_backup = self.db_path.with_suffix(".db.before_restore")
                self._sqlite_copy(self.db_path, safety_backup)
                logger.info(f"Backup de sécurité créé: {safety_backup}")

            # Restaurer le backup (API backup SQLite, y compris si la base cible est ouverte)
            self._sqlite_copy(backup_path, self.db_path)

            # Vérifier l'intégrité
            if self._verify_backup(self.db_path):
                logger.info(f"✅ Backup restauré avec succès depuis: {backup_path}")
                return True
            else:
                logger.error("❌ Base restaurée corrompue")
                return False

        except Exception as e:
            logger.error(f"❌ Erreur lors de la restauration: {e}")
            return False

    def list_backups(self) -> list:
        """Liste tous les backups disponibles"""
        try:
            backups = sorted(
                self.backup_dir.glob("backup_*.db"), key=lambda p: p.stat().st_mtime, reverse=True
            )

            backup_info = []
            for backup in backups:
                stat = backup.stat()
                backup_info.append(
                    {
                        "path": backup,
                        "name": backup.name,
                        "size_mb": stat.st_size / (1024 * 1024),
                        "created": datetime.fromtimestamp(stat.st_mtime),
                        "operation": backup.stem.replace("backup_", "").rsplit("_", 2)[0],
                    }
                )

            return backup_info

        except Exception as e:
            logger.error(f"Erreur listage backups: {e}")
            return []

    def get_backup_stats(self) -> dict:
        """Statistiques sur les backups"""
        try:
            backups = list(self.backup_dir.glob("backup_*.db"))
            total_size = sum(b.stat().st_size for b in backups)

            return {
                "count": len(backups),
                "total_size_mb": total_size / (1024 * 1024),
                "backup_dir": str(self.backup_dir),
                "latest": max(backups, key=lambda p: p.stat().st_mtime).name if backups else None,
            }

        except Exception as e:
            logger.error(f"Erreur stats backups: {e}")
            return {}


# Instance globale
_backup_manager = None


def get_backup_manager() -> DatabaseBackupManager:
    """Retourne l'instance globale du backup manager"""
    global _backup_manager
    if _backup_manager is None:
        _backup_manager = DatabaseBackupManager()
    return _backup_manager


if __name__ == "__main__":
    # Test du système de backup
    logging.basicConfig(level=logging.INFO)

    manager = DatabaseBackupManager()

    print("\n=== Test du système de backup ===\n")

    # Créer un backup de test
    backup_path = manager.create_backup("test")
    if backup_path:
        print(f"✅ Backup créé: {backup_path}")

    # Lister les backups
    print("\n📋 Backups disponibles:")
    for backup in manager.list_backups():
        print(f"  - {backup['name']}")
        print(f"    Taille: {backup['size_mb']:.2f} MB")
        print(f"    Créé: {backup['created']}")
        print(f"    Opération: {backup['operation']}")
        print()

    # Stats
    stats = manager.get_backup_stats()
    print("\n📊 Statistiques:")
    print(f"  Nombre de backups: {stats['count']}")
    print(f"  Taille totale: {stats['total_size_mb']:.2f} MB")
    print(f"  Dernier backup: {stats['latest']}")
