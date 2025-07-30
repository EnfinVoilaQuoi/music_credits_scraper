"""Gestionnaire de sauvegarde et chargement des données"""
import json
import sqlite3
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime
from contextlib import contextmanager

from src.config import DATABASE_URL, ARTISTS_DIR
from src.models import Artist, Track, Credit
from src.utils.logger import get_logger


logger = get_logger(__name__)


class DataManager:
    """Gère la persistance des données"""
    
    def __init__(self):
        self.db_path = DATABASE_URL.replace("sqlite:///", "")
        self._init_database()
    
    def _init_database(self):
        """Initialise la base de données"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Table des artistes
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS artists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    genius_id INTEGER,
                    spotify_id TEXT,
                    discogs_id INTEGER,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
            """)
            
            # Table des morceaux
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    artist_id INTEGER NOT NULL,
                    album TEXT,
                    release_date TIMESTAMP,
                    genius_id INTEGER,
                    spotify_id TEXT,
                    discogs_id INTEGER,
                    bpm INTEGER,
                    duration INTEGER,
                    genre TEXT,
                    genius_url TEXT,
                    spotify_url TEXT,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP,
                    last_scraped TIMESTAMP,
                    FOREIGN KEY (artist_id) REFERENCES artists (id),
                    UNIQUE(title, artist_id)
                )
            """)
            
            # Table des crédits
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS credits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    role_detail TEXT,
                    source TEXT,
                    FOREIGN KEY (track_id) REFERENCES tracks (id),
                    UNIQUE(track_id, name, role, role_detail)
                )
            """)
            
            # Table des erreurs de scraping
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scraping_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id INTEGER NOT NULL,
                    error_message TEXT,
                    error_time TIMESTAMP,
                    FOREIGN KEY (track_id) REFERENCES tracks (id)
                )
            """)
            
            conn.commit()
            logger.info("Base de données initialisée")
    
    @contextmanager
    def _get_connection(self):
        """Context manager pour les connexions à la base de données"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def save_artist(self, artist: Artist) -> int:
        """Sauvegarde ou met à jour un artiste"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            if artist.id:
                # Mise à jour
                cursor.execute("""
                    UPDATE artists 
                    SET name = ?, genius_id = ?, spotify_id = ?, 
                        discogs_id = ?, updated_at = ?
                    WHERE id = ?
                """, (artist.name, artist.genius_id, artist.spotify_id,
                      artist.discogs_id, datetime.now(), artist.id))
            else:
                # Insertion
                cursor.execute("""
                    INSERT INTO artists (name, genius_id, spotify_id, 
                                       discogs_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (artist.name, artist.genius_id, artist.spotify_id,
                      artist.discogs_id, datetime.now(), datetime.now()))
                artist.id = cursor.lastrowid
            
            conn.commit()
            logger.info(f"Artiste sauvegardé: {artist.name} (ID: {artist.id})")
            return artist.id
    
    def save_track(self, track: Track) -> int:
        """Sauvegarde ou met à jour un morceau"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # S'assurer que l'artiste existe
            if not track.artist or not track.artist.id:
                raise ValueError("Le morceau doit avoir un artiste avec un ID")
            
            if track.id:
                # Mise à jour
                cursor.execute("""
                    UPDATE tracks 
                    SET title = ?, album = ?, release_date = ?, 
                        genius_id = ?, spotify_id = ?, discogs_id = ?,
                        bpm = ?, duration = ?, genre = ?,
                        genius_url = ?, spotify_url = ?,
                        updated_at = ?, last_scraped = ?
                    WHERE id = ?
                """, (track.title, track.album, track.release_date,
                      track.genius_id, track.spotify_id, track.discogs_id,
                      track.bpm, track.duration, track.genre,
                      track.genius_url, track.spotify_url,
                      datetime.now(), track.last_scraped, track.id))
            else:
                # Insertion
                cursor.execute("""
                    INSERT INTO tracks (
                        title, artist_id, album, release_date,
                        genius_id, spotify_id, discogs_id,
                        bpm, duration, genre, genius_url, spotify_url,
                        created_at, updated_at, last_scraped
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (track.title, track.artist.id, track.album, track.release_date,
                      track.genius_id, track.spotify_id, track.discogs_id,
                      track.bpm, track.duration, track.genre,
                      track.genius_url, track.spotify_url,
                      datetime.now(), datetime.now(), track.last_scraped))
                track.id = cursor.lastrowid
            
            # Sauvegarder les crédits
            for credit in track.credits:
                self._save_credit(cursor, track.id, credit)
            
            # Sauvegarder les erreurs
            for error in track.scraping_errors:
                cursor.execute("""
                    INSERT INTO scraping_errors (track_id, error_message, error_time)
                    VALUES (?, ?, ?)
                """, (track.id, error, datetime.now()))
            
            conn.commit()
            logger.info(f"Morceau sauvegardé: {track.title} (ID: {track.id})")
            return track.id
    
    def _save_credit(self, cursor, track_id: int, credit: Credit):
        """Sauvegarde un crédit"""
        try:
            cursor.execute("""
                INSERT INTO credits (track_id, name, role, role_detail, source)
                VALUES (?, ?, ?, ?, ?)
            """, (track_id, credit.name, credit.role.value, 
                  credit.role_detail, credit.source))
        except sqlite3.IntegrityError:
            # Le crédit existe déjà, on ignore
            pass
    
    def get_artist_by_name(self, name: str) -> Optional[Artist]:
        """Récupère un artiste par son nom"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM artists WHERE name = ?", (name,))
            row = cursor.fetchone()
            
            if row:
                artist = Artist(
                    id=row['id'],
                    name=row['name'],
                    genius_id=row['genius_id'],
                    spotify_id=row['spotify_id'],
                    discogs_id=row['discogs_id']
                )
                # Charger les morceaux
                artist.tracks = self.get_artist_tracks(artist.id)
                return artist
            return None
    
    def get_artist_tracks(self, artist_id: int) -> List[Track]:
        """Récupère tous les morceaux d'un artiste"""
        tracks = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM tracks WHERE artist_id = ?
                ORDER BY title
            """, (artist_id,))
            
            for row in cursor.fetchall():
                track = Track(
                    id=row['id'],
                    title=row['title'],
                    album=row['album'],
                    release_date=row['release_date'],
                    genius_id=row['genius_id'],
                    spotify_id=row['spotify_id'],
                    discogs_id=row['discogs_id'],
                    bpm=row['bpm'],
                    duration=row['duration'],
                    genre=row['genre'],
                    genius_url=row['genius_url'],
                    spotify_url=row['spotify_url'],
                    last_scraped=row['last_scraped']
                )
                
                # Charger les crédits
                track.credits = self._get_track_credits(cursor, track.id)
                tracks.append(track)
        
        return tracks
    
    def _get_track_credits(self, cursor, track_id: int) -> List[Credit]:
        """Récupère les crédits d'un morceau"""
        credits = []
        cursor.execute("""
            SELECT * FROM credits WHERE track_id = ?
        """, (track_id,))
        
        for row in cursor.fetchall():
            credit = Credit(
                name=row['name'],
                role=CreditRole(row['role']),
                role_detail=row['role_detail'],
                source=row['source']
            )
            credits.append(credit)
        
        return credits
    
    def export_to_json(self, artist_name: str, filepath: Optional[Path] = None):
        """Exporte les données d'un artiste en JSON"""
        artist = self.get_artist_by_name(artist_name)
        if not artist:
            logger.error(f"Artiste non trouvé: {artist_name}")
            return None
        
        # Déterminer le chemin du fichier
        if filepath is None:
            filename = f"{artist.name.replace(' ', '_').lower()}_credits.json"
            filepath = ARTISTS_DIR / filename
        
        # Préparer les données
        data = {
            'artist': artist.to_dict(),
            'tracks': [track.to_dict() for track in artist.tracks],
            'export_date': datetime.now().isoformat(),
            'total_tracks': len(artist.tracks),
            'total_credits': sum(len(t.credits) for t in artist.tracks)
        }
        
        # Sauvegarder
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Données exportées vers: {filepath}")
        return filepath
    
    def get_statistics(self) -> Dict[str, Any]:
        """Retourne des statistiques sur la base de données"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            stats = {}
            
            # Nombre d'artistes
            cursor.execute("SELECT COUNT(*) FROM artists")
            stats['total_artists'] = cursor.fetchone()[0]
            
            # Nombre de morceaux
            cursor.execute("SELECT COUNT(*) FROM tracks")
            stats['total_tracks'] = cursor.fetchone()[0]
            
            # Nombre de crédits
            cursor.execute("SELECT COUNT(*) FROM credits")
            stats['total_credits'] = cursor.fetchone()[0]
            
            # Morceaux avec crédits complets
            cursor.execute("""
                SELECT COUNT(DISTINCT t.id) 
                FROM tracks t 
                JOIN credits c ON t.id = c.track_id 
                WHERE c.role IN ('Producer', 'Writer')
                GROUP BY t.id 
                HAVING COUNT(DISTINCT c.role) = 2
            """)
            result = cursor.fetchone()
            stats['tracks_with_complete_credits'] = result[0] if result else 0
            
            # Erreurs récentes
            cursor.execute("""
                SELECT COUNT(*) FROM scraping_errors 
                WHERE error_time > datetime('now', '-1 day')
            """)
            stats['recent_errors'] = cursor.fetchone()[0]
            
            return stats
