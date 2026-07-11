"""
Script de vérification de l'état de la base de données
Affiche un résumé des données et des backups disponibles
"""

import sys
from datetime import datetime
from pathlib import Path

# Fix encodage Windows
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


import sqlite3

from src.config import DATABASE_URL
from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager

# Chemin réel de la base (data/music_credits.db), pas relatif au cwd
DB_PATH = Path(DATABASE_URL.replace("sqlite:///", ""))


def format_size(size_bytes):
    """Formate une taille en bytes vers une forme lisible"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def main():
    """Programme principal"""
    print("\n" + "=" * 60)
    print("   ÉTAT DE LA BASE DE DONNÉES")
    print("=" * 60 + "\n")

    db_path = DB_PATH

    # Vérifier l'existence de la base
    if not db_path.exists():
        print(f"❌ Base de données introuvable : {db_path}")
        print("💡 Lancez l'application pour créer la base")
        return

    # Taille de la base
    db_size = db_path.stat().st_size
    print(f"📊 Base de données : {format_size(db_size)}")
    print(f"📁 Emplacement : {db_path.absolute()}")
    print()

    # Statistiques via DataManager
    try:
        dm = DataManager()
        stats = dm.get_statistics()

        print("📈 CONTENU DE LA BASE")
        print("-" * 60)
        print(f"  🎤 Artistes : {stats['total_artists']}")
        print(f"  🎵 Morceaux : {stats['total_tracks']}")
        print(f"  🏷️  Crédits : {stats['total_credits']}")
        print()

        if stats["total_artists"] > 0:
            # Lister les artistes avec leur nombre de morceaux
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    a.name,
                    COUNT(t.id) as track_count,
                    MAX(t.updated_at) as last_update
                FROM artists a
                LEFT JOIN tracks t ON a.id = t.artist_id
                GROUP BY a.id, a.name
                ORDER BY track_count DESC
            """)

            print("👥 ARTISTES EN BASE")
            print("-" * 60)
            for i, (name, track_count, last_update) in enumerate(cursor.fetchall(), 1):
                update_date = ""
                if last_update:
                    try:
                        dt = datetime.fromisoformat(last_update.split(".")[0])
                        update_date = f" (MAJ : {dt.strftime('%d/%m/%Y %H:%M')})"
                    except Exception:
                        pass
                print(f"  {i}. {name} : {track_count} morceaux{update_date}")

            conn.close()
            print()

    except Exception as e:
        print(f"⚠️  Erreur lecture base : {e}")
        print()

    # Statistiques des backups
    try:
        backup_manager = get_backup_manager()
        backup_stats = backup_manager.get_backup_stats()
        backups = backup_manager.list_backups()

        print("💾 BACKUPS DISPONIBLES")
        print("-" * 60)

        if backup_stats["count"] == 0:
            print("  ℹ️  Aucun backup disponible")
            print("  💡 Les backups sont créés automatiquement lors de la récupération de morceaux")
        else:
            print(f"  📦 Nombre : {backup_stats['count']}")
            print(f"  📏 Taille totale : {backup_stats['total_size_mb']:.2f} MB")
            print(f"  📁 Emplacement : {backup_stats['backup_dir']}")
            print()

            print("  📋 Liste des backups :")
            for i, backup in enumerate(backups[:5], 1):  # Afficher les 5 derniers
                print(f"    {i}. {backup['name']}")
                print(f"       📅 {backup['created'].strftime('%d/%m/%Y %H:%M:%S')}")
                print(f"       📦 {backup['size_mb']:.2f} MB")
                print(f"       🔧 {backup['operation']}")
                print()

            if len(backups) > 5:
                print(f"    ... et {len(backups) - 5} autres backups")

        print()

    except Exception as e:
        print(f"⚠️  Erreur lecture backups : {e}")
        print()

    # Intégrité de la base
    print("🔍 VÉRIFICATION D'INTÉGRITÉ")
    print("-" * 60)
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Vérifier les tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        required_tables = ["artists", "tracks", "credits", "scraping_errors"]
        missing = [t for t in required_tables if t not in tables]

        if missing:
            print(f"  ⚠️  Tables manquantes : {', '.join(missing)}")
        else:
            print("  ✅ Toutes les tables sont présentes")

        # Vérifier les contraintes
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchone()[0]

        if result == "ok":
            print("  ✅ Intégrité de la base vérifiée")
        else:
            print(f"  ⚠️  Problème d'intégrité : {result}")

        conn.close()

    except Exception as e:
        print(f"  ❌ Erreur vérification : {e}")

    print()
    print("=" * 60)
    print()

    # Actions recommandées
    if backup_stats.get("count", 0) == 0:
        print("💡 RECOMMANDATION :")
        print("  Récupérez des morceaux pour créer un premier backup automatique")
    elif stats.get("total_artists", 0) > 0 and backup_stats.get("count", 0) > 0:
        print("✅ Tout semble OK !")
        print("  Vos données sont protégées par les backups automatiques")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrompu par l'utilisateur")
    except Exception as e:
        print(f"\n❌ Erreur : {e}")
        import traceback

        traceback.print_exc()
