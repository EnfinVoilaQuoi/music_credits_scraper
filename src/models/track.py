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
    
    def to_dict(self) -> dict:
        """Convertit le crédit en dictionnaire"""
        return {
            'name': self.name,
            'role': self.role.value,
            'role_detail': self.role_detail,
            'source': self.source
        }
    
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
    
    # Métadonnées supplémentaires
    popularity: Optional[int] = None  # Nombre de vues sur Genius
    artwork_url: Optional[str] = None  # URL de la pochette
    
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
        """Retourne la liste des producteurs (tous types confondus)"""
        producer_roles = [
            CreditRole.PRODUCER,
            CreditRole.CO_PRODUCER,
            CreditRole.EXECUTIVE_PRODUCER,
            CreditRole.VOCAL_PRODUCER,
            CreditRole.ADDITIONAL_PRODUCTION
        ]
        producers = []
        for role in producer_roles:
            producers.extend([c.name for c in self.get_credits_by_role(role)])
        return producers
    
    def get_writers(self) -> List[str]:
        """Retourne la liste des auteurs (tous types confondus)"""
        writer_roles = [
            CreditRole.WRITER,
            CreditRole.COMPOSER,
            CreditRole.LYRICIST
        ]
        writers = []
        for role in writer_roles:
            writers.extend([c.name for c in self.get_credits_by_role(role)])
        return writers
    
    def has_complete_credits(self) -> bool:
        """Vérifie si les crédits semblent complets - VERSION AMÉLIORÉE"""
        music_credits = self.get_music_credits()
        
        if not music_credits:
            return False
        
        # Un morceau est considéré comme complet s'il a :
        # - Au moins 4 crédits musicaux (plus strict)
        # - ET au moins un producteur OU un auteur
        
        has_producer = bool(self.get_producers())
        has_writer = bool(self.get_writers())
        has_enough_credits = len(music_credits) >= 4
        
        return has_enough_credits and (has_producer or has_writer)
    
    def get_music_credits(self) -> List[Credit]:
        """Retourne seulement les crédits musicaux - VERSION AMÉLIORÉE"""
        # Obtenir d'abord tous les crédits vidéo
        video_credits = self.get_video_credits()
        video_credits_set = set(video_credits)
        
        # Retourner tous les crédits qui ne sont PAS vidéo
        music_credits = [c for c in self.credits if c not in video_credits_set]
        
        return music_credits
    
    def get_video_credits(self) -> List[Credit]:
        """Retourne seulement les crédits vidéo - VERSION AMÉLIORÉE"""
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
        
        video_credits = [c for c in self.credits if c.role in video_roles]
        
        # ✅ AMÉLIORATION: Vérifier les rôles OTHER avec mots-clés vidéo
        video_keywords = [
            # Rôles techniques vidéo
            'video', 'vidéo', 'clip', 'director', 'réalisateur', 'cinematographer',
            'camera', 'caméra', 'drone', 'steadicam', 'gimbal',
            
            # Éclairage et technique
            'electrician', 'électricien', 'lighting', 'éclairage', 'gaffer',
            'grip', 'focus puller', 'assistant camera', 'camera operator',
            
            # Maquillage et costume pour vidéo
            'makeup artist', 'maquilleur', 'maquilleuse', 'hair', 'coiffeur',
            'costume', 'wardrobe', 'styliste', 'styling',
            
            # Post-production
            'editor', 'monteur', 'monteuse', 'colorist', 'étalonnage',
            'motion graphics', 'vfx', 'visual effects', 'effets visuels',
            
            # Décors et accessoires
            'set decorator', 'décorateur', 'props', 'accessoires',
            'location', 'repérage', 'casting director',
            
            # Production vidéo
            'video producer', 'production manager', 'assistant director',
            'script supervisor', 'continuity'
        ]
        
        # Ajouter les rôles OTHER qui contiennent des mots-clés vidéo
        for credit in self.credits:
            if credit.role == CreditRole.OTHER and credit.role_detail:
                role_detail_lower = credit.role_detail.lower()
                
                # Vérifier si c'est un rôle vidéo
                if any(keyword in role_detail_lower for keyword in video_keywords):
                    # Double vérification : ne pas prendre les rôles purement musicaux
                    music_exclusions = [
                        'songwriter', 'composer', 'producer', 'mix', 'master',
                        'guitar', 'piano', 'drums', 'bass', 'vocal', 'engineer'
                    ]
                    
                    is_music_role = any(exclusion in role_detail_lower for exclusion in music_exclusions)
                    
                    if not is_music_role and credit not in video_credits:
                        video_credits.append(credit)
                        logger.debug(f"🎬 Crédit vidéo détecté: {credit.name} - {credit.role_detail}")
        
        return video_credits
    
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
        """Convertit le morceau en dictionnaire - VERSION AVEC SÉPARATION VIDÉO"""
        is_featuring = hasattr(self, 'is_featuring') and self.is_featuring
        
        music_credits = self.get_music_credits()
        video_credits = self.get_video_credits()
        
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