"""Modèles pour représenter les morceaux et crédits"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime

from enum import Enum


class CreditRole(Enum):
    """Types de rôles dans les crédits"""
    # Rôles d'écriture
    WRITER = "Writer"
    COMPOSER = "Composer"
    LYRICIST = "Lyricist"
    TRANSLATOR = "Translator"
    
    # Rôles de production musicale
    PRODUCER = "Producer"
    CO_PRODUCER = "Co-Producer"
    EXECUTIVE_PRODUCER = "Executive Producer"
    VOCAL_PRODUCER = "Vocal Producer"
    ADDITIONAL_PRODUCTION = "Additional Production"
    PROGRAMMER = "Programmer"
    DRUM_PROGRAMMER = "Drum Programmer"
    ARRANGER = "Arranger"
    
    # Rôles studio
    MIXING_ENGINEER = "Mixing Engineer"
    MASTERING_ENGINEER = "Mastering Engineer"
    RECORDING_ENGINEER = "Recording Engineer"
    ENGINEER = "Engineer"
    ASSISTANT_MIXING_ENGINEER = "Assistant Mixing Engineer"
    ASSISTANT_MASTERING_ENGINEER = "Assistant Mastering Engineer"
    ASSISTANT_RECORDING_ENGINEER = "Assistant Recording Engineer"
    ASSISTANT_ENGINEER = "Assistant Engineer"
    STUDIO_PERSONNEL = "Studio Personnel"
    ADDITIONAL_MIXING = "Additional Mixing"
    ADDITIONAL_MASTERING = "Additional Mastering"
    ADDITIONAL_RECORDING = "Additional Recording"
    ADDITIONAL_ENGINEERING = "Additional Engineering"
    PREPARER = "Preparer"
    
    # Rôles liés au chant
    VOCALS = "Vocals"
    LEAD_VOCALS = "Lead Vocals"
    BACKGROUND_VOCALS = "Background Vocals"
    ADDITIONAL_VOCALS = "Additional Vocals"
    CHOIR = "Choir"
    AD_LIBS = "Ad-Libs"
    
    # Label / Rôles liés à l'édition
    LABEL = "Label"
    PUBLISHER = "Publisher"
    DISTRIBUTOR = "Distributor"
    COPYRIGHT = "Copyright ©"
    PHONOGRAPHIC_COPYRIGHT = "Phonographic Copyright ℗"
    MANUFACTURER = "Manufacturer"
    
    # Rôles liés aux instruments
    GUITAR = "Guitar"
    BASS_GUITAR = "Bass Guitar"
    ACOUSTIC_GUITAR = "Acoustic Guitar"
    ELECTRIC_GUITAR = "Electric Guitar"
    RHYTHM_GUITAR = "Rhythm Guitar"
    CELLO = "Cello"
    DRUMS = "Drums"
    BASS = "Bass"
    KEYBOARD = "Keyboard"
    PERCUSSION = "Percussion"
    PIANO = "Piano"
    VIOLIN = "Violin"
    ORGAN = "Organ"
    SYNTHESIZER = "Synthesizer"
    STRINGS = "Strings"
    TRUMPET = "Trumpet"
    VIOLA = "Viola"
    SAXOPHONE = "Saxophone"
    TROMBONE = "Trombone"
    SCRATCHES = "Scratches"
    INSTRUMENTATION = "Instrumentation"
    
    # Lieux
    RECORDED_AT = "Recorded At"
    MASTERED_AT = "Mastered At"
    MIXED_AT = "Mixed At"
    
    # Crédits pour la jaquette
    ARTWORK = "Artwork"
    ART_DIRECTION = "Art Direction"
    GRAPHIC_DESIGN = "Graphic Design"
    ILLUSTRATION = "Illustration"
    LAYOUT = "Layout"
    PHOTOGRAPHY = "Photography"
    
    # Crédits vidéo
    VIDEO_DIRECTOR = "Video Director"
    VIDEO_PRODUCER = "Video Producer"
    VIDEO_DIRECTOR_OF_PHOTOGRAPHY = "Video Director of Photography"
    VIDEO_CINEMATOGRAPHER = "Video Cinematographer"
    VIDEO_DIGITAL_IMAGING_TECHNICIAN = "Video Digital Imaging Technician"
    VIDEO_CAMERA_OPERATOR = "Video Camera Operator"
    VIDEO_DRONE_OPERATOR = "Video Drone Operator"
    VIDEO_SET_DECORATOR = "Video Set Decorator"
    VIDEO_EDITOR = "Video Editor"
    VIDEO_COLORIST = "Video Colorist"
    
    # Rôles liés à l'album
    A_AND_R = "A&R"
    
    # Autres
    FEATURED = "Featured Artist"
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
    
    @staticmethod
    def from_role_and_names(role: str, names: List[str]) -> List["Credit"]:
        """Crée une liste de crédits à partir d'un rôle (texte) et de noms"""
        credits = []
        
        # Mapper le rôle texte vers l'enum
        try:
            # Essayer de trouver le rôle exact
            credit_role = None
            for role_enum in CreditRole:
                if role_enum.value.lower() == role.lower():
                    credit_role = role_enum
                    break
            
            if not credit_role:
                # Si pas trouvé, utiliser OTHER
                credit_role = CreditRole.OTHER
                
        except (ValueError, AttributeError):
            credit_role = CreditRole.OTHER

        # Créer un crédit pour chaque nom
        for name in names:
            name = name.strip()
            if name:  # S'assurer que le nom n'est pas vide
                credit = Credit(
                    name=name,
                    role=credit_role,
                    role_detail=role if credit_role == CreditRole.OTHER else None,
                    source="genius"
                )
                credits.append(credit)
        
        return credits


