"""Gestionnaire de sauvegarde et chargement des données"""
import json
import sqlite3
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime
from contextlib import contextmanager

from src.config import DATABASE_URL, ARTISTS_DIR
from src.models import Artist, Track, Credit, CreditRole
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
            
            # Table des morceaux avec track_number
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    artist_id INTEGER NOT NULL,
                    album TEXT,
                    track_number INTEGER,
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
        """Sauvegarde ou met à jour un morceau - VERSION CORRIGÉE POUR CONTRAINTE UNIQUE"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # S'assurer que l'artiste existe
            if not track.artist or not track.artist.id:
                raise ValueError("Le morceau doit avoir un artiste avec un ID")
            
            # ✅ CORRECTION : Vérifier si le track existe déjà AVANT d'essayer de l'insérer
            cursor.execute("""
                SELECT id FROM tracks 
                WHERE title = ? AND artist_id = ?
            """, (track.title, track.artist.id))
            
            existing_track = cursor.fetchone()
            
            if existing_track:
                # Le track existe déjà, mettre à jour
                track.id = existing_track[0]
                logger.debug(f"Track existant trouvé (ID: {track.id}), mise à jour...")
                
                cursor.execute("""
                    UPDATE tracks 
                    SET album = ?, track_number = ?, release_date = ?, 
                        genius_id = ?, spotify_id = ?, discogs_id = ?,
                        bpm = ?, duration = ?, genre = ?,
                        genius_url = ?, spotify_url = ?,
                        updated_at = ?, last_scraped = ?
                    WHERE id = ?
                """, (track.album, getattr(track, 'track_number', None), track.release_date,
                      track.genius_id, track.spotify_id, track.discogs_id,
                      track.bpm, track.duration, track.genre,
                      track.genius_url, track.spotify_url,
                      datetime.now(), track.last_scraped, track.id))
            else:
                # Nouveau track, insérer
                logger.debug(f"Nouveau track, insertion...")
                
                cursor.execute("""
                    INSERT INTO tracks (
                        title, artist_id, album, track_number, release_date,
                        genius_id, spotify_id, discogs_id,
                        bpm, duration, genre, genius_url, spotify_url,
                        created_at, updated_at, last_scraped
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (track.title, track.artist.id, track.album, getattr(track, 'track_number', None), track.release_date,
                      track.genius_id, track.spotify_id, track.discogs_id,
                      track.bpm, track.duration, track.genre,
                      track.genius_url, track.spotify_url,
                      datetime.now(), datetime.now(), track.last_scraped))
                track.id = cursor.lastrowid
            
            # ✅ AMÉLIORATION : Supprimer les anciens crédits avant d'ajouter les nouveaux (pour éviter les doublons)
            if track.id:
                cursor.execute("DELETE FROM credits WHERE track_id = ?", (track.id,))
                
                # Sauvegarder les nouveaux crédits
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
        """Sauvegarde un crédit - VERSION SIMPLIFIÉE SANS VÉRIFICATION UNIQUE"""
        try:
            cursor.execute("""
                INSERT INTO credits (track_id, name, role, role_detail, source)
                VALUES (?, ?, ?, ?, ?)
            """, (track_id, credit.name, credit.role.value, 
                  credit.role_detail, credit.source))
        except Exception as e:
            # Log mais ne pas arrêter le processus pour un crédit
            logger.debug(f"Erreur lors de la sauvegarde du crédit {credit.name}: {e}")
    
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
                    track_number=row['track_number'],
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
            'total_music_credits': sum(len(t.get_music_credits()) for t in artist.tracks),
            'total_video_credits': sum(len(t.get_video_credits()) for t in artist.tracks),
            'total_all_credits': sum(len(t.credits) for t in artist.tracks)
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
    
    def get_scraping_analytics(self, artist_name: str) -> Dict[str, Any]:
        """Analyse l'efficacité du scraping vs API"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Récupérer les tracks de l'artiste
            cursor.execute("""
                SELECT t.*, a.name as artist_name
                FROM tracks t 
                JOIN artists a ON t.artist_id = a.id 
                WHERE a.name = ?
            """, (artist_name,))
            
            tracks = cursor.fetchall()
            
            analytics = {
                'total_tracks': len(tracks),
                'api_coverage': {
                    'album': 0,
                    'release_date': 0,
                    'genre': 0,
                    'bpm': 0
                },
                'scraping_needed': {
                    'album': 0,
                    'release_date': 0,
                    'genre': 0,
                    'credits': 0
                },
                'data_completeness': {
                    'complete_tracks': 0,
                    'missing_album': 0,
                    'missing_date': 0,
                    'missing_genre': 0,
                    'missing_credits': 0
                },
                'time_savings_estimate': 0
            }
            
            if not tracks:
                return analytics
            
            for track in tracks:
                # Analyser la complétude
                if track['album']:
                    analytics['api_coverage']['album'] += 1
                else:
                    analytics['scraping_needed']['album'] += 1
                    analytics['data_completeness']['missing_album'] += 1
                
                if track['release_date']:
                    analytics['api_coverage']['release_date'] += 1
                else:
                    analytics['scraping_needed']['release_date'] += 1
                    analytics['data_completeness']['missing_date'] += 1
                
                if track['genre']:
                    # Le genre vient toujours du scraping
                    analytics['scraping_needed']['genre'] += 1
                else:
                    analytics['data_completeness']['missing_genre'] += 1
                
                if track['bpm']:
                    analytics['api_coverage']['bpm'] += 1
                
                # Compter les crédits
                cursor.execute("SELECT COUNT(*) FROM credits WHERE track_id = ?", (track['id'],))
                credit_count = cursor.fetchone()[0]
                
                if credit_count > 0:
                    analytics['scraping_needed']['credits'] += 1
                else:
                    analytics['data_completeness']['missing_credits'] += 1
                
                # Track complet = album + date + genre + crédits
                if (track['album'] and track['release_date'] and 
                    track['genre'] and credit_count > 0):
                    analytics['data_completeness']['complete_tracks'] += 1
            
            # Calculer les pourcentages
            total = analytics['total_tracks']
            for category in ['api_coverage', 'scraping_needed', 'data_completeness']:
                for key in analytics[category]:
                    if total > 0:
                        percentage = (analytics[category][key] / total) * 100
                        analytics[category][f'{key}_percentage'] = round(percentage, 1)
            
            # Estimation du gain de temps (basé sur 2s par morceau scraped complètement vs 0.5s pour métadonnées partielles)
            full_scraping_time = total * 2  # 2 secondes par track complet
            partial_scraping_time = (
                analytics['scraping_needed']['album'] * 0.1 +  # 0.1s pour scraper juste l'album
                analytics['scraping_needed']['release_date'] * 0.1 +  # 0.1s pour la date
                analytics['scraping_needed']['genre'] * 0.2 +  # 0.2s pour le genre
                analytics['scraping_needed']['credits'] * 1.5  # 1.5s pour les crédits (toujours nécessaire)
            )
            
            analytics['time_savings_estimate'] = max(0, full_scraping_time - partial_scraping_time)
            analytics['efficiency_gain_percentage'] = round(
                (analytics['time_savings_estimate'] / full_scraping_time * 100) if full_scraping_time > 0 else 0, 1
            )
            
            return analytics

    def delete_artist(self, artist_name: str) -> bool:
        """Supprime un artiste et toutes ses données associées"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Récupérer l'ID de l'artiste
                cursor.execute("SELECT id FROM artists WHERE name = ?", (artist_name,))
                artist_row = cursor.fetchone()
                
                if not artist_row:
                    logger.warning(f"Artiste non trouvé: {artist_name}")
                    return False
                
                artist_id = artist_row[0]
                
                # Supprimer dans l'ordre (contraintes de clés étrangères)
                
                # 1. Supprimer les erreurs de scraping
                cursor.execute("DELETE FROM scraping_errors WHERE track_id IN (SELECT id FROM tracks WHERE artist_id = ?)", (artist_id,))
                deleted_errors = cursor.rowcount
                
                # 2. Supprimer les crédits
                cursor.execute("DELETE FROM credits WHERE track_id IN (SELECT id FROM tracks WHERE artist_id = ?)", (artist_id,))
                deleted_credits = cursor.rowcount
                
                # 3. Supprimer les morceaux
                cursor.execute("DELETE FROM tracks WHERE artist_id = ?", (artist_id,))
                deleted_tracks = cursor.rowcount
                
                # 4. Supprimer l'artiste
                cursor.execute("DELETE FROM artists WHERE id = ?", (artist_id,))
                deleted_artist = cursor.rowcount
                
                conn.commit()
                
                logger.info(f"Artiste '{artist_name}' supprimé avec succès:")
                logger.info(f"  - {deleted_tracks} morceaux")
                logger.info(f"  - {deleted_credits} crédits")
                logger.info(f"  - {deleted_errors} erreurs de scraping")
                
                return deleted_artist > 0
                
        except Exception as e:
            logger.error(f"Erreur lors de la suppression de l'artiste: {e}")
            return False

    def get_artist_details(self, artist_name: str) -> Dict[str, Any]:
        """Récupère les détails complets d'un artiste"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Informations de base de l'artiste
                cursor.execute("""
                    SELECT id, name, genius_id, spotify_id, discogs_id, created_at, updated_at
                    FROM artists WHERE name = ?
                """, (artist_name,))
                
                artist_row = cursor.fetchone()
                if not artist_row:
                    return {}
                
                artist_id = artist_row[0]
                
                # Compter les morceaux et crédits
                cursor.execute("""
                    SELECT 
                        COUNT(DISTINCT t.id) as tracks_count,
                        COUNT(DISTINCT c.id) as credits_count
                    FROM tracks t
                    LEFT JOIN credits c ON t.id = c.track_id
                    WHERE t.artist_id = ?
                """, (artist_id,))
                
                counts = cursor.fetchone()
                
                # Morceaux récents
                cursor.execute("""
                    SELECT 
                        t.title, 
                        t.album, 
                        t.release_date,
                        COUNT(c.id) as credits_count
                    FROM tracks t
                    LEFT JOIN credits c ON t.id = c.track_id
                    WHERE t.artist_id = ?
                    GROUP BY t.id, t.title, t.album, t.release_date
                    ORDER BY t.updated_at DESC
                    LIMIT 20
                """, (artist_id,))
                
                recent_tracks = []
                for row in cursor.fetchall():
                    recent_tracks.append({
                        'title': row[0],
                        'album': row[1],
                        'release_date': row[2],
                        'credits_count': row[3]
                    })
                
                # Crédits par rôle
                cursor.execute("""
                    SELECT role, COUNT(*) as count
                    FROM credits c
                    JOIN tracks t ON c.track_id = t.id
                    WHERE t.artist_id = ?
                    GROUP BY role
                    ORDER BY count DESC
                """, (artist_id,))
                
                credits_by_role = {}
                for row in cursor.fetchall():
                    credits_by_role[row[0]] = row[1]
                
                return {
                    'name': artist_row[1],
                    'genius_id': artist_row[2],
                    'spotify_id': artist_row[3],
                    'discogs_id': artist_row[4],
                    'created_at': artist_row[5],
                    'updated_at': artist_row[6],
                    'tracks_count': counts[0] if counts else 0,
                    'credits_count': counts[1] if counts else 0,
                    'recent_tracks': recent_tracks,
                    'credits_by_role': credits_by_role
                }
                
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des détails: {e}")
            return {}

    def clean_orphaned_data(self) -> Dict[str, int]:
        """Nettoie les données orphelines dans la base"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Supprimer les crédits orphelins (sans track)
                cursor.execute("""
                    DELETE FROM credits 
                    WHERE track_id NOT IN (SELECT id FROM tracks)
                """)
                orphaned_credits = cursor.rowcount
                
                # Supprimer les erreurs orphelines (sans track)
                cursor.execute("""
                    DELETE FROM scraping_errors 
                    WHERE track_id NOT IN (SELECT id FROM tracks)
                """)
                orphaned_errors = cursor.rowcount
                
                # Supprimer les tracks orphelins (sans artiste)
                cursor.execute("""
                    DELETE FROM tracks 
                    WHERE artist_id NOT IN (SELECT id FROM artists)
                """)
                orphaned_tracks = cursor.rowcount
                
                conn.commit()
                
                cleaned = {
                    'orphaned_credits': orphaned_credits,
                    'orphaned_errors': orphaned_errors,
                    'orphaned_tracks': orphaned_tracks
                }
                
                if sum(cleaned.values()) > 0:
                    logger.info(f"Nettoyage terminé: {cleaned}")
                
                return cleaned
                
        except Exception as e:
            logger.error(f"Erreur lors du nettoyage: {e}")
            return {'orphaned_credits': 0, 'orphaned_errors': 0, 'orphaned_tracks': 0}

    def get_database_size_info(self) -> Dict[str, Any]:
        """Retourne des informations sur la taille de la base de données"""
        try:
            import os
            
            db_file_path = self.db_path
            
            info = {
                'file_exists': os.path.exists(db_file_path),
                'file_size_bytes': 0,
                'file_size_mb': 0,
                'file_path': db_file_path
            }
            
            if info['file_exists']:
                info['file_size_bytes'] = os.path.getsize(db_file_path)
                info['file_size_mb'] = round(info['file_size_bytes'] / (1024 * 1024), 2)
            
            # Statistiques des tables
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                tables = ['artists', 'tracks', 'credits', 'scraping_errors']
                for table in tables:
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    count = cursor.fetchone()[0]
                    info[f'{table}_count'] = count
            
            return info
            
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des infos de taille: {e}")
            return {}