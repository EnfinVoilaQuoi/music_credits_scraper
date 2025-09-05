"""Enrichissement des données avec les certifications"""
from typing import List, Dict, Any, Optional
from datetime import datetime

from src.models import Artist, Track
from src.models.certification import Certification
from src.api.snep_certifications import get_snep_manager
from src.utils.logger import get_logger


logger = get_logger(__name__)


class CertificationEnricher:
    """Enrichit les données d'artistes et morceaux avec les certifications"""
    
    def __init__(self):
        """Initialise l'enrichisseur de certifications"""
        self.snep_manager = get_snep_manager()
        logger.info("✅ CertificationEnricher initialisé")
    
    def enrich_artist(self, artist: Artist) -> Artist:
        """Enrichit un artiste avec ses certifications"""
        if not artist or not artist.name:
            return artist
        
        try:
            # Récupérer toutes les certifications de l'artiste
            certifications = self.snep_manager.get_artist_certifications(artist.name)
            
            if certifications:
                # Ajouter les certifications à l'artiste
                if not hasattr(artist, 'certifications'):
                    artist.certifications = []
                
                artist.certifications = certifications
                
                # Ajouter des statistiques
                artist.certification_stats = self._calculate_artist_stats(certifications)
                
                logger.info(f"✅ {len(certifications)} certifications trouvées pour {artist.name}")
            else:
                logger.info(f"ℹ️ Aucune certification trouvée pour {artist.name}")
                artist.certifications = []
                artist.certification_stats = {}
            
        except Exception as e:
            logger.error(f"❌ Erreur lors de l'enrichissement de {artist.name}: {e}")
            artist.certifications = []
            artist.certification_stats = {}
        
        return artist
    
    def enrich_tracks(self, artist: Artist, tracks: List[Track]) -> List[Track]:
        """Enrichit une liste de morceaux avec leurs certifications"""
        if not tracks or not artist:
            return tracks
        
        enriched_count = 0
        
        for track in tracks:
            try:
                # Rechercher la certification du morceau
                cert_data = self.snep_manager.get_track_certification(
                    artist.name, 
                    track.title
                )
                
                if cert_data:
                    # Ajouter les données de certification au morceau
                    track.certification = cert_data
                    track.has_certification = True
                    track.certification_level = cert_data.get('certification', '')
                    track.certification_date = cert_data.get('certification_date', '')
                    
                    enriched_count += 1
                    logger.debug(f"✅ Certification trouvée: {track.title} - {track.certification_level}")
                else:
                    track.certification = None
                    track.has_certification = False
                    track.certification_level = None
                    track.certification_date = None
                    
            except Exception as e:
                logger.error(f"Erreur enrichissement {track.title}: {e}")
                track.certification = None
                track.has_certification = False
        
        if enriched_count > 0:
            logger.info(f"🏆 {enriched_count}/{len(tracks)} morceaux enrichis avec certifications")
        
        return tracks
    
    def _calculate_artist_stats(self, certifications: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calcule les statistiques de certification d'un artiste"""
        stats = {
            'total': len(certifications),
            'by_level': {},
            'by_category': {},
            'by_year': {},
            'highest_certification': None,
            'most_recent': None,
            'singles_count': 0,
            'albums_count': 0
        }
        
        if not certifications:
            return stats
        
        # Ordre de priorité des certifications
        cert_order = [
            'Quadruple Diamant', 'Triple Diamant', 'Double Diamant', 'Diamant',
            'Triple Platine', 'Double Platine', 'Platine',
            'Triple Or', 'Double Or', 'Or'
        ]
        
        for cert in certifications:
            # Par niveau
            level = cert.get('certification', '')
            stats['by_level'][level] = stats['by_level'].get(level, 0) + 1
            
            # Par catégorie
            category = cert.get('category', '')
            stats['by_category'][category] = stats['by_category'].get(category, 0) + 1
            
            # Compter singles et albums
            if category == 'Singles':
                stats['singles_count'] += 1
            elif category == 'Albums':
                stats['albums_count'] += 1
            
            # Par année
            cert_date = cert.get('certification_date')
            if cert_date:
                if isinstance(cert_date, str):
                    try:
                        cert_date = datetime.fromisoformat(cert_date)
                    except:
                        continue
                year = cert_date.year
                stats['by_year'][year] = stats['by_year'].get(year, 0) + 1
        
        # Plus haute certification
        for cert_level in cert_order:
            if cert_level in stats['by_level']:
                stats['highest_certification'] = cert_level
                break
        
        # Certification la plus récente
        sorted_certs = sorted(
            certifications, 
            key=lambda x: x.get('certification_date', ''), 
            reverse=True
        )
        if sorted_certs:
            most_recent = sorted_certs[0]
            stats['most_recent'] = {
                'title': most_recent.get('title'),
                'level': most_recent.get('certification'),
                'date': most_recent.get('certification_date')
            }
        
        return stats
    
    def get_certification_summary(self, artist_name: str) -> str:
        """Génère un résumé textuel des certifications d'un artiste"""
        certifications = self.snep_manager.get_artist_certifications(artist_name)
        
        if not certifications:
            return f"Aucune certification trouvée pour {artist_name}"
        
        stats = self._calculate_artist_stats(certifications)
        
        summary = f"📊 Certifications de {artist_name}:\n"
        summary += f"• Total: {stats['total']} certifications\n"
        summary += f"• Singles: {stats['singles_count']} | Albums: {stats['albums_count']}\n"
        
        if stats['highest_certification']:
            summary += f"• Plus haute: {stats['highest_certification']}\n"
        
        if stats['most_recent']:
            summary += f"• Plus récente: {stats['most_recent']['title']} "
            summary += f"({stats['most_recent']['level']}) - {stats['most_recent']['date']}\n"
        
        # Détail par niveau
        if stats['by_level']:
            summary += "\n📈 Par niveau:\n"
            for level, count in sorted(stats['by_level'].items(), 
                                      key=lambda x: x[1], reverse=True):
                summary += f"  • {level}: {count}\n"
        
        return summary
    
    def calculate_certification_duration(self, track: Track) -> Optional[int]:
        """Calcule la durée en jours pour obtenir une certification"""
        if not track.certification or not track.release_date:
            return None
        
        try:
            cert_date = track.certification.get('certification_date')
            if isinstance(cert_date, str):
                cert_date = datetime.fromisoformat(cert_date)
            
            if isinstance(track.release_date, str):
                release_date = datetime.fromisoformat(track.release_date)
            else:
                release_date = track.release_date
            
            duration = (cert_date - release_date).days
            return duration if duration >= 0 else None
            
        except Exception as e:
            logger.error(f"Erreur calcul durée certification: {e}")
            return None