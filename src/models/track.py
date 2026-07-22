"""Modèles pour représenter les morceaux et crédits"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from src.enrichment.observation import Observation
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
class Audio:
    """Données audio réconciliées d'un morceau (BPM / key / mode + provenance).

    Sous-objet de `Track` (Phase 5). Regroupe les champs audio historiquement
    plats. Pilotés par le moteur de réconciliation (`apply_resolutions`) depuis
    les observations ; posés à None par le mapper au chargement. Accès via
    `track.audio.<champ>`.
    """

    bpm: int | None = None  # BPM "réel" (double-time) — valeur exportée
    bpm_alt: int | None = None  # Octave alternative (half-time), ex. 71 pour 142
    bpm_source: str | None = None  # Source(s) du BPM retenu (vote §8.3)
    bpm_confidence: int | None = None  # Nb de sources concordantes
    key: int | None = None  # Pitch class 0-11 (posé par le moteur, ex-attr dynamique)
    mode: int | None = None  # 0=mineur, 1=majeur (ex-attr dynamique)
    key_mode_source: str | None = None  # Source de key/mode (peut différer du BPM)
    musical_key: str | None = None  # Notation FR canonique (ex. "Do# mineur")
    time_signature: str | None = None  # ex. "4/4"
    reccobeats_resolution: str | None = None  # 'isrc' | 'spotify_id' — voie ReccoBeats


@dataclass
class Streams:
    """Compteurs de streams d'un morceau (Spotify via Kworb + YouTube Music).

    Sous-objet de `Track` (Phase 5). Écrits en base par write-through
    (update_kworb / update_ytmusic) puis relus par le mapper ; noms de champs
    alignés sur les colonnes DB. Accès via `track.streams.<champ>`.
    """

    spotify_streams: int | None = None
    spotify_daily_streams: int | None = None
    spotify_streams_updated: datetime | None = None
    ytm_streams: int | None = None
    ytm_streams_updated: datetime | None = None


@dataclass
class Lyrics:
    """Paroles d'un morceau (texte + synchro LRC + provenance).

    Sous-objet de `Track` (Phase 5). Champs renommés (le sous-objet porte déjà le
    contexte « lyrics ») : accès via `track.lyrics.text.text`, `.synced`, etc. Les
    colonnes DB gardent leurs noms (`lyrics`, `has_lyrics`, `lyrics_synced`…).
    """

    text: str | None = None  # Paroles complètes (colonne DB `lyrics`)
    present: bool = False  # Présence de paroles (colonne DB `has_lyrics`)
    scraped_at: datetime | None = None  # Date de récupération (colonne `lyrics_scraped_at`)
    source: str | None = None  # Provenance (colonne `lyrics_source`)
    synced: str | None = None  # LRC retenu LRCLIB>YTM (colonne `lyrics_synced`)
    synced_source: str | None = None  # colonne `lyrics_synced_source`
    synced_confidence: int | None = None  # colonne `lyrics_synced_confidence`


@dataclass
class Certs:
    """Certifications d'un morceau (plus haute + listes détaillées).

    Sous-objet de `Track` (Phase 5). Renommé (`certs`) pour éviter la collision
    avec la liste `certifications` : accès via track.certs.level / .date /
    .entries … Les colonnes/JSON DB gardent leurs noms.
    """

    has: bool = False  # colonne DB `has_certification`
    level: str | None = None  # Plus haute certif (colonne `certification_level`)
    date: datetime | None = None  # Date de la plus haute (colonne `certification_date`)
    duration_days: int | None = None  # Durée d'obtention (colonne `certification_duration_days`)
    entries: list[dict[str, Any]] = field(default_factory=list)  # colonne `certifications`
    album_entries: list[dict[str, Any]] = field(default_factory=list)  # `album_certifications`


@dataclass
class Media:
    """Images (pochette/vignette) et vidéo YouTube d'un morceau.

    Sous-objet de `Track` (Phase 5). Accès via track.media.<champ> ; noms alignés
    sur les colonnes DB (inchangées).
    """

    artwork_url: str | None = None  # URL de la pochette
    cover_path: str | None = None  # Pochette album/single/sample sur disque
    yt_thumbnail_path: str | None = None  # Vignette YouTube (shows/lives)
    youtube_video_kind: str | None = None  # 'clip'/'show'/'audio'/'unknown'
    youtube_video_views: int | None = None
    youtube_video_views_updated: datetime | None = None


@dataclass(eq=False)
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
    # Audio (BPM/key/mode + provenance) regroupé en sous-objet `audio` (Phase 5) :
    # accès via track.audio.<champ> (bpm, key, mode, musical_key…).
    audio: Audio = field(default_factory=Audio)
    duration: int | None = None  # En secondes
    genre: str | None = None
    track_number: int | None = None
    audio_features: dict[str, Any] | None = field(default_factory=dict)

    # Support des features
    is_featuring: bool = False  # True si l'artiste est en featuring
    featured_artists: str | None = None  # Liste des artistes en featuring
    primary_artist_name: str | None = None  # Nom de l'artiste principal si différent
    secondary_role: str | None = (
        None  # Rôle secondaire (ex: "Additional Voices") si l'artiste n'est ni primary ni feat — rempli = contribution secondaire
    )

    # Paroles (texte + synchro LRC + provenance) regroupées en sous-objet
    # `lyrics` (Phase 5) : accès via track.lyrics.text.text / .synced / .source …
    lyrics: Lyrics = field(default_factory=Lyrics)
    anecdotes: str | None = None  # Anecdotes et informations supplémentaires

    # Métadonnées supplémentaires
    popularity: int | None = None  # Nombre de vues sur Genius
    # Images (pochette/vignette) + vidéo YouTube regroupées en sous-objet `media`
    # (Phase 5) : accès via track.media.cover_path / .youtube_video_views …
    media: Media = field(default_factory=Media)

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
    # Observations FRAÎCHES du run d'enrichissement en cours (phase E5) — NON une
    # colonne : champ transitoire, vidé par save_track qui les upsert dans SA
    # transaction (triple écriture). Toujours vide après une lecture DB (le mapper
    # ne le peuple pas). Peuplé par les providers migrés `fetch()` (E5c-2).
    observations: list["Observation"] = field(default_factory=list, repr=False)

    # Signal transitoire d'EFFACEMENT audio (E7-D1) : posé par les chemins de
    # nettoyage (clear_track_data / _clear_after_total_failure) pour que save_track
    # SUPPRIME les observations audio persistées (bpm/key/mode/…) dans SA
    # transaction — sinon la réconciliation les ressusciterait à la lecture. Non
    # une colonne ; toujours False après une lecture DB.
    clear_audio_observations: bool = field(default=False, repr=False)

    # Certifications (plus haute + listes détaillées) regroupées en sous-objet
    # `certs` (Phase 5) : accès via track.certs.level / .date / .entries …
    certs: Certs = field(default_factory=Certs)

    # Streams (Spotify via kworb.net + YouTube Music) regroupés en sous-objet
    # `streams` (Phase 5) : accès via track.streams.<champ>.
    streams: Streams = field(default_factory=Streams)

    def _identity(self) -> tuple:
        """Clé d'identité métier d'un morceau.

        Le `genius_id` prime : deux objets Track qui partagent le même
        `genius_id` désignent le MÊME morceau, même si les autres champs
        diffèrent (ex. version re-scrapée avec des paroles à jour). À défaut de
        `genius_id`, on retombe sur `(titre, nom d'artiste)`. Le discriminant en
        tête empêche un morceau « avec genius_id » d'être jugé égal à un
        morceau « sans genius_id » (garantit la cohérence __eq__/__hash__).
        """
        if self.genius_id is not None:
            return ("genius_id", self.genius_id)
        return ("title_artist", self.title, self.artist.name if self.artist else None)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Track):
            return NotImplemented
        return self._identity() == other._identity()

    def __hash__(self) -> int:
        return hash(self._identity())

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

        cert_date = parse_flexible(self.certs.date)
        rel_date = parse_flexible(self.release_date)
        if cert_date is None or rel_date is None:
            return None

        try:
            duration = (cert_date - rel_date).days
        except TypeError:
            # Mélange aware/naive (une date ISO avec 'Z', l'autre non)
            return None
        self.certs.duration_days = duration if duration >= 0 else None
        return self.certs.duration_days

    def certification_milestone_durations(self) -> list[tuple[str, int]]:
        """Délai (jours) sortie→certif pour chaque palier IMPORTANT atteint.

        Un couple `(palier, jours)` par palier de base (Or, Platine, Diamant —
        hors multiplicateurs) présent dans `certifications`, à la date la PLUS
        ANCIENNE où le morceau l'a atteint. Paliers absents, sans date de sortie
        ou aux dates illisibles : ignorés.
        """
        from src.utils.dates import parse_flexible

        rel_date = parse_flexible(self.release_date)
        if rel_date is None:
            return []

        out: list[tuple[str, int]] = []
        for level in ("Or", "Platine", "Diamant"):
            dates = [
                d
                for c in self.certs.entries
                if c.get("certification") == level
                and (d := parse_flexible(c.get("certification_date"))) is not None
            ]
            if not dates:
                continue
            try:
                days = (min(dates) - rel_date).days
            except TypeError:
                # Mélange aware/naive (une date ISO avec 'Z', l'autre non)
                continue
            if days >= 0:
                out.append((level, days))
        return out

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

    def to_dict(self) -> dict:
        """Convertit le morceau en dictionnaire - VERSION AVEC SÉPARATION VIDÉO ET PAROLES"""
        is_featuring = self.is_featuring

        music_credits = self.get_music_credits()
        video_credits = self.get_video_credits()

        # Informations sur les paroles
        if self.lyrics.text:
            lyrics_info = {
                "has_lyrics": True,
                "lyrics_word_count": len(self.lyrics.text.split()),
                "lyrics_char_count": len(self.lyrics.text),
                "lyrics_scraped_at": (
                    self.lyrics.scraped_at.isoformat() if self.lyrics.scraped_at else None
                ),
                "lyrics_source": self.lyrics.source,
                "has_synced_lyrics": bool(self.lyrics.synced),
                "lyrics_synced_source": self.lyrics.synced_source,
                "lyrics_synced_confidence": self.lyrics.synced_confidence,
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
            "bpm": self.audio.bpm,
            "bpm_alt": self.audio.bpm_alt,
            "bpm_source": self.audio.bpm_source,
            "bpm_confidence": self.audio.bpm_confidence,
            "key_mode_source": self.audio.key_mode_source,
            "reccobeats_resolution": self.audio.reccobeats_resolution,
            "duration": self.duration,
            "genre": self.genre,
            "is_featuring": is_featuring,
            "featured_artists": self.featured_artists,
            "primary_artist_name": self.primary_artist_name,
            "secondary_role": self.secondary_role,
            "popularity": self.popularity,
            "artwork_url": self.media.artwork_url,
            # Chantier « Media » : chemins d'images + vidéo YouTube
            "cover_path": self.media.cover_path,
            "yt_thumbnail_path": self.media.yt_thumbnail_path,
            "youtube_video_kind": self.media.youtube_video_kind,
            "youtube_video_views": self.media.youtube_video_views,
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
