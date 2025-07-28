"""Point d'entrée principal de l'application Music Credits Scraper"""
import sys
import os
from pathlib import Path

# Ajouter le dossier parent au path pour les imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.gui.main_window import MainWindow
from src.utils.logger import get_logger
from src.config import GENIUS_API_KEY, DEBUG


logger = get_logger(__name__)


def check_configuration():
    """Vérifie que la configuration est correcte"""
    errors = []
    
    # Vérifier les clés API
    if not GENIUS_API_KEY:
        errors.append("GENIUS_API_KEY non configurée dans le fichier .env")
    
    # Vérifier que Chrome est installé (pour Selenium)
    # Note: webdriver-manager s'occupe de télécharger le driver automatiquement
    
    if errors:
        print("Erreurs de configuration:")
        for error in errors:
            print(f"  - {error}")
        print("\nVeuillez configurer le fichier .env avec vos clés API")
        return False
    
    return True


def main():
    """Fonction principale"""
    logger.info("Démarrage de Music Credits Scraper")
    
    # Vérifier la configuration
    if not check_configuration():
        sys.exit(1)
    
    try:
        # Créer et lancer l'interface
        app = MainWindow()
        logger.info("Interface graphique lancée")
        app.run()
        
    except Exception as e:
        logger.error(f"Erreur fatale: {e}")
        if DEBUG:
            raise
        else:
            print(f"Erreur: {e}")
            sys.exit(1)
    
    logger.info("Arrêt de l'application")


if __name__ == "__main__":
    main()
