"""Script de mise à jour automatique des certifications SNEP"""
import sys
import requests
from pathlib import Path
from datetime import datetime
import shutil
sys.path.append(str(Path(__file__).parent.parent.parent))

from src.api.snep_certifications import SNEPCertificationManager
from src.utils.logger import get_logger
from src.config import DATA_PATH


logger = get_logger(__name__)


def download_latest_snep_csv():
    """Télécharge la dernière version du CSV depuis le site SNEP"""
    print("=" * 60)
    print("MISE À JOUR DES CERTIFICATIONS SNEP")
    print("=" * 60)
    
    # Construire l'URL dynamiquement basée sur la date actuelle
    # Format observé : https://snepmusique.com/wp-content/uploads/YYYY/MM/certif-.csv
    current_date = datetime.now()
    year = current_date.year
    month = f"{current_date.month:02d}"
    
    # Essayer plusieurs URLs possibles (mois actuel et précédent)
    urls_to_try = [
        f"https://snepmusique.com/wp-content/uploads/{year}/{month}/certif-.csv",
        f"https://snepmusique.com/wp-content/uploads/{year}/{int(month)-1:02d}/certif-.csv" if int(month) > 1 else None,
        # URL de fallback si le pattern change
        "https://snepmusique.com/wp-content/uploads/certif-.csv"
    ]
    
    # Chemin de destination
    dest_dir = Path(DATA_PATH) / 'certifications' / 'snep'
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / 'certif-.csv'
    
    # Backup du fichier existant si présent
    if dest_path.exists():
        backup_path = dest_dir / f'certif-backup-{datetime.now():%Y%m%d_%H%M%S}.csv'
        shutil.copy2(dest_path, backup_path)
        print(f"✅ Backup créé : {backup_path.name}")
    
    # Essayer de télécharger le fichier
    for url in urls_to_try:
        if url is None:
            continue
            
        print(f"\n🔍 Tentative de téléchargement depuis :")
        print(f"   {url}")
        
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://snepmusique.com/les-certifications/',
                'Accept': 'text/csv,application/csv,text/plain,*/*'
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 200:
                # Vérifier que c'est bien un CSV
                content_type = response.headers.get('Content-Type', '')
                if 'csv' in content_type.lower() or 'text' in content_type.lower():
                    # Sauvegarder le fichier
                    with open(dest_path, 'wb') as f:
                        f.write(response.content)
                    
                    # Vérifier la taille du fichier
                    file_size = dest_path.stat().st_size
                    print(f"✅ Fichier téléchargé avec succès !")
                    print(f"   Taille : {file_size / 1024:.1f} KB")
                    
                    return dest_path
                else:
                    print(f"⚠️ Le contenu ne semble pas être un CSV (Type: {content_type})")
            else:
                print(f"❌ Erreur HTTP {response.status_code}")
                
        except requests.exceptions.RequestException as e:
            print(f"❌ Erreur de connexion : {e}")
        except Exception as e:
            print(f"❌ Erreur inattendue : {e}")
    
    print("\n⚠️ Impossible de télécharger le fichier CSV")
    print("   Le fichier existant sera utilisé si disponible")
    
    if dest_path.exists():
        return dest_path
    return None


def update_snep_database():
    """Met à jour la base de données avec le dernier CSV"""
    # Télécharger le dernier CSV
    csv_path = download_latest_snep_csv()
    
    if not csv_path:
        print("❌ Impossible de mettre à jour : pas de fichier CSV disponible")
        return False
    
    print("\n📥 Import dans la base de données...")
    
    # Initialiser le manager
    manager = SNEPCertificationManager()
    
    # Obtenir les stats avant mise à jour
    stats_before = manager.get_certification_stats()
    total_before = stats_before['total_certifications']
    
    # Importer les données
    success = manager.import_from_csv(csv_path)
    
    if success:
        # Obtenir les stats après mise à jour
        stats_after = manager.get_certification_stats()
        total_after = stats_after['total_certifications']
        
        print("\n✅ MISE À JOUR TERMINÉE")
        print(f"\n📊 Résumé :")
        print(f"  • Certifications avant : {total_before}")
        print(f"  • Certifications après : {total_after}")
        print(f"  • Nouvelles/mises à jour : {total_after - total_before}")
        
        # Afficher les certifications récentes
        if stats_after['recent_certifications']:
            print(f"\n🆕 Certifications récentes :")
            for cert in stats_after['recent_certifications'][:5]:
                date_str = cert['certification_date'][:10] if cert['certification_date'] else 'N/A'
                print(f"  • {date_str} : {cert['artist_name']} - {cert['title']} ({cert['certification']})")
        
        return True
    else:
        print("❌ Erreur lors de l'import dans la base de données")
        return False


def check_for_updates():
    """Vérifie s'il y a de nouvelles certifications disponibles"""
    print("🔍 Vérification des mises à jour...")
    
    # Cette fonction pourrait comparer les dates ou le contenu
    # Pour l'instant, elle lance simplement la mise à jour
    return update_snep_database()


def schedule_monthly_update():
    """Programme une mise à jour mensuelle (à utiliser avec cron ou task scheduler)"""
    import logging
    from datetime import datetime
    
    # Configuration du logging pour le mode automatique
    log_file = Path(DATA_PATH) / 'certifications' / 'snep' / 'update_log.txt'
    logging.basicConfig(
        filename=str(log_file),
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    logging.info("=" * 50)
    logging.info("Début de la mise à jour mensuelle programmée")
    
    try:
        success = update_snep_database()
        if success:
            logging.info("✅ Mise à jour réussie")
        else:
            logging.error("❌ Échec de la mise à jour")
    except Exception as e:
        logging.error(f"❌ Erreur lors de la mise à jour : {e}")
    
    logging.info("Fin de la mise à jour mensuelle")
    logging.info("=" * 50)


def main():
    """Point d'entrée principal du script"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Mise à jour automatique des certifications SNEP"
    )
    parser.add_argument(
        '--update', 
        action='store_true',
        help="Télécharger et importer les dernières certifications"
    )
    parser.add_argument(
        '--check', 
        action='store_true',
        help="Vérifier s'il y a des mises à jour disponibles"
    )
    parser.add_argument(
        '--scheduled', 
        action='store_true',
        help="Mode automatique pour tâche planifiée (cron/scheduler)"
    )
    
    args = parser.parse_args()
    
    if args.scheduled:
        # Mode silencieux pour les tâches planifiées
        schedule_monthly_update()
    elif args.update:
        update_snep_database()
    elif args.check:
        check_for_updates()
    else:
        # Par défaut, lancer la mise à jour
        print("💡 Conseil : Utilisez --help pour voir toutes les options\n")
        update_snep_database()


if __name__ == "__main__":
    main()