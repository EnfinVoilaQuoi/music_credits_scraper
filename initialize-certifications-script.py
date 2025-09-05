"""Script d'initialisation et de test des certifications SNEP"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from src.api.snep_certifications import SNEPCertificationManager
from src.utils.certification_enricher import CertificationEnricher
from src.utils.data_manager import DataManager
from src.utils.logger import get_logger
from src.config import DATA_PATH


logger = get_logger(__name__)


def initialize_snep_database():
    """Initialise la base de données SNEP depuis le CSV"""
    print("=" * 60)
    print("INITIALISATION DES CERTIFICATIONS SNEP")
    print("=" * 60)
    
    # Chemins
    csv_path = Path(DATA_PATH) / 'certifications' / 'snep' / 'Certification SNEP.csv'
    
    # Vérifier que le CSV existe
    if not csv_path.exists():
        print(f"❌ Fichier CSV non trouvé : {csv_path}")
        print(f"Veuillez placer le fichier 'Certification SNEP.csv' dans :")
        print(f"  {csv_path.parent}")
        return False
    
    print(f"✅ Fichier CSV trouvé : {csv_path}")
    
    # Initialiser le manager
    manager = SNEPCertificationManager()
    
    # Importer les données
    print("\n📥 Importation des certifications...")
    success = manager.import_from_csv(csv_path)
    
    if success:
        # Afficher les statistiques
        stats = manager.get_certification_stats()
        print("\n📊 STATISTIQUES D'IMPORT:")
        print(f"  • Total certifications : {stats['total_certifications']}")
        print(f"\n  • Par catégorie:")
        for category, count in stats['by_category'].items():
            print(f"    - {category}: {count}")
        print(f"\n  • Par niveau:")
        for level, count in sorted(stats['by_level'].items(), 
                                  key=lambda x: x[1], reverse=True)[:5]:
            print(f"    - {level}: {count}")
        
        return True
    else:
        print("❌ Erreur lors de l'import")
        return False


def test_josman_certifications():
    """Test avec les certifications de Josman"""
    print("\n" + "=" * 60)
    print("TEST : CERTIFICATIONS DE JOSMAN")
    print("=" * 60)
    
    manager = SNEPCertificationManager()
    
    # Récupérer les certifications de Josman
    certifications = manager.get_artist_certifications("Josman")
    
    if not certifications:
        print("❌ Aucune certification trouvée pour Josman")
        print("   Vérifiez que le CSV a bien été importé")
        return
    
    print(f"\n✅ {len(certifications)} certifications trouvées pour Josman\n")
    
    # Afficher les singles certifiés
    singles = [c for c in certifications if c['category'] == 'Singles']
    albums = [c for c in certifications if c['category'] == 'Albums']
    
    if singles:
        print(f"🎵 SINGLES ({len(singles)}):")
        for cert in singles[:10]:  # Limiter à 10 pour l'affichage
            duration_str = ""
            if cert.get('release_date') and cert.get('certification_date'):
                try:
                    from datetime import datetime
                    release = datetime.fromisoformat(str(cert['release_date']))
                    certif = datetime.fromisoformat(str(cert['certification_date']))
                    duration = (certif - release).days
                    
                    years = duration // 365
                    months = (duration % 365) // 30
                    
                    if years > 0:
                        duration_str = f"{years}a {months}m"
                    else:
                        duration_str = f"{months} mois"
                    
                    duration_str = f" | Durée: {duration_str}"
                except:
                    pass
            
            print(f"  • {cert['title']}")
            print(f"    → {cert['certification']} (constat: {cert['certification_date'][:10]}){duration_str}")
    
    if albums:
        print(f"\n💿 ALBUMS ({len(albums)}):")
        for cert in albums:
            duration_str = ""
            if cert.get('release_date') and cert.get('certification_date'):
                try:
                    from datetime import datetime
                    release = datetime.fromisoformat(str(cert['release_date']))
                    certif = datetime.fromisoformat(str(cert['certification_date']))
                    duration = (certif - release).days
                    
                    years = duration // 365
                    months = (duration % 365) // 30
                    
                    if years > 0:
                        duration_str = f"{years}a {months}m"
                    else:
                        duration_str = f"{months} mois"
                    
                    duration_str = f" | Durée: {duration_str}"
                except:
                    pass
            
            print(f"  • {cert['title']}")
            print(f"    → {cert['certification']} (constat: {cert['certification_date'][:10]}){duration_str}")
    
    # Statistiques
    print(f"\n📈 STATISTIQUES JOSMAN:")
    stats = manager.get_certification_stats("Josman")
    
    # Certifications par niveau
    print(f"\n  Répartition par niveau:")
    for level, count in sorted(stats['by_level'].items(), 
                              key=lambda x: x[1], reverse=True):
        print(f"    • {level}: {count}")
    
    # Timeline
    print(f"\n  📅 Dernières certifications:")
    for cert in stats['recent_certifications'][:5]:
        print(f"    • {cert['certification_date'][:10]}: {cert['title']} ({cert['certification']})")


def test_enrichment():
    """Test de l'enrichissement d'un artiste avec ses certifications"""
    print("\n" + "=" * 60)
    print("TEST : ENRICHISSEMENT AUTOMATIQUE")
    print("=" * 60)
    
    from src.models import Artist, Track
    
    # Créer un artiste test
    artist = Artist(name="Josman", genius_id=123456)
    
    # Ajouter quelques morceaux tests
    artist.tracks = [
        Track(title="Goal", artist=artist),
        Track(title="INTRO", artist=artist),
        Track(title="J.O.$", artist=artist),
        Track(title="Matrix", artist=artist),  # Album
        Track(title="Split", artist=artist),   # Album
    ]
    
    # Enrichir avec les certifications
    enricher = CertificationEnricher()
    artist = enricher.enrich_artist(artist)
    artist.tracks = enricher.enrich_tracks(artist, artist.tracks)
    
    # Calculer les durées
    for track in artist.tracks:
        if track.has_certification:
            track.calculate_certification_duration()
    
    # Afficher les résultats
    print(f"\n✅ Artiste enrichi : {artist.name}")
    
    if hasattr(artist, 'certification_stats'):
        print(f"\n📊 Statistiques globales:")
        stats = artist.certification_stats
        print(f"  • Total: {stats['total']} certifications")
        print(f"  • Singles: {stats['singles_count']} | Albums: {stats['albums_count']}")
        print(f"  • Plus haute: {stats.get('highest_certification', 'N/A')}")
    
    print(f"\n🎵 Morceaux enrichis:")
    for track in artist.tracks:
        if track.has_certification:
            print(f"  • {track.title}: {track.get_certification_info()}")
        else:
            print(f"  • {track.title}: Pas de certification")
    
    # Test du résumé
    summary = enricher.get_certification_summary("Josman")
    print(f"\n📄 RÉSUMÉ:")
    print(summary)


def main():
    """Point d'entrée principal du script"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Gestion des certifications SNEP")
    parser.add_argument('--init', action='store_true', 
                       help="Initialiser la base de données depuis le CSV")
    parser.add_argument('--test', action='store_true',
                       help="Tester avec Josman")
    parser.add_argument('--enrich', action='store_true',
                       help="Tester l'enrichissement")
    parser.add_argument('--artist', type=str,
                       help="Rechercher les certifications d'un artiste")
    
    args = parser.parse_args()
    
    if args.init:
        initialize_snep_database()
    
    if args.test:
        test_josman_certifications()
    
    if args.enrich:
        test_enrichment()
    
    if args.artist:
        manager = SNEPCertificationManager()
        certifications = manager.get_artist_certifications(args.artist)
        
        if certifications:
            print(f"\n✅ {len(certifications)} certifications pour {args.artist}:")
            for cert in certifications[:10]:
                print(f"  • {cert['title']} - {cert['certification']} ({cert['certification_date'][:10]})")
        else:
            print(f"❌ Aucune certification trouvée pour {args.artist}")
    
    if not any([args.init, args.test, args.enrich, args.artist]):
        print("Utilisez --help pour voir les options disponibles")
        print("\nExemples:")
        print("  python initialize_certifications.py --init    # Importer le CSV")
        print("  python initialize_certifications.py --test    # Tester avec Josman")
        print("  python initialize_certifications.py --artist 'PNL'  # Rechercher un artiste")


if __name__ == "__main__":
    main()