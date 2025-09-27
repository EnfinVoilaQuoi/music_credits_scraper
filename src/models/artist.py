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


@dataclass
class ArtistDetail:
    """ Métadonnées statiques d’un artiste """
    artist_id: str
    name: str
    spotify_href: Optional[str] = None
    # tu peux ajouter d’autres attributs (genres, popularité, etc.)

    def to_dict(self):
        return {
            "artist_id": self.artist_id,
            "name": self.name,
            "spotify_href": self.spotify_href
        }