@dataclass
class Track:
    """Représente un morceau musical"""
    id: Optional[int] = None
    title: str = ""
    artist: Optional['Artist'] = None
    album: Optional[str] = None
    release_date: Optional[datetime] = None

    # Champs internes de marquage
    _album_from_api: bool = field(default=False, repr=False)
    _release_date_from_api: bool = field(default=False, repr=False)
    
    # IDs externes
    genius_id: Optional[int] = None
    spotify_id: Optional[str] = None
    discogs_id: Optional[int] = None
    
    # Métadonnées
    bpm: Optional[int] = None
    duration: Optional[int] = None  # En secondes
    genre: Optional[str] = None
    track_number: Optional[int] = None  # Numéro de piste
    
    # Support des features
    is_featuring: bool = False  # True si l'artiste est en featuring
    featured_artists: Optional[str] = None  # Liste des artistes en featuring
    primary_artist_name: Optional[str] = None  # Nom de l'artiste principal si différent

    # Support des paroles
    lyrics: Optional[str] = None  # Paroles complètes
    has_lyrics: bool = False  # Indicateur si les paroles sont disponibles
    lyrics_scraped_at: Optional[datetime] = None  # Date de récupération des paroles
    
    # Métadonnées supplémentaires
    popularity: Optional[int] = None  # Nombre de vues sur Genius
    artwork_url: Optional[str] = None  # URL de la pochette
    
    # Crédits
    credits: List[Credit] = field(default_factory=list)
    
    # URLs
    genius_url: Optional[str] = None
    spotify_url: Optional[str] = None
    youtube_url: Optional[str] = None
    
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
        """Retourne tous les crédits d'un rôle spécifique - VERSION ROBUSTE"""
        try:
            if not hasattr(self, 'credits') or not self.credits:
                return []
                
            matching_credits = []
            for credit in self.credits:
                try:
                    if hasattr(credit, 'role') and credit.role == role:
                        matching_credits.append(credit)
                except Exception:
                    continue
                    
            return matching_credits
            
        except Exception as e:
            logger.debug(f"Erreur get_credits_by_role: {e}")
            return []
    
    def get_producers(self) -> List[str]:
        """Retourne la liste des producteurs (tous types confondus) - VERSION ROBUSTE"""
        try:
            if not hasattr(self, 'credits') or not self.credits:
                return []
                
            producer_roles = [
                CreditRole.PRODUCER,
                CreditRole.CO_PRODUCER,
                CreditRole.EXECUTIVE_PRODUCER,
                CreditRole.VOCAL_PRODUCER,
                CreditRole.ADDITIONAL_PRODUCTION
            ]
            
            producers = []
            for role in producer_roles:
                try:
                    role_credits = self.get_credits_by_role(role)
                    producers.extend([c.name for c in role_credits if hasattr(c, 'name')])
                except Exception:
                    continue
                    
            return producers
            
        except Exception as e:
            logger.debug(f"Erreur get_producers: {e}")
            return []

    def get_writers(self) -> List[str]:
        """Retourne la liste des auteurs (tous types confondus) - VERSION ROBUSTE"""
        try:
            if not hasattr(self, 'credits') or not self.credits:
                return []
                
            writer_roles = [
                CreditRole.WRITER,
                CreditRole.COMPOSER,
                CreditRole.LYRICIST
            ]
            
            writers = []
            for role in writer_roles:
                try:
                    role_credits = self.get_credits_by_role(role)
                    writers.extend([c.name for c in role_credits if hasattr(c, 'name')])
                except Exception:
                    continue

            return writers
        
        except Exception as e:
            logger.debug(f"Erreur get_writers: {e}")
            return []
        
    @property
    def producers(self):
        """Propriété pour la compatibilité avec l'interface - retourne get_producers()"""
        return self.get_producers()
    
    @property
    def writers(self):
        """Propriété pour la compatibilité avec l'interface - retourne get_writers()"""
        return self.get_writers()
    
    @property
    def featured_artists_list(self):
        """Retourne la liste des featured artists depuis les crédits ou le champ featured_artists"""
        # D'abord essayer le champ featured_artists (string)
        if hasattr(self, 'featured_artists') and self.featured_artists:
            # Si c'est une string avec des virgules, la splitter
            if isinstance(self.featured_artists, str):
                return [a.strip() for a in self.featured_artists.split(',') if a.strip()]
            return self.featured_artists
        
        # Sinon, extraire depuis les crédits
        try:
            featured_credits = self.get_credits_by_role(CreditRole.FEATURED)
            return [c.name for c in featured_credits if hasattr(c, 'name')]
        except Exception:
            return []
    
    @property
    def credits_scraped(self):
        """Retourne le nombre de crédits au lieu d'un booléen"""
        try:
            if hasattr(self, 'credits') and self.credits:
                return len(self.credits)
            return 0
        except Exception:
            return 0

    def has_complete_credits(self) -> bool:
        """Vérifie si les crédits semblent complets"""
        try:
            # CORRECTION 1: Obtenir les crédits musicaux de manière sécurisée
            try:
                music_credits = self.get_music_credits()
            except Exception:
                music_credits = getattr(self, 'credits', [])
            
            if not music_credits:
                return False
            
            # CORRECTION 2: Obtenir les producteurs et auteurs de manière sécurisée
            try:
                producers = self.get_producers()
            except Exception:
                producers = []
                
            try:
                writers = self.get_writers()
            except Exception:
                writers = []
            
            # Un morceau est considéré comme complet s'il a :
            # - Au moins 2 crédits musicaux (plus strict)
            # - ET au moins un producteur OU un auteur
            
            has_producer = bool(producers)
            has_writer = bool(writers)
            has_enough_credits = len(music_credits) >= 2
            
            return has_enough_credits and (has_producer or has_writer)
            
        except Exception as e:
            logger.error(f"Erreur dans has_complete_credits pour {getattr(self, 'title', 'track inconnu')}: {e}")
            return False
    
    def get_music_credits(self) -> List[Credit]:
        """Retourne seulement les crédits musicaux - VERSION CORRIGÉE ROBUSTE"""
        try:
            # CORRECTION 1: Vérification de l'existence des crédits
            if not hasattr(self, 'credits') or not self.credits:
                return []
            
            # CORRECTION 2: Obtenir les crédits vidéo de manière sécurisée
            try:
                video_credits = self.get_video_credits()
            except Exception as video_error:
                logger.debug(f"Erreur get_video_credits: {video_error}")
                video_credits = []
            
            # CORRECTION 3: Créer les identifiants uniques de manière robuste
            video_credit_ids = set()
            for credit in video_credits:
                try:
                    if hasattr(credit, 'name') and hasattr(credit, 'role'):
                        credit_id = (
                            str(credit.name).strip(),
                            str(credit.role.value) if hasattr(credit.role, 'value') else str(credit.role),
                            str(getattr(credit, 'role_detail', '')) if hasattr(credit, 'role_detail') else '',
                            str(getattr(credit, 'source', '')) if hasattr(credit, 'source') else ''
                        )
                        video_credit_ids.add(credit_id)
                except Exception as id_error:
                    logger.debug(f"Erreur création ID crédit vidéo: {id_error}")
                    continue
            
            # CORRECTION 4: Filtrer les crédits musicaux de manière robuste
            music_credits = []
            for credit in self.credits:
                try:
                    if hasattr(credit, 'name') and hasattr(credit, 'role'):
                        credit_id = (
                            str(credit.name).strip(),
                            str(credit.role.value) if hasattr(credit.role, 'value') else str(credit.role),
                            str(getattr(credit, 'role_detail', '')) if hasattr(credit, 'role_detail') else '',
                            str(getattr(credit, 'source', '')) if hasattr(credit, 'source') else ''
                        )
                        
                        # Si ce n'est pas un crédit vidéo, c'est un crédit musical
                        if credit_id not in video_credit_ids:
                            music_credits.append(credit)
                            
                except Exception as credit_error:
                    logger.debug(f"Erreur traitement crédit musical: {credit_error}")
                    # En cas d'erreur, considérer comme musical par défaut
                    music_credits.append(credit)
                    continue
            
            return music_credits
        
        except Exception as e:
            logger.error(f"Erreur générale dans get_music_credits pour {getattr(self, 'title', 'track inconnu')}: {e}")
            # En cas d'erreur totale, retourner tous les crédits
            return getattr(self, 'credits', [])
    
    def get_video_credits(self) -> List[Credit]:
        """Retourne seulement les crédits vidéo - VERSION CORRIGÉE ROBUSTE"""
        try:
            # CORRECTION 1: Vérification de l'existence des crédits
            if not hasattr(self, 'credits') or not self.credits:
                return []
            
            video_credits = []
            
            # CORRECTION 2: Liste des rôles vidéo avec gestion d'erreur
            try:
                video_roles = [
                    CreditRole.VIDEO_DIRECTOR,
                    CreditRole.VIDEO_PRODUCER,
                    CreditRole.VIDEO_DIRECTOR_OF_PHOTOGRAPHY,
                    CreditRole.VIDEO_CINEMATOGRAPHER,
                    CreditRole.VIDEO_DIGITAL_IMAGING_TECHNICIAN,
                    CreditRole.VIDEO_CAMERA_OPERATOR,
                    CreditRole.VIDEO_DRONE_OPERATOR,
                    CreditRole.VIDEO_SET_DECORATOR,
                    CreditRole.VIDEO_EDITOR,
                    CreditRole.VIDEO_COLORIST,
                    CreditRole.PHOTOGRAPHY  # Considéré comme vidéo
                ]
            except Exception:
                # Si CreditRole n'est pas accessible, utiliser des strings
                video_roles = []
            
            # CORRECTION 3: Filtrer par rôles vidéo explicites
            for credit in self.credits:
                try:
                    if hasattr(credit, 'role') and credit.role in video_roles:
                        video_credits.append(credit)
                except Exception:
                    continue
            
            # CORRECTION 4: Vérifier les rôles OTHER avec mots-clés vidéo
            video_keywords = [
                'video', 'vidéo', 'clip', 'director', 'réalisateur', 'cinematographer',
                'camera', 'caméra', 'drone', 'steadicam', 'gimbal', 'electrician', 
                'électricien', 'lighting', 'éclairage', 'gaffer', 'grip', 'focus puller',
                'makeup artist', 'maquilleur', 'maquilleuse', 'hair', 'coiffeur',
                'costume', 'wardrobe', 'styliste', 'styling', 'editor', 'monteur',
                'monteuse', 'colorist', 'étalonnage', 'motion graphics', 'vfx',
                'visual effects', 'effets visuels', 'set decorator', 'décorateur',
                'props', 'accessoires', 'location', 'repérage', 'casting director',
                'video producer', 'production manager', 'assistant director',
                'script supervisor', 'continuity'
            ]
            
            # Exclusions pour éviter les faux positifs
            music_exclusions = [
                'songwriter', 'composer', 'producer', 'mix', 'master',
                'guitar', 'piano', 'drums', 'bass', 'vocal', 'engineer'
            ]
            
            for credit in self.credits:
                try:
                    if (hasattr(credit, 'role') and 
                        hasattr(credit.role, 'value') and 
                        str(credit.role.value).lower() == 'other' and
                        hasattr(credit, 'role_detail') and 
                        credit.role_detail):
                        
                        role_detail_lower = str(credit.role_detail).lower()
                        
                        # Vérifier si c'est un rôle vidéo
                        is_video = any(keyword in role_detail_lower for keyword in video_keywords)
                        is_music = any(exclusion in role_detail_lower for exclusion in music_exclusions)
                        
                        if is_video and not is_music and credit not in video_credits:
                            video_credits.append(credit)
                            
                except Exception:
                    continue
            
            return video_credits
            
        except Exception as e:
            logger.error(f"Erreur générale dans get_video_credits pour {getattr(self, 'title', 'track inconnu')}: {e}")
            return []
    
    def get_display_title(self) -> str:
        """Retourne le titre à afficher (avec indication featuring si applicable)"""
        if hasattr(self, 'is_featuring') and self.is_featuring:
            # Pour les features : garder le titre original (il contient déjà "feat.")
            return self.title
        return self.title
    
    def get_display_artist(self) -> str:
        """Retourne l'artiste à afficher (principal si featuring)"""
        if hasattr(self, 'is_featuring') and self.is_featuring:
            # Pour les features : retourner l'artiste principal si disponible
            if hasattr(self, 'primary_artist_name') and self.primary_artist_name:
                return self.primary_artist_name
            # Sinon, extraire l'artiste principal du titre s'il contient "feat."
            if " feat. " in self.title:
                # Le titre est probablement "ArtistePrincipal - Titre feat. ArtisteCherché"
                parts = self.title.split(" feat. ")
                if len(parts) > 1:
                    # Extraire l'artiste principal du début
                    artist_and_title = parts[0]
                    if " - " in artist_and_title:
                        return artist_and_title.split(" - ")[0].strip()
            return "Artiste principal inconnu"
        
        # Pour les morceaux principaux
        return self.artist.name if self.artist else "Unknown"
    
    def is_main_track(self) -> bool:
        """Retourne True si c'est un morceau principal (pas un featuring)"""
        return not (hasattr(self, 'is_featuring') and self.is_featuring)
    
    def to_dict(self) -> dict:
        """Convertit le morceau en dictionnaire - VERSION AVEC SÉPARATION VIDÉO ET PAROLES"""
        is_featuring = hasattr(self, 'is_featuring') and self.is_featuring
        
        music_credits = self.get_music_credits()
        video_credits = self.get_video_credits()
        
        # ✅ NOUVEAU: Informations sur les paroles
        lyrics_info = {}
        if hasattr(self, 'lyrics') and self.lyrics:
            lyrics_info = {
                'has_lyrics': True,
                'lyrics_word_count': len(self.lyrics.split()) if self.lyrics else 0,
                'lyrics_char_count': len(self.lyrics) if self.lyrics else 0,
                'lyrics_scraped_at': self.lyrics_scraped_at.isoformat() if self.lyrics_scraped_at else None
            }
        else:
            lyrics_info = {
                'has_lyrics': False,
                'lyrics_word_count': 0,
                'lyrics_char_count': 0,
                'lyrics_scraped_at': None
            }
        
        return {
            'id': self.id,
            'title': self.title,
            'display_title': self.get_display_title(),
            'artist': self.artist.name if self.artist else None,
            'display_artist': self.get_display_artist(),
            'album': self.album,
            'track_number': getattr(self, 'track_number', None),
            'release_date': self.release_date.isoformat() if self.release_date else None,
            'genius_id': self.genius_id,
            'spotify_id': self.spotify_id,
            'discogs_id': self.discogs_id,
            'bpm': self.bpm,
            'duration': self.duration,
            'genre': self.genre,
            'is_featuring': is_featuring,
            'featured_artists': getattr(self, 'featured_artists', None),
            'primary_artist_name': getattr(self, 'primary_artist_name', None),
            'popularity': getattr(self, 'popularity', None),
            'artwork_url': getattr(self, 'artwork_url', None),
            
            # ✅ NOUVEAU: Informations paroles
            **lyrics_info,
            
            # ✅ SÉPARATION DES CRÉDITS
            'music_credits': [c.to_dict() for c in music_credits],
            'video_credits': [c.to_dict() for c in video_credits],
            'all_credits': [c.to_dict() for c in self.credits],  # Garde la compatibilité
            
            # Statistiques
            'music_credits_count': len(music_credits),
            'video_credits_count': len(video_credits),
            'total_credits_count': len(self.credits),
            'has_complete_credits': self.has_complete_credits(),
            
            'genius_url': self.genius_url,
            'spotify_url': self.spotify_url,
            'youtube_url': self.youtube_url,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'last_scraped': self.last_scraped.isoformat() if self.last_scraped else None,
            'scraping_errors': self.scraping_errors
        }

    def _start_lyrics_scraping(self):
        """Lance le scraping des paroles pour les morceaux sélectionnés"""
        if not self.current_artist or not self.current_artist.tracks:
            return
        
        if not self.selected_tracks:
            messagebox.showwarning("Attention", "Aucun morceau sélectionné")
            return
        
        # Filtrer les morceaux sélectionnés
        selected_tracks_list = [self.current_artist.tracks[i] for i in sorted(self.selected_tracks)]
        
        # Confirmation
        result = messagebox.askyesno(
            "Scraping des paroles",
            f"Voulez-vous scraper les paroles de {len(selected_tracks_list)} morceaux sélectionnés ?\n\n"
            "📝 Cela récupérera :\n"
            "• Les paroles complètes\n"
            "• L'analyse de structure (intro, couplets, refrain...)\n"
            "• Estimation de durée par section\n\n"
            "⏱️ Temps estimé : ~{} minutes".format(len(selected_tracks_list) * 0.5)
        )
        
        if not result:
            return
        
        self.lyrics_button.configure(state="disabled", text="📝 Scraping paroles...")
        self.progress_bar.set(0)
        
        def update_progress(current, total, track_name):
            progress = current / total
            self.root.after(0, lambda: self.progress_var.set(progress))
            self.root.after(0, lambda: self.progress_label.configure(
                text=f"📝 {current}/{total} - {track_name[:25]}..."
            ))
        
        def scrape_lyrics():
            scraper = None
            try:
                scraper = GeniusScraper(headless=True)
                results = scraper.scrape_multiple_tracks_with_lyrics(
                    selected_tracks_list,
                    progress_callback=update_progress,
                    include_lyrics=True
                )
                
                # Sauvegarder les données avec paroles
                for track in selected_tracks_list:
                    track.artist = self.current_artist
                    self.data_manager.save_track(track)
                
                # Afficher le résumé
                self.root.after(0, lambda: messagebox.showinfo(
                    "📝 Paroles récupérées",
                    f"✅ Scraping des paroles terminé !\n\n"
                    f"📊 Résultats :\n"
                    f"• Morceaux traités : {results['success']}\n"
                    f"• Paroles récupérées : {results['lyrics_scraped']}\n"
                    f"• Structures analysées : {results['structures_analyzed']}\n"
                    f"• Échecs : {results['failed']}\n\n"
                    f"💡 Les paroles sont maintenant disponibles dans les détails des morceaux"
                ))
                
                self.root.after(0, self._update_artist_info)
                
            except Exception as err:
                error_msg = str(err) if str(err) != "None" else "Erreur inconnue"
                logger.error(f"Erreur scraping paroles: {error_msg}", exc_info=True)
                self.root.after(0, lambda: messagebox.showerror(
                    "Erreur",
                    f"Erreur lors du scraping des paroles :\n{error_msg}"
                ))
            finally:
                if scraper:
                    try:
                        scraper.close()
                    except:
                        pass
                
                self.root.after(0, lambda: self.lyrics_button.configure(
                    state="normal",
                    text="📝 Scraper paroles"
                ))
                self.root.after(0, lambda: self.progress_bar.set(0))
                self.root.after(0, lambda: self.progress_label.configure(text=""))
        
        threading.Thread(target=scrape_lyrics, daemon=True).start()