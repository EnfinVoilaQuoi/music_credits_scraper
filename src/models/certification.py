"""Modèles pour représenter les certifications musicales"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum


class CertificationLevel(Enum):
    """Niveaux de certification"""
    # Certifications françaises SNEP
    OR = "Or"
    DOUBLE_OR = "Double Or"
    TRIPLE_OR = "Triple Or"
    PLATINE = "Platine"
    DOUBLE_PLATINE = "Double Platine"
    TRIPLE_PLATINE = "Triple Platine"
    DIAMANT = "Diamant"
    DOUBLE_DIAMANT = "Double Diamant"
    TRIPLE_DIAMANT = "Triple Diamant"
    QUADRUPLE_DIAMANT = "Quadruple Diamant"
    
    # Certifications internationales (pour extension future)
    GOLD = "Gold"
    PLATINUM = "Platinum"
    DIAMOND = "Diamond"
    
    @classmethod
    def from_string(cls, value: str) -> Optional['CertificationLevel']:
        """Convertit une string en CertificationLevel"""
        value_clean = value.strip().upper().replace(' ', '_')
        
        # Mapping des variantes
        mapping = {
            'OR': cls.OR,
            'DOUBLE_OR': cls.DOUBLE_OR,
            'TRIPLE_OR': cls.TRIPLE_OR,
            'PLATINE': cls.PLATINE,
            'DOUBLE_PLATINE': cls.DOUBLE_PLATINE,
            'TRIPLE_PLATINE': cls.TRIPLE_PLATINE,
            'DIAMANT': cls.DIAMANT,
            'DOUBLE_DIAMANT': cls.DOUBLE_DIAMANT,
            'TRIPLE_DIAMANT': cls.TRIPLE_DIAMANT,
            'QUADRUPLE_DIAMANT': cls.QUADRUPLE_DIAMANT,
            'GOLD': cls.GOLD,
            'PLATINUM': cls.PLATINUM,
            'DIAMOND': cls.DIAMOND
        }
        
        return mapping.get(value_clean)
    
    def get_threshold(self, country: str = 'FR', category: str = 'Singles') -> int:
        """Retourne le seuil pour cette certification"""
        thresholds = {
            'FR': {
                'Singles': {
                    self.OR: 15_000_000,
                    self.PLATINE: 30_000_000,
                    self.DIAMANT: 50_000_000
                },
                'Albums': {
                    self.OR: 50_000,
                    self.DOUBLE_OR: 100_000,
                    self.PLATINE: 100_000,
                    self.DOUBLE_PLATINE: 200_000,
                    self.TRIPLE_PLATINE: 300_000,
                    self.DIAMANT: 500_000
                }
            }
        }
        
        try:
            return thresholds[country][category][self]
        except KeyError:
            return 0


class CertificationCategory(Enum):
    """Catégories de certification"""
    SINGLES = "Singles"
    ALBUMS = "Albums"
    VIDEOS = "Vidéos"
    DVD = "DVD"

    @classmethod
    def from_string(cls, value: str) -> 'CertificationCategory':
        """Convertit une string en CertificationCategory"""
        if not value:
            return cls.SINGLES

        value_clean = value.strip().upper()

        # Normaliser "Single" -> "Singles"
        if value_clean == 'SINGLE':
            value_clean = 'SINGLES'

        mapping = {
            'SINGLES': cls.SINGLES,
            'ALBUMS': cls.ALBUMS,
            'VIDÉOS': cls.VIDEOS,
            'VIDEOS': cls.VIDEOS,
            'DVD': cls.DVD
        }

        return mapping.get(value_clean, cls.SINGLES)


@dataclass
class Certification:
    """Représente une certification musicale"""
    id: Optional[int] = None
    artist_name: str = ""
    title: str = ""
    category: CertificationCategory = CertificationCategory.SINGLES
    level: CertificationLevel = CertificationLevel.OR
    certification_date: Optional[datetime] = None
    release_date: Optional[datetime] = None
    publisher: Optional[str] = None
    country: str = "FR"
    certifying_body: str = "SNEP"
    
    # Métadonnées
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    # Champs calculés
    threshold_units: int = field(init=False)
    
    def __post_init__(self):
        """Calcule les champs dérivés après initialisation"""
        self.threshold_units = self.level.get_threshold(self.country, self.category.value)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit la certification en dictionnaire"""
        return {
            'id': self.id,
            'artist_name': self.artist_name,
            'title': self.title,
            'category': self.category.value,
            'level': self.level.value,
            'certification_date': self.certification_date.isoformat() if self.certification_date else None,
            'release_date': self.release_date.isoformat() if self.release_date else None,
            'publisher': self.publisher,
            'country': self.country,
            'certifying_body': self.certifying_body,
            'threshold_units': self.threshold_units,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Certification':
        """Crée une certification depuis un dictionnaire"""
        cert = cls()
        
        # Champs simples
        cert.id = data.get('id')
        cert.artist_name = data.get('artist_name', '')
        cert.title = data.get('title', '')
        cert.publisher = data.get('publisher')
        cert.country = data.get('country', 'FR')
        cert.certifying_body = data.get('certifying_body', 'SNEP')
        
        # Enums
        if 'category' in data:
            cert.category = CertificationCategory[data['category'].upper().replace(' ', '_')]
        if 'level' in data:
            cert.level = CertificationLevel.from_string(data['level'])
        
        # Dates
        if data.get('certification_date'):
            cert.certification_date = datetime.fromisoformat(data['certification_date'])
        if data.get('release_date'):
            cert.release_date = datetime.fromisoformat(data['release_date'])
        if data.get('created_at'):
            cert.created_at = datetime.fromisoformat(data['created_at'])
        if data.get('updated_at'):
            cert.updated_at = datetime.fromisoformat(data['updated_at'])
        
        return cert
    
    def __str__(self) -> str:
        """Représentation string"""
        return f"{self.artist_name} - {self.title} ({self.level.value} {self.country})"
    
    def __repr__(self) -> str:
        """Représentation pour debug"""
        return f"<Certification: {self.artist_name} - {self.title} - {self.level.value}>"