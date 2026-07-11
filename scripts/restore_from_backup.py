"""
Script de restauration depuis un backup
Permet de restaurer la base de données depuis un backup
"""

import logging

from src.utils.database_backup import get_backup_manager

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def main():
    """Programme principal de restauration"""
    manager = get_backup_manager()

    print("\n" + "=" * 60)
    print("   RESTAURATION DE LA BASE DE DONNÉES")
    print("=" * 60 + "\n")

    # Lister les backups disponibles
    backups = manager.list_backups()

    if not backups:
        print("❌ Aucun backup disponible")
        print(f"📁 Répertoire de backups: {manager.backup_dir}")
        return

    print(f"📋 {len(backups)} backup(s) disponible(s):\n")

    for i, backup in enumerate(backups, 1):
        print(f"{i}. {backup['name']}")
        print(f"   📅 Date: {backup['created'].strftime('%d/%m/%Y %H:%M:%S')}")
        print(f"   📦 Taille: {backup['size_mb']:.2f} MB")
        print(f"   🔧 Opération: {backup['operation']}")
        print()

    # Demander quel backup restaurer
    print("=" * 60)
    choice = input("\nNuméro du backup à restaurer (ou 'q' pour quitter): ").strip()

    if choice.lower() == "q":
        print("Annulé")
        return

    try:
        index = int(choice) - 1
        if index < 0 or index >= len(backups):
            print("❌ Numéro invalide")
            return

        selected_backup = backups[index]

        # Confirmation
        print("\n⚠️  ATTENTION: Cette opération va remplacer la base de données actuelle")
        print(f"📄 Backup sélectionné: {selected_backup['name']}")
        print(f"📅 Créé le: {selected_backup['created'].strftime('%d/%m/%Y %H:%M:%S')}")

        confirm = input("\nÊtes-vous sûr ? (oui/non): ").strip().lower()

        if confirm not in ["oui", "o", "yes", "y"]:
            print("Annulé")
            return

        # Restaurer
        print("\n🔄 Restauration en cours...")
        success = manager.restore_backup(selected_backup["path"])

        if success:
            print("\n✅ Restauration terminée avec succès!")
            print("💡 Un backup de sécurité de l'ancienne base a été créé")
            print("   (fichier .db.before_restore)")
        else:
            print("\n❌ Échec de la restauration")
            print("💡 Consultez les logs pour plus de détails")

    except ValueError:
        print("❌ Veuillez entrer un numéro valide")
    except Exception as e:
        print(f"❌ Erreur: {e}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrompu par l'utilisateur")
    except Exception as e:
        logger.error(f"Erreur: {e}", exc_info=True)
