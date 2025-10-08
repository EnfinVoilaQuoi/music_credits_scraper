"""Gestionnaire de sauvegarde et chargement des données"""
import json
import sqlite3
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime
from contextlib import contextmanager

from src.config import DATABASE_URL, ARTISTS_DIR, DATA_DIR
from src.models import Artist, Track, Credit, CreditRole
from src.utils.logger import get_logger


logger = get_logger(__name__)


class DataManager:
    """Gère la persistance des données"""
    
    def __init__(self):
        self.db_path = DATABASE_URL.replace("sqlite:///", "")
        self._init_database()
        # Import tardif pour éviter la circularité
        try:
            from src.utils.certification_enricher import CertificationEnricher
            from src.api.snep_certifications import get_snep_manager
            
            self.certification_enricher = CertificationEnricher()
            self.snep_manager = get_snep_manager()
            self._initialize_certifications()
        except ImportError:
            # Si les modules ne sont pas encore créés
            self.certification_enricher = None
            self.snep_manager = None
    
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

            # Vérifier et ajouter les colonnes de certifications si elles n'existent pas
            cursor.execute("PRAGMA table_info(tracks)")
            existing_columns = {row[1] for row in cursor.fetchall()}

            # Liste des colonnes à ajouter
            new_columns = {
                'certifications': 'TEXT',  # JSON array
                'album_certifications': 'TEXT',  # JSON array
                'musical_key': 'TEXT',  # Musical key en français (ex: "Do majeur")
                'key': 'TEXT',  # Key brute (ex: "C", "G#/Ab")
                'mode': 'TEXT',  # Mode (ex: "major", "minor")
                'time_signature': 'TEXT',  # Signature rythmique (ex: "4/4")
                'anecdotes': 'TEXT',  # Anecdotes depuis Genius
                'spotify_page_title': 'TEXT'  # Titre de la page Spotify pour vérification
            }

            for col_name, col_type in new_columns.items():
                if col_name not in existing_columns:
                    try:
                        cursor.execute(f"ALTER TABLE tracks ADD COLUMN {col_name} {col_type}")
                        logger.info(f"✅ Colonne '{col_name}' ajoutée à la table tracks")
                    except Exception as e:
                        logger.debug(f"Colonne '{col_name}' déjà existante ou erreur: {e}")
            
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
    
    def _initialize_certifications(self):
        """Initialise la base de données des certifications au premier lancement"""
        try:
            # Vérifier si le CSV existe et l'importer - nom exact du fichier SNEP
            csv_path = Path(DATA_DIR) / 'certifications' / 'snep' / 'certif-.csv'
            if csv_path.exists():
                logger.info("🔄 Importation initiale des certifications SNEP...")
                success = self.snep_manager.import_from_csv(csv_path)
                if success:
                    logger.info("✅ Certifications SNEP importées avec succès")
                else:
                    logger.warning("⚠️ Problème lors de l'import des certifications")
        except Exception as e:
            logger.error(f"Erreur initialisation certifications: {e}")
    

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
        """Sauvegarde ou met à jour un morceau avec musical_key et time_signature"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            if not track.artist or not track.artist.id:
                raise ValueError("Le morceau doit avoir un artiste avec un ID")
            
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
                
                # Sérialiser les certifications en JSON
                certifications_json = json.dumps(getattr(track, 'certifications', [])) if hasattr(track, 'certifications') else '[]'
                album_certifications_json = json.dumps(getattr(track, 'album_certifications', [])) if hasattr(track, 'album_certifications') else '[]'

                # UPDATE avec key, mode, musical_key, time_signature, anecdotes et certifications
                cursor.execute("""
                    UPDATE tracks
                    SET album = ?, track_number = ?, release_date = ?,
                        genius_id = ?, spotify_id = ?, discogs_id = ?,
                        bpm = ?, duration = ?, genre = ?,
                        key = ?, mode = ?, musical_key = ?, time_signature = ?,
                        genius_url = ?, spotify_url = ?,
                        is_featuring = ?, primary_artist_name = ?, featured_artists = ?,
                        lyrics = ?, lyrics_scraped_at = ?, has_lyrics = ?, anecdotes = ?,
                        certifications = ?, album_certifications = ?,
                        updated_at = ?, last_scraped = ?
                    WHERE id = ?
                """, (track.album, getattr(track, 'track_number', None), track.release_date,
                    track.genius_id, track.spotify_id, track.discogs_id,
                    track.bpm, track.duration, track.genre,
                    getattr(track, 'key', None), getattr(track, 'mode', None),
                    getattr(track, 'musical_key', None), getattr(track, 'time_signature', None),
                    track.genius_url, track.spotify_url,
                    getattr(track, 'is_featuring', False),
                    getattr(track, 'primary_artist_name', None),
                    getattr(track, 'featured_artists', None),
                    getattr(track, 'lyrics', None),
                    getattr(track, 'lyrics_scraped_at', None),
                    bool(getattr(track, 'lyrics', None)),
                    getattr(track, 'anecdotes', None),
                    certifications_json, album_certifications_json,
                    datetime.now(), track.last_scraped, track.id))
            else:
                # Sérialiser les certifications en JSON
                certifications_json = json.dumps(getattr(track, 'certifications', [])) if hasattr(track, 'certifications') else '[]'
                album_certifications_json = json.dumps(getattr(track, 'album_certifications', [])) if hasattr(track, 'album_certifications') else '[]'

                # INSERT avec key, mode, musical_key, time_signature, anecdotes, certifications et spotify_page_title
                cursor.execute("""
                    INSERT INTO tracks (
                        title, artist_id, album, track_number, release_date,
                        genius_id, spotify_id, discogs_id,
                        bpm, duration, genre, key, mode, musical_key, time_signature,
                        genius_url, spotify_url,
                        is_featuring, primary_artist_name, featured_artists,
                        lyrics, lyrics_scraped_at, has_lyrics, anecdotes,
                        certifications, album_certifications, spotify_page_title,
                        created_at, updated_at, last_scraped
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (track.title, track.artist.id, track.album, getattr(track, 'track_number', None), track.release_date,
                    track.genius_id, track.spotify_id, track.discogs_id,
                    track.bpm, track.duration, track.genre,
                    getattr(track, 'key', None), getattr(track, 'mode', None),
                    getattr(track, 'musical_key', None), getattr(track, 'time_signature', None),
                    track.genius_url, track.spotify_url,
                    getattr(track, 'is_featuring', False),
                    getattr(track, 'primary_artist_name', None),
                    getattr(track, 'featured_artists', None),
                    getattr(track, 'lyrics', None),
                    getattr(track, 'lyrics_scraped_at', None),
                    bool(getattr(track, 'lyrics', None)),
                    getattr(track, 'anecdotes', None),
                    certifications_json, album_certifications_json,
                    getattr(track, 'spotify_page_title', None),
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
                
                # SELECT avec key, mode, certifications JSON, anecdotes et spotify_page_title
                cursor.execute("""
                    SELECT id, title, album, track_number, release_date,
                        genius_id, spotify_id, discogs_id,
                        bpm, duration, genre, key, mode, musical_key, time_signature,
                        genius_url, spotify_url,
                        is_featuring, primary_artist_name, featured_artists,
                        lyrics, lyrics_scraped_at, has_lyrics, anecdotes,
                        certifications, album_certifications, spotify_page_title,
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
                        # Accès par index (indices ajustés avec key et mode)
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
                        key = row[11]        # key  (NOUVEAU)
                        mode = row[12]       # mode (NOUVEAU)
                        musical_key = row[13] # musical_key
                        time_signature = row[14] # time_signature
                        genius_url = row[15] # genius_url
                        spotify_url = row[16] # spotify_url
                        is_featuring = row[17] # is_featuring
                        primary_artist_name = row[18] # primary_artist_name
                        featured_artists = row[19] # featured_artists
                        lyrics = row[20]     # lyrics
                        lyrics_scraped_at = row[21] # lyrics_scraped_at
                        has_lyrics = row[22] # has_lyrics
                        anecdotes = row[23]  # anecdotes
                        certifications_json = row[24] # certifications JSON
                        album_certifications_json = row[25] # album_certifications JSON
                        spotify_page_title = row[26] # spotify_page_title
                        created_at = row[27] # created_at
                        updated_at = row[28] # updated_at
                        last_scraped = row[29] # last_scraped
                        
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
                        track.key = safe_assign(key)  # NOUVEAU
                        track.mode = safe_assign(mode)  # NOUVEAU
                        track.musical_key = safe_assign(musical_key)
                        track.time_signature = safe_assign(time_signature)
                        track.genius_url = safe_assign(genius_url)
                        track.spotify_url = safe_assign(spotify_url)
                        track.spotify_page_title = safe_assign(spotify_page_title)
                        track.created_at = safe_assign(created_at)
                        track.updated_at = safe_assign(updated_at)
                        track.last_scraped = safe_assign(last_scraped)
                        
                        # Propriétés featuring
                        track.is_featuring = bool(safe_assign(is_featuring, False))
                        track.primary_artist_name = safe_assign(primary_artist_name)
                        track.featured_artists = safe_assign(featured_artists)
                        
                        # Propriétés paroles
                        track.lyrics = safe_assign(lyrics)
                        track.anecdotes = safe_assign(anecdotes)
                        track.has_lyrics = bool(safe_assign(has_lyrics, False))
                        track.lyrics_scraped_at = safe_assign(lyrics_scraped_at)

                        # Désérialiser les certifications JSON
                        try:
                            if certifications_json:
                                track.certifications = json.loads(certifications_json)
                                # Mettre à jour les champs de rétrocompatibilité
                                if track.certifications:
                                    highest = track.certifications[0]
                                    track.has_certification = True
                                    track.certification_level = highest.get('certification')
                                    track.certification_date = highest.get('certification_date')
                            else:
                                track.certifications = []
                        except:
                            track.certifications = []

                        try:
                            if album_certifications_json:
                                track.album_certifications = json.loads(album_certifications_json)
                            else:
                                track.album_certifications = []
                        except:
                            track.album_certifications = []
                        
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
                
                # Compter les tracks avec musical_key
                tracks_with_key = sum(1 for t in tracks if hasattr(t, 'musical_key') and t.musical_key)
                logger.info(f"✅ {len(tracks)} tracks chargés avec succès ({tracks_with_key} avec musical_key)")
                
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

    def get_statistics(self) -> Dict[str, Any]:
        """Retourne des statistiques sur la base de données"""
        try:
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

        except Exception as e:
            logger.error(f"Erreur lors de la récupération des statistiques: {e}")
            return {
                'total_artists': 0,
                'total_tracks': 0,
                'total_credits': 0,
                'tracks_with_complete_credits': 0,
                'recent_errors': 0
            }

