"""Modèles pour représenter les morceaux et crédits"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from src.models.artist import Artist

# NB : les utilitaires de src.utils sont importés LOCALEMENT dans les méthodes
# (et non au niveau module) : src/utils/__init__.py charge DataManager/DataEnricher,
# qui importent Track — un import module-niveau ici créerait un cycle.
logger = logging.getLogger(__name__)


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


_PRODUCER_ROLES = frozenset(
    {
        CreditRole.PRODUCER,
        CreditRole.CO_PRODUCER,
        CreditRole.EXECUTIVE_PRODUCER,
        CreditRole.VOCAL_PRODUCER,
        CreditRole.ADDITIONAL_PRODUCTION,
    }
)

_WRITER_ROLES = frozenset({CreditRole.WRITER, CreditRole.COMPOSER, CreditRole.LYRICIST})

_VIDEO_ROLES = frozenset(
    {
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
        CreditRole.PHOTOGRAPHY,  # Considéré comme vidéo
    }
)

# Rôles OTHER dont le détail évoque un métier vidéo (départage par mots-clés)
_VIDEO_KEYWORDS = (
    "video",
    "vidéo",
    "clip",
    "director",
    "réalisateur",
    "cinematographer",
    "camera",
    "caméra",
    "drone",
    "steadicam",
    "gimbal",
    "electrician",
    "électricien",
    "lighting",
    "éclairage",
    "gaffer",
    "grip",
    "focus puller",
    "makeup artist",
    "maquilleur",
    "maquilleuse",
    "hair",
    "coiffeur",
    "costume",
    "wardrobe",
    "styliste",
    "styling",
    "editor",
    "monteur",
    "monteuse",
    "colorist",
    "étalonnage",
    "motion graphics",
    "vfx",
    "visual effects",
    "effets visuels",
    "set decorator",
    "décorateur",
    "props",
    "accessoires",
    "location",
    "repérage",
    "casting director",
    "video producer",
    "production manager",
    "assistant director",
    "script supervisor",
    "continuity",
)

# Exclusions pour éviter les faux positifs (métiers musicaux mal étiquetés OTHER)
_MUSIC_EXCLUSIONS = (
    "songwriter",
    "composer",
    "producer",
    "mix",
    "master",
    "guitar",
    "piano",
    "drums",
    "bass",
    "vocal",
    "engineer",
)


@dataclass
class Credit:
    """Représente un crédit sur un morceau"""

    name: str
    role: CreditRole
    role_detail: str | None = None  # Ex: "Guitar", "Piano", etc.
    source: str = "genius"  # Source de l'information

    def to_dict(self) -> dict:
        """Convertit le crédit en dictionnaire (utilisé par Track.to_dict / export JSON)"""
        return {
            "name": self.name,
            "role": self.role.value,
            "role_detail": self.role_detail,
            "source": self.source,
        }

    @staticmethod
    def from_role_and_names(role: str, names: list[str]) -> list["Credit"]:
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
                    source="genius",
                )
                credits.append(credit)

        return credits


@dataclass
class Track:
    """Représente un morceau musical"""

    id: int | None = None
    title: str = ""
    artist: Optional["Artist"] = None
    album: str | None = None
    release_date: datetime | None = None

    # Champs internes de marquage
    _album_from_api: bool = field(default=False, repr=False)
    _release_date_from_api: bool = field(default=False, repr=False)

    # IDs externes
    genius_id: int | None = None
    spotify_id: str | None = None
    spotify_ids: list[str] = field(default_factory=list)
    discogs_id: int | None = None
    isrc: str | None = None  # International Standard Recording Code (pivot inter-sources)

    # Métadonnées
    bpm: int | None = None  # BPM "réel" (double-time) — valeur exportée
    bpm_alt: int | None = None  # Octave alternative (half-time), ex. 71 pour 142
    bpm_source: str | None = None  # Source(s) du BPM retenu (vote §8.3)
    bpm_confidence: int | None = None  # Nb de sources concordantes
    key_mode_source: str | None = None  # Source de key/mode (peut différer du BPM)
    reccobeats_resolution: str | None = (
        None  # 'isrc' | 'spotify_id' — voie de résolution ReccoBeats
    )
    duration: int | None = None  # En secondes
    genre: str | None = None
    track_number: int | None = None
    musical_key: str | None = None
    time_signature: str | None = None
    audio_features: dict[str, Any] | None = field(default_factory=dict)

    # Support des features
    is_featuring: bool = False  # True si l'artiste est en featuring
    featured_artists: str | None = None  # Liste des artistes en featuring
    primary_artist_name: str | None = None  # Nom de l'artiste principal si différent
    secondary_role: str | None = (
        None  # Rôle secondaire (ex: "Additional Voices") si l'artiste n'est ni primary ni feat — rempli = contribution secondaire
    )

    # Support des paroles
    lyrics: str | None = None  # Paroles complètes
    has_lyrics: bool = False  # Indicateur si les paroles sont disponibles
    lyrics_scraped_at: datetime | None = None  # Date de récupération des paroles
    lyrics_source: str | None = None  # Provenance des paroles (YouTube Music / genius)
    lyrics_synced: str | None = None  # Paroles synchronisées (LRC) retenues (LRCLIB > YTM)
    lyrics_synced_source: str | None = (
        None  # Source de la synchro retenue ('LRCLIB' / 'YouTube Music')
    )
    lyrics_synced_confidence: int | None = (
        None  # Nb de sources concordantes (2=croisé/validé, 1=unique ou après départage durée)
    )
    anecdotes: str | None = None  # Anecdotes et informations supplémentaires

    # Métadonnées supplémentaires
    popularity: int | None = None  # Nombre de vues sur Genius
    artwork_url: str | None = None  # URL de la pochette

    # Crédits
    credits: list[Credit] = field(default_factory=list)

    # Relations « inspiré de » (amont) : samples/interpolations/cover_of/remix_of + trad FR
    # Chaque entrée : {type, title, artist, url}. Source : API Genius song_relationships.
    relationships: list[dict[str, Any]] = field(default_factory=list)

    # URLs
    genius_url: str | None = None
    spotify_url: str | None = None
    youtube_url: str | None = None
    # Provenance du lien YouTube : 'genius_media' (API Genius, prioritaire)
    # ou 'search_auto' (recherche ytmusicapi persistée si confiance ≥ YOUTUBE_PERSIST_CONFIDENCE)
    youtube_url_source: str | None = None
    # 1 = album édité MANUELLEMENT (détaché via la vue Albums…) — l'API ne re-remplit pas
    album_override: int | None = None

    # Métadonnées système
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    last_scraped: datetime | None = None
    scraping_errors: list[str] = field(default_factory=list)

    # Champs de certification SNEP - VERSION MULTI-CERTIFICATIONS
    has_certification: bool = False
    certification_level: str | None = None  # Plus haute certification (rétrocompatibilité)
    certification_date: datetime | None = None  # Date de la plus haute certification
    certification_duration_days: int | None = None  # Durée d'obtention en jours
    certification_category: str | None = None  # "Singles" ou "Albums"
    certification_publisher: str | None = None  # Éditeur/Distributeur
    certification_details: dict[str, Any] | None = None  # Détails de la plus haute certification

    # NOUVEAU: Support de plusieurs certifications
    certifications: list[dict[str, Any]] = field(
        default_factory=list
    )  # Toutes les certifications du morceau
    album_certifications: list[dict[str, Any]] = field(
        default_factory=list
    )  # Certifications de l'album associé

    # Streams Spotify (kworb.net)
    spotify_streams: int | None = None
    spotify_daily_streams: int | None = None
    spotify_streams_updated: datetime | None = None

    # Streams YouTube Music
    ytm_streams: int | None = None
    ytm_streams_updated: datetime | None = None

    def add_credit(self, credit: Credit):
        """Ajoute un crédit au morceau"""
        # Éviter les doublons
        for existing in self.credits:
            if (
                existing.name == credit.name
                and existing.role == credit.role
                and existing.role_detail == credit.role_detail
            ):
                return
        self.credits.append(credit)

    def update_release_date(self, new_date, source: str = "unknown", force: bool = False) -> bool:
        """
        Met à jour la date de sortie de manière intelligente

        Args:
            new_date: Nouvelle date (datetime, str, ou None)
            source: Source de la date ("api", "scraper", "manual")
            force: Si True, écrase la date existante même si elle est plus ancienne

        Returns:
            bool: True si la date a été mise à jour, False sinon

        Logique:
        - Garde toujours la date la PLUS ANCIENNE (singles sortis avant l'album)
        - Si force=True, écrase sans vérifier
        - Si pas de date existante, met à jour
        """
        from src.utils.dates import parse_flexible

        # Convertir en datetime (ISO, "YYYY-MM-DD", objet datetime tel quel)
        parsed = parse_flexible(new_date)
        if parsed is None:
            if new_date is not None:
                logger.debug(f"Impossible de parser la date '{new_date}'")
            return False
        new_date = parsed

        # Si pas de date existante, mettre à jour
        if not self.release_date:
            self.release_date = new_date
            logger.debug(
                f"Date de sortie définie pour '{self.title}': {new_date.strftime('%d/%m/%Y')} (source: {source})"
            )
            return True

        # Convertir la date existante ; si illisible, la remplacer
        existing_date = parse_flexible(self.release_date)
        if existing_date is None:
            self.release_date = new_date
            logger.debug(
                f"Date existante invalide remplacée pour '{self.title}': {new_date.strftime('%d/%m/%Y')}"
            )
            return True

        # Si force=True, écraser sans vérifier
        if force:
            self.release_date = new_date
            logger.debug(
                f"Date de sortie écrasée (force) pour '{self.title}': {new_date.strftime('%d/%m/%Y')} (source: {source})"
            )
            return True

        # Comparer les dates et garder la plus ancienne
        if new_date < existing_date:
            old_date_str = existing_date.strftime("%d/%m/%Y")
            new_date_str = new_date.strftime("%d/%m/%Y")
            self.release_date = new_date
            logger.info(
                f"✨ Date plus ancienne trouvée pour '{self.title}': {new_date_str} (remplace {old_date_str}) - Source: {source}"
            )
            return True
        else:
            logger.debug(
                f"Date existante conservée pour '{self.title}': {existing_date.strftime('%d/%m/%Y')} (nouvelle date {new_date.strftime('%d/%m/%Y')} ignorée)"
            )
            return False

    def get_credits_by_role(self, role: CreditRole) -> list[Credit]:
        """Retourne tous les crédits d'un rôle spécifique."""
        return [c for c in self.credits if c.role == role]

    def get_producers(self) -> list[str]:
        """Retourne la liste des producteurs (tous types confondus)."""
        return [c.name for c in self.credits if c.role in _PRODUCER_ROLES]

    def get_writers(self) -> list[str]:
        """Retourne la liste des auteurs (tous types confondus)."""
        return [c.name for c in self.credits if c.role in _WRITER_ROLES]

    # Méthode pour calculer la durée d'obtention
    def calculate_certification_duration(self) -> int | None:
        """Calcule la durée (en jours) entre la sortie et la certification."""
        from src.utils.dates import parse_flexible

        cert_date = parse_flexible(self.certification_date)
        rel_date = parse_flexible(self.release_date)
        if cert_date is None or rel_date is None:
            return None

        try:
            duration = (cert_date - rel_date).days
        except TypeError:
            # Mélange aware/naive (une date ISO avec 'Z', l'autre non)
            return None
        self.certification_duration_days = duration if duration >= 0 else None
        return self.certification_duration_days

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
        """Liste des featured artists : champ dédié (string CSV) sinon crédits FEATURED."""
        if self.featured_artists:
            return [a.strip() for a in self.featured_artists.split(",") if a.strip()]
        return [c.name for c in self.get_credits_by_role(CreditRole.FEATURED)]

    @property
    def credits_scraped(self):
        """Retourne le nombre de crédits (au lieu d'un booléen)."""
        return len(self.credits)

    def has_complete_credits(self) -> bool:
        """Vérifie si les crédits semblent complets.

        Complet = au moins 2 crédits musicaux ET au moins un producteur OU auteur.
        """
        music_credits = self.get_music_credits()
        if len(music_credits) < 2:
            return False
        return bool(self.get_producers()) or bool(self.get_writers())

    def get_music_credits(self) -> list[Credit]:
        """Retourne les crédits musicaux (tout ce qui n'est pas un crédit vidéo)."""
        video_credits = self.get_video_credits()
        return [c for c in self.credits if c not in video_credits]

    def get_video_credits(self) -> list[Credit]:
        """Retourne les crédits vidéo (rôles vidéo explicites + OTHER par mots-clés)."""
        video_credits = [c for c in self.credits if c.role in _VIDEO_ROLES]

        # Rôles OTHER dont le détail évoque un métier vidéo, hors faux positifs
        for credit in self.credits:
            if credit.role != CreditRole.OTHER or not credit.role_detail:
                continue
            detail = credit.role_detail.lower()
            is_video = any(keyword in detail for keyword in _VIDEO_KEYWORDS)
            is_music = any(exclusion in detail for exclusion in _MUSIC_EXCLUSIONS)
            if is_video and not is_music and credit not in video_credits:
                video_credits.append(credit)

        return video_credits

    def get_display_title(self) -> str:
        """Retourne le titre à afficher (le titre contient déjà « feat. » le cas échéant)."""
        return self.title

    def get_display_artist(self) -> str:
        """Retourne l'artiste à afficher (principal si featuring)"""
        if self.is_featuring:
            # Pour les features : retourner l'artiste principal si disponible
            if self.primary_artist_name:
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
        return not self.is_featuring

    def to_dict(self) -> dict:
        """Convertit le morceau en dictionnaire - VERSION AVEC SÉPARATION VIDÉO ET PAROLES"""
        is_featuring = self.is_featuring

        music_credits = self.get_music_credits()
        video_credits = self.get_video_credits()

        # Informations sur les paroles
        if self.lyrics:
            lyrics_info = {
                "has_lyrics": True,
                "lyrics_word_count": len(self.lyrics.split()),
                "lyrics_char_count": len(self.lyrics),
                "lyrics_scraped_at": (
                    self.lyrics_scraped_at.isoformat() if self.lyrics_scraped_at else None
                ),
                "lyrics_source": self.lyrics_source,
                "has_synced_lyrics": bool(self.lyrics_synced),
                "lyrics_synced_source": self.lyrics_synced_source,
                "lyrics_synced_confidence": self.lyrics_synced_confidence,
            }
        else:
            lyrics_info = {
                "has_lyrics": False,
                "lyrics_word_count": 0,
                "lyrics_char_count": 0,
                "lyrics_scraped_at": None,
            }

        return {
            "id": self.id,
            "title": self.title,
            "display_title": self.get_display_title(),
            "artist": self.artist.name if self.artist else None,
            "display_artist": self.get_display_artist(),
            "album": self.album,
            "track_number": self.track_number,
            "release_date": self.release_date.isoformat() if self.release_date else None,
            "genius_id": self.genius_id,
            "spotify_id": self.spotify_id,
            "discogs_id": self.discogs_id,
            "isrc": self.isrc,
            "relationships": self.relationships or [],
            "bpm": self.bpm,
            "bpm_alt": self.bpm_alt,
            "bpm_source": self.bpm_source,
            "bpm_confidence": self.bpm_confidence,
            "key_mode_source": self.key_mode_source,
            "reccobeats_resolution": self.reccobeats_resolution,
            "duration": self.duration,
            "genre": self.genre,
            "is_featuring": is_featuring,
            "featured_artists": self.featured_artists,
            "primary_artist_name": self.primary_artist_name,
            "secondary_role": self.secondary_role,
            "popularity": self.popularity,
            "artwork_url": self.artwork_url,
            # Informations paroles
            **lyrics_info,
            # ✅ SÉPARATION DES CRÉDITS
            "music_credits": [c.to_dict() for c in music_credits],
            "video_credits": [c.to_dict() for c in video_credits],
            "all_credits": [c.to_dict() for c in self.credits],  # Garde la compatibilité
            # Statistiques
            "music_credits_count": len(music_credits),
            "video_credits_count": len(video_credits),
            "total_credits_count": len(self.credits),
            "has_complete_credits": self.has_complete_credits(),
            "genius_url": self.genius_url,
            "spotify_url": self.spotify_url,
            "youtube_url": self.youtube_url,
            "youtube_url_source": self.youtube_url_source,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_scraped": self.last_scraped.isoformat() if self.last_scraped else None,
            "scraping_errors": self.scraping_errors,
        }

    # (Méthode GUI _start_lyrics_scraping supprimée le 2026-07-10 : code d'interface
    # copié par erreur dans le modèle — cf. AUDIT.md §3.3. La fonctionnalité vit
    # dans src/gui/workers/scraping.py.)

    @property
    def primary_spotify_id(self) -> str | None:
        """Retourne l'ID Spotify principal"""
        if self.spotify_ids and len(self.spotify_ids) > 0:
            return self.spotify_ids[0]
        return self.spotify_id

    def add_spotify_id(self, new_id: str) -> bool:
        """
        Ajoute un Spotify ID à la liste (sans doublons)

        Returns:
            bool: True si l'ID a été ajouté, False s'il existait déjà
        """
        if not new_id:
            return False

        # Éviter les doublons
        if new_id in self.spotify_ids:
            return False

        # Ajouter le nouvel ID
        self.spotify_ids.append(new_id)

        # Mettre à jour spotify_id (compatibilité)
        if not self.spotify_id:
            self.spotify_id = new_id

        return True

    def get_all_spotify_ids(self) -> list[str]:
        """Retourne tous les Spotify IDs du track (legacy + liste, sans doublons)."""
        ids = []
        if self.spotify_id:
            ids.append(self.spotify_id)
        for sid in self.spotify_ids:
            if sid not in ids:
                ids.append(sid)
        return ids
