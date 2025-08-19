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
                    youtube_url TEXT,
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
        """Sauvegarde ou met à jour un morceau - VERSION SANS YOUTUBE_URL"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            if not track.artist or not track.artist.id:
                raise ValueError("Le morceau doit avoir un artiste avec un ID")
            
            # CORRECTION: Supprimer youtube_url de la requête
            cursor.execute("""
                SELECT id, is_featuring, primary_artist_name, featured_artists, 
                    lyrics, has_lyrics, lyrics_scraped_at FROM tracks 
                WHERE title = ? AND artist_id = ?
            """, (track.title, track.artist.id))
            
            existing_track = cursor.fetchone()
            
            if existing_track:
                track.id = existing_track[0]
                
                # Préserver les infos existantes
                if existing_track[1] and not hasattr(track, 'is_featuring'):
                    track.is_featuring = bool(existing_track[1])
                    track.primary_artist_name = existing_track[2]
                    track.featured_artists = existing_track[3]
                
                if existing_track[4] and not hasattr(track, 'lyrics'):
                    track.lyrics = existing_track[4]
                    track.has_lyrics = bool(existing_track[5])
                    track.lyrics_scraped_at = existing_track[6]
                
                # CORRECTION: Supprimer youtube_url de l'UPDATE
                cursor.execute("""
                    UPDATE tracks 
                    SET album = ?, track_number = ?, release_date = ?, 
                        genius_id = ?, spotify_id = ?, discogs_id = ?,
                        bpm = ?, duration = ?, genre = ?,
                        genius_url = ?, spotify_url = ?,
                        is_featuring = ?, primary_artist_name = ?, featured_artists = ?,
                        lyrics = ?, lyrics_scraped_at = ?, has_lyrics = ?,
                        updated_at = ?, last_scraped = ?
                    WHERE id = ?
                """, (track.album, getattr(track, 'track_number', None), track.release_date,
                    track.genius_id, track.spotify_id, track.discogs_id,
                    track.bpm, track.duration, track.genre,
                    track.genius_url, track.spotify_url,
                    getattr(track, 'is_featuring', False),
                    getattr(track, 'primary_artist_name', None),
                    getattr(track, 'featured_artists', None),
                    getattr(track, 'lyrics', None),
                    getattr(track, 'lyrics_scraped_at', None),
                    bool(getattr(track, 'lyrics', None)),
                    datetime.now(), track.last_scraped, track.id))
            else:
                # CORRECTION: Supprimer youtube_url de l'INSERT
                cursor.execute("""
                    INSERT INTO tracks (
                        title, artist_id, album, track_number, release_date,
                        genius_id, spotify_id, discogs_id,
                        bmp, duration, genre, genius_url, spotify_url,
                        is_featuring, primary_artist_name, featured_artists,
                        lyrics, lyrics_scraped_at, has_lyrics,
                        created_at, updated_at, last_scraped
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (track.title, track.artist.id, track.album, getattr(track, 'track_number', None), track.release_date,
                    track.genius_id, track.spotify_id, track.discogs_id,
                    track.bpm, track.duration, track.genre,
                    track.genius_url, track.spotify_url,
                    getattr(track, 'is_featuring', False),
                    getattr(track, 'primary_artist_name', None),
                    getattr(track, 'featured_artists', None),
                    getattr(track, 'lyrics', None),
                    getattr(track, 'lyrics_scraped_at', None),
                    bool(getattr(track, 'lyrics', None)),
                    datetime.now(), datetime.now(), track.last_scraped))
                track.id = cursor.lastrowid
            
            # Supprimer les anciens crédits avant d'ajouter les nouveaux
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
            
            lyrics_info = f", Paroles: {bool(getattr(track, 'lyrics', None))}" if hasattr(track, 'lyrics') else ""
            logger.info(f"Morceau sauvegardé: {track.title} (ID: {track.id}, Featuring: {getattr(track, 'is_featuring', False)}{lyrics_info})")
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
        """Récupère un artiste par son nom - VERSION CORRIGÉE"""
        try:
            logger.debug(f"🔍 Recherche de l'artiste: '{name}'")
            
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, name, genius_id, spotify_id, discogs_id FROM artists WHERE name = ?", (name,))
                row = cursor.fetchone()
                
                if not row:
                    logger.debug(f"❌ Aucun artiste trouvé pour: '{name}'")
                    return None
                
                # Accès par INDEX pour éviter les erreurs de clé
                artist = Artist(
                    id=row[0],           # id
                    name=row[1],         # name
                    genius_id=row[2],    # genius_id
                    spotify_id=row[3],   # spotify_id
                    discogs_id=row[4]    # discogs_id
                )
                
                logger.debug(f"🎤 Objet Artist créé: {artist.name} (ID: {artist.id})")
                
                # Charger les tracks
                try:
                    artist.tracks = self.get_artist_tracks(artist.id)
                    logger.info(f"🎵 {len(artist.tracks)} morceaux chargés pour {artist.name}")
                except Exception as tracks_error:
                    logger.error(f"⚠️ Erreur chargement tracks: {tracks_error}")
                    artist.tracks = []
                
                return artist
                
        except Exception as e:
            logger.error(f"❌ Erreur dans get_artist_by_name: {e}")
            return None
    
    def get_artist_tracks(self, artist_id: int) -> List[Track]:
        """Récupère tous les morceaux d'un artiste - VERSION SANS YOUTUBE_URL"""
        tracks = []
        
        try:
            logger.info(f"🔍 Chargement des tracks pour artist_id: {artist_id}")
            
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # ✅ ÉTAPE 1: Récupérer d'abord les infos de l'artiste
                cursor.execute("SELECT id, name, genius_id, spotify_id, discogs_id FROM artists WHERE id = ?", (artist_id,))
                artist_row = cursor.fetchone()
                
                if not artist_row:
                    logger.error(f"❌ Artiste avec ID {artist_id} non trouvé")
                    return tracks
                
                # ✅ ÉTAPE 2: Créer l'objet Artist
                from src.models import Artist
                artist = Artist(
                    id=artist_row[0],
                    name=artist_row[1], 
                    genius_id=artist_row[2],
                    spotify_id=artist_row[3],
                    discogs_id=artist_row[4]
                )

                # Vérifier le nombre total
                cursor.execute("SELECT COUNT(*) FROM tracks WHERE artist_id = ?", (artist_id,))
                total_count = cursor.fetchone()[0]
                logger.info(f"📊 {total_count} tracks trouvés en base")
                
                if total_count == 0:
                    return tracks
                
                # CORRECTION: Supprimer youtube_url de la requête SELECT
                cursor.execute("""
                    SELECT id, title, album, track_number, release_date,
                        genius_id, spotify_id, discogs_id,
                        bpm, duration, genre, genius_url, spotify_url,
                        is_featuring, primary_artist_name, featured_artists,
                        lyrics, lyrics_scraped_at, has_lyrics,
                        created_at, updated_at, last_scraped
                    FROM tracks 
                    WHERE artist_id = ?
                    ORDER BY title
                """, (artist_id,))
                
                rows = cursor.fetchall()
                logger.info(f"📦 {len(rows)} lignes récupérées")
                
                # Création des objets Track
                for i, row in enumerate(rows):
                    try:
                        # Accès par index (indices ajustés sans youtube_url)
                        track_id = row[0]    # id
                        title = row[1]       # title
                        album = row[2]       # album
                        track_number = row[3] # track_number
                        release_date = row[4] # release_date
                        genius_id = row[5]   # genius_id
                        spotify_id = row[6]  # spotify_id
                        discogs_id = row[7]  # discogs_id
                        bpm = row[8]         # bpm
                        duration = row[9]    # duration
                        genre = row[10]      # genre
                        genius_url = row[11] # genius_url
                        spotify_url = row[12] # spotify_url
                        is_featuring = row[13] # is_featuring
                        primary_artist_name = row[14] # primary_artist_name
                        featured_artists = row[15] # featured_artists
                        lyrics = row[16]     # lyrics
                        lyrics_scraped_at = row[17] # lyrics_scraped_at
                        has_lyrics = row[18] # has_lyrics
                        created_at = row[19] # created_at
                        updated_at = row[20] # updated_at
                        last_scraped = row[21] # last_scraped
                        
                        # Validation
                        if not track_id or not title:
                            continue
                        
                        if str(title).strip() in ['', 'None', 'NULL']:
                            continue
                        
                        # Création Track
                        from src.models import Track
                        track = Track(
                            id=track_id,
                            title=str(title).strip()
                        )
                        
                        # Assigner Artiste
                        track.artist = artist

                        # Assignation sécurisée
                        def safe_assign(value, default=None):
                            if value is None or str(value) in ['None', 'NULL', '']:
                                return default
                            return value
                        
                        track.album = safe_assign(album)
                        track.track_number = safe_assign(track_number)
                        track.release_date = safe_assign(release_date)
                        track.genius_id = safe_assign(genius_id)
                        track.spotify_id = safe_assign(spotify_id)
                        track.discogs_id = safe_assign(discogs_id)
                        track.bpm = safe_assign(bpm)
                        track.duration = safe_assign(duration)
                        track.genre = safe_assign(genre)
                        track.genius_url = safe_assign(genius_url)
                        track.spotify_url = safe_assign(spotify_url)
                        track.created_at = safe_assign(created_at)
                        track.updated_at = safe_assign(updated_at)
                        track.last_scraped = safe_assign(last_scraped)
                        
                        # Propriétés featuring
                        track.is_featuring = bool(safe_assign(is_featuring, False))
                        track.primary_artist_name = safe_assign(primary_artist_name)
                        track.featured_artists = safe_assign(featured_artists)
                        
                        # Propriétés paroles
                        track.lyrics = safe_assign(lyrics)
                        track.has_lyrics = bool(safe_assign(has_lyrics, False))
                        track.lyrics_scraped_at = safe_assign(lyrics_scraped_at)
                        
                        # Chargement crédits
                        try:
                            track.credits = self._get_track_credits(cursor, track_id)
                        except Exception:
                            track.credits = []
                        
                        tracks.append(track)
                        
                        if i < 5:
                            logger.info(f"✅ Track {i+1}: {track.title}")
                    
                    except Exception as track_error:
                        logger.error(f"❌ Erreur track {i}: {track_error}")
                        continue
                
                logger.info(f"✅ {len(tracks)} tracks chargés avec succès")
                
        except Exception as e:
            logger.error(f"❌ Erreur dans get_artist_tracks: {e}")
        
        return tracks
    
    def _safe_get(self, row, column_name: str, available_columns: list, default=None):
        """Accès sécurisé à une colonne de la base de données"""
        try:
            if column_name in available_columns:
                if hasattr(row, 'keys') and callable(row.keys):
                    # sqlite3.Row
                    return row[column_name]
                elif hasattr(row, '__getitem__'):
                    # Tuple/List - utiliser l'index
                    column_index = available_columns.index(column_name)
                    if column_index < len(row):
                        return row[column_index]
                    else:
                        return default
                else:
                    return default
            else:
                return default
        except Exception as e:
            logger.debug(f"⚠️ Erreur _safe_get pour {column_name}: {e}")
            return default
    
    def _get_track_credits(self, cursor, track_id: int) -> List[Credit]:
        """Récupère les crédits d'un morceau - VERSION ROBUSTE"""
        credits = []
        
        try:
            cursor.execute("SELECT * FROM credits WHERE track_id = ?", (track_id,))
            credit_rows = cursor.fetchall()
            
            for row in credit_rows:
                try:
                    # Accès par index aussi pour les crédits
                    if len(row) >= 6:  # S'assurer qu'on a assez de colonnes
                        name = row[2] if len(row) > 2 else None
                        role_str = row[3] if len(row) > 3 else None
                        role_detail = row[4] if len(row) > 4 else None
                        source = row[5] if len(row) > 5 else "genius"
                        
                        if name and role_str:
                            from src.models import Credit, CreditRole
                            
                            # Conversion du rôle string vers enum
                            try:
                                role = CreditRole(role_str)
                            except ValueError:
                                role = CreditRole.OTHER
                            
                            credit = Credit(
                                name=str(name),
                                role=role,
                                role_detail=role_detail,
                                source=str(source)
                            )
                            credits.append(credit)
                            
                except Exception as credit_error:
                    logger.debug(f"Erreur crédit: {credit_error}")
                    continue
                    
        except Exception as e:
            logger.debug(f"Erreur _get_track_credits: {e}")
        
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
        
    def force_update_track_credits(self, track: Track) -> int:
        """Force la mise à jour complète des crédits d'un morceau - VERSION PRÉSERVANT FEATURES"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # ✅ CORRECTION: Récupérer les infos featuring AVANT suppression
                cursor.execute("""
                    SELECT is_featuring, primary_artist_name, featured_artists 
                    FROM tracks WHERE id = ?
                """, (track.id,))
                
                featuring_info = cursor.fetchone()
                
                if featuring_info:
                    # Préserver les infos featuring sur l'objet track
                    track.is_featuring = bool(featuring_info[0]) if featuring_info[0] else False
                    track.primary_artist_name = featuring_info[1]
                    track.featured_artists = featuring_info[2]
                    logger.info(f"🔒 Infos featuring préservées pour {track.title}")
                else:
                    track.is_featuring = False
                
                # Supprimer TOUS les anciens crédits
                cursor.execute("DELETE FROM credits WHERE track_id = ?", (track.id,))
                deleted_count = cursor.rowcount
                logger.info(f"🗑️ {deleted_count} anciens crédits supprimés pour '{track.title}'")
                
                # Supprimer les anciennes erreurs de scraping
                cursor.execute("DELETE FROM scraping_errors WHERE track_id = ?", (track.id,))
                deleted_errors = cursor.rowcount
                if deleted_errors > 0:
                    logger.info(f"🗑️ {deleted_errors} anciennes erreurs supprimées")
                
                # Remettre à zéro les métadonnées de scraping (MAIS PRÉSERVER FEATURING)
                cursor.execute("""
                    UPDATE tracks 
                    SET last_scraped = NULL,
                        genre = CASE 
                            WHEN genre IS NOT NULL AND genre != '' THEN genre 
                            ELSE NULL 
                        END
                    WHERE id = ?
                """, (track.id,))
                
                # Sauvegarder les nouveaux crédits
                for credit in track.credits:
                    self._save_credit(cursor, track.id, credit)
                
                # Mettre à jour le track complet (EN PRÉSERVANT LES FEATURES)
                cursor.execute("""
                    UPDATE tracks 
                    SET album = ?, track_number = ?, release_date = ?, 
                        genius_id = ?, spotify_id = ?, discogs_id = ?,
                        bpm = ?, duration = ?, genre = ?,
                        genius_url = ?, spotify_url = ?,
                        is_featuring = ?, primary_artist_name = ?, featured_artists = ?,
                        updated_at = ?, last_scraped = ?
                    WHERE id = ?
                """, (track.album, getattr(track, 'track_number', None), track.release_date,
                    track.genius_id, track.spotify_id, track.discogs_id,
                    track.bpm, track.duration, track.genre,
                    track.genius_url, track.spotify_url,
                    getattr(track, 'is_featuring', False),
                    getattr(track, 'primary_artist_name', None),
                    getattr(track, 'featured_artists', None),
                    datetime.now(), track.last_scraped, track.id))
                
                # Sauvegarder les nouvelles erreurs s'il y en a
                for error in track.scraping_errors:
                    cursor.execute("""
                        INSERT INTO scraping_errors (track_id, error_message, error_time)
                        VALUES (?, ?, ?)
                    """, (track.id, error, datetime.now()))
                
                conn.commit()
                
                new_credits_count = len(track.credits)
                logger.info(f"✅ Mise à jour forcée terminée pour '{track.title}': {new_credits_count} nouveaux crédits (Featuring préservé: {getattr(track, 'is_featuring', False)})")
                
                return new_credits_count
                
        except Exception as e:
            logger.error(f"❌ Erreur lors de la mise à jour forcée: {e}")
            return 0

    def force_update_multiple_tracks(self, tracks: List[Track], progress_callback=None) -> Dict[str, int]:
        """Force la mise à jour de plusieurs morceaux"""
        results = {
            'updated': 0,
            'failed': 0,
            'total_credits_before': 0,
            'total_credits_after': 0
        }
        
        total = len(tracks)
        
        for i, track in enumerate(tracks):
            try:
                # Compter les crédits avant
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM credits WHERE track_id = ?", (track.id,))
                    credits_before = cursor.fetchone()[0]
                    results['total_credits_before'] += credits_before
                
                # Mise à jour forcée
                new_credits = self.force_update_track_credits(track)
                
                if new_credits >= 0:  # Succès (même si 0 crédits)
                    results['updated'] += 1
                    results['total_credits_after'] += new_credits
                else:
                    results['failed'] += 1
                
                if progress_callback:
                    progress_callback(i + 1, total, f"Mise à jour: {track.title}")
                    
            except Exception as e:
                logger.error(f"❌ Erreur sur {track.title}: {e}")
                results['failed'] += 1
        
        logger.info(f"🔄 Mise à jour forcée terminée: {results['updated']} réussis, {results['failed']} échoués")
        logger.info(f"📊 Crédits: {results['total_credits_before']} → {results['total_credits_after']}")
        
        return results
    
    def diagnose_tracks_database(self, artist_id: int) -> Dict[str, Any]:
        """Diagnostique les problèmes dans la base de données pour un artiste"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                diagnosis = {
                    'total_tracks': 0,
                    'valid_tracks': 0,
                    'invalid_titles': [],
                    'missing_data': {
                        'no_credits': 0,
                        'no_lyrics': 0,
                        'no_bpm': 0
                    }
                }
                
                # Compter le total
                cursor.execute("SELECT COUNT(*) FROM tracks WHERE artist_id = ?", (artist_id,))
                diagnosis['total_tracks'] = cursor.fetchone()[0]
                
                # Analyser chaque track
                cursor.execute("""
                    SELECT id, title, bpm, 
                        (SELECT COUNT(*) FROM credits WHERE track_id = tracks.id) as credits_count,
                        CASE WHEN lyrics IS NULL OR lyrics = '' OR lyrics = 'None' THEN 0 ELSE 1 END as has_lyrics
                    FROM tracks 
                    WHERE artist_id = ?
                """, (artist_id,))
                
                for row in cursor.fetchall():
                    track_id, title, bpm, credits_count, has_lyrics = row
                    
                    # Vérifier le titre
                    if not title or str(title).strip() in ['', 'None', 'NULL']:
                        diagnosis['invalid_titles'].append({
                            'id': track_id,
                            'title': title
                        })
                    else:
                        diagnosis['valid_tracks'] += 1
                        
                        # Compter les données manquantes
                        if credits_count == 0:
                            diagnosis['missing_data']['no_credits'] += 1
                        if not has_lyrics:
                            diagnosis['missing_data']['no_lyrics'] += 1
                        if not bpm or bpm <= 0:
                            diagnosis['missing_data']['no_bpm'] += 1
                
                return diagnosis
                
        except Exception as e:
            logger.error(f"Erreur diagnostic: {e}")
            return None