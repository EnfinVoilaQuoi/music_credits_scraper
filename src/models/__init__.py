# src/models/__init__.py
from .artist import Artist
from .track import Track, Credit

__all__ = ['Artist', 'Track', 'Credit']

# -------------------------------
# src/models/artist.py
"""Modèle pour représenter un artiste"""
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime


@dataclass
class Artist:
    """Représente un artiste musical"""
    id: Optional[int] = None
    name: str = ""
    genius_id: Optional[int] = None
    spotify_id: Optional[str] = None
    discogs_id: Optional[int] = None
    tracks: List['Track'] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    def add_track(self, track: 'Track'):
        """Ajoute un morceau à l'artiste"""
        if track not in self.tracks:
            self.tracks.append(track)
            track.artist = self
    
    def get_tracks_count(self) -> int:
        """Retourne le nombre de morceaux"""
        return len(self.tracks)
    
    def to_dict(self) -> dict:
        """Convertit l'artiste en dictionnaire"""
        return {
            'id': self.id,
            'name': self.name,
            'genius_id': self.genius_id,
            'spotify_id': self.spotify_id,
            'discogs_id': self.discogs_id,
            'tracks_count': self.get_tracks_count(),
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }

# -------------------------------
# src/models/track.py
"""Modèles pour représenter les morceaux et crédits"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum


class CreditRole(Enum):
    """Types de rôles dans les crédits"""
    PRODUCER = "Producer"
    WRITER = "Writer"
    PERFORMER = "Performer"
    FEATURED = "Featured Artist"
    BACKGROUND_VOCALS = "Background Vocals"
    ADDITIONAL_VOCALS = "Additional Vocals"
    MUSICIAN = "Musician"
    ENGINEER = "Engineer"
    MIXER = "Mixer"
    MASTERING = "Mastering Engineer"
    SAMPLE = "Sample"
    INTERPOLATION = "Interpolation"
    OTHER = "Other"


@dataclass
class Credit:
    """Représente un crédit sur un morceau"""
    name: str
    role: CreditRole
    role_detail: Optional[str] = None  # Ex: "Guitar", "Piano", etc.
    source: str = "genius"  # Source de l'information
    
    def to_dict(self) -> dict:
        """Convertit le crédit en dictionnaire"""
        return {
            'name': self.name,
            'role': self.role.value,
            'role_detail': self.role_detail,
            'source': self.source
        }


@dataclass
class Track:
    """Représente un morceau musical"""
    id: Optional[int] = None
    title: str = ""
    artist: Optional['Artist'] = None
    album: Optional[str] = None
    release_date: Optional[datetime] = None
    
    # IDs externes
    genius_id: Optional[int] = None
    spotify_id: Optional[str] = None
    discogs_id: Optional[int] = None
    
    # Métadonnées
    bpm: Optional[int] = None
    duration: Optional[int] = None  # En secondes
    genre: Optional[str] = None
    
    # Crédits
    credits: List[Credit] = field(default_factory=list)
    
    # URLs
    genius_url: Optional[str] = None
    spotify_url: Optional[str] = None
    
    # Métadonnées système
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    last_scraped: Optional[datetime] = None
    scraping_errors: List[str] = field(default_factory=list)
    
    def add_credit(self, credit: Credit):
        """Ajoute un crédit au morceau"""
        # Éviter les doublons
        for existing in self.credits:
            if (existing.name == credit.name and 
                existing.role == credit.role and
                existing.role_detail == credit.role_detail):
                return
        self.credits.append(credit)
    
    def get_credits_by_role(self, role: CreditRole) -> List[Credit]:
        """Retourne tous les crédits d'un rôle spécifique"""
        return [c for c in self.credits if c.role == role]
    
    def get_producers(self) -> List[str]:
        """Retourne la liste des producteurs"""
        return [c.name for c in self.get_credits_by_role(CreditRole.PRODUCER)]
    
    def get_writers(self) -> List[str]:
        """Retourne la liste des auteurs"""
        return [c.name for c in self.get_credits_by_role(CreditRole.WRITER)]
    
    def has_complete_credits(self) -> bool:
        """Vérifie si les crédits semblent complets"""
        # Au minimum, on devrait avoir des producteurs et auteurs
        return bool(self.get_producers() and self.get_writers())
    
    def to_dict(self) -> dict:
        """Convertit le morceau en dictionnaire"""
        return {
            'id': self.id,
            'title': self.title,
            'artist': self.artist.name if self.artist else None,
            'album': self.album,
            'release_date': self.release_date.isoformat() if self.release_date else None,
            'genius_id': self.genius_id,
            'spotify_id': self.spotify_id,
            'discogs_id': self.discogs_id,
            'bpm': self.bpm,
            'duration': self.duration,
            'genre': self.genre,
            'credits': [c.to_dict() for c in self.credits],
            'credits_count': len(self.credits),
            'has_complete_credits': self.has_complete_credits(),
            'genius_url': self.genius_url,
            'spotify_url': self.spotify_url,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'last_scraped': self.last_scraped.isoformat() if self.last_scraped else None,
            'scraping_errors': self.scraping_errors
        }
