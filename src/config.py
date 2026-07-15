"""Configuration centralisée du projet.

Config typée et validée via **pydantic-settings**. Les valeurs viennent des
variables d'environnement Windows (prioritaires) puis du fichier ``.env``
(fallback) — même sémantique qu'avant (``override=False``).

Règle : ne plus écrire de valeur applicative « en dur » ici. Pour ajouter un
réglage, déclarer un **champ typé** dans :class:`Settings` (validé au démarrage :
un champ invalide fait crasher au lancement, pas un ``None`` silencieux trois
couches plus loin). Les constantes module-niveau historiques
(``GENIUS_API_KEY``, ``DELAY_BETWEEN_REQUESTS``, ``LOG_LEVEL``…) restent exposées
pour compatibilité : elles sont désormais dérivées de l'objet ``settings``.
"""

from pathlib import Path

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Chemins du projet — dérivés du code source, jamais de l'environnement.
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
ARTISTS_DIR = DATA_DIR / "artists"
LOGS_DIR = DATA_DIR / "logs"
ENV_FILE = BASE_DIR / ".env"

# Charger .env comme FALLBACK sans écraser les variables Windows (override=False) :
# Windows reste prioritaire. On garde load_dotenv EN PLUS de pydantic-settings car
# plusieurs modules lisent des clés directement via os.getenv (MUSIXMATCH_*,
# GENIUS_CDP_URL, SCRAPER_BROWSER_CHANNEL, DISCOGS_USER_TOKEN…) : elles doivent
# rester injectées dans os.environ.
load_dotenv(ENV_FILE, override=False)

# Créer les dossiers s'ils n'existent pas
ARTISTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class Settings(BaseSettings):
    """Réglages applicatifs typés (env Windows prioritaire, ``.env`` en fallback)."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # .env contient des clés hors Settings (MUSIXMATCH_*, GENIUS_CDP_URL…)
    )

    # --- Clés API (optionnelles : l'app démarre sans, mais la source liée est inactive) ---
    genius_api_key: str | None = None
    discogs_token: str | None = None
    last_fm_api_key: str | None = None
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    getsongbpm_api_key: str | None = None
    youtube_api_key: str | None = None  # optionnel, fallback sur ytmusicapi
    bpmfinder_email: str | None = None
    bpmfinder_password: str | None = None

    # --- Application ---
    debug: bool = False
    log_level: str = "INFO"  # piloté par l'env LOG_LEVEL (fin du « DEBUG » en dur)

    # --- Scraping ---
    selenium_timeout: int = 30  # secondes
    max_retries: int = 3
    delay_between_requests: float = 1.0  # secondes

    # --- Genius API ---
    genius_timeout: int = 30
    genius_retries: int = 2
    genius_sleep_time: float = 0.5

    # --- Interface ---
    window_width: int = 1200
    window_height: int = 800
    theme: str = "dark"  # "dark" ou "light"

    # --- YouTube ---
    youtube_quota_limit: int = 10000
    youtube_cache_ttl_hours: int = 24
    youtube_auto_select_album_tracks: bool = True
    youtube_verify_official_channels: bool = True
    youtube_confidence_threshold: float = 0.85  # seuil auto-sélection
    youtube_persist_confidence: float = 0.90  # seuil pour PERSISTER un lien trouvé par recherche

    # --- Désambiguïsation canal YTM (gate d'identité, update_ytmusic) ---
    # Un canal inféré/recherché est jugé suspect (→ abort sans écriture) si trop
    # peu de titres communs avec la base, ou ratio faible SANS album commun.
    ytm_identity_min_matched: int = 2  # plancher de titres YTM communs avec la base
    ytm_identity_min_ratio: float = 0.3  # part min des titres YTM retrouvés en base

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> str:
        level = str(value).strip().upper()
        if level not in _VALID_LOG_LEVELS:
            raise ValueError(
                f"LOG_LEVEL invalide: {value!r} "
                f"(attendu: {', '.join(sorted(_VALID_LOG_LEVELS))})"
            )
        return level

    @field_validator("theme", mode="before")
    @classmethod
    def _normalize_theme(cls, value: object) -> str:
        theme = str(value).strip().lower()
        if theme not in {"dark", "light"}:
            raise ValueError(f"THEME invalide: {value!r} (attendu: dark ou light)")
        return theme


settings = Settings()

# --- Compat : noms module-niveau historiques dérivés de `settings` ---------------
# (54 fichiers importent ces constantes ; ne pas les retirer sans migrer les appels.)

# Clés API
GENIUS_API_KEY = settings.genius_api_key
DISCOGS_TOKEN = settings.discogs_token
LAST_FM_API_KEY = settings.last_fm_api_key
SPOTIFY_CLIENT_ID = settings.spotify_client_id
SPOTIFY_CLIENT_SECRET = settings.spotify_client_secret
GETSONGBPM_API_KEY = settings.getsongbpm_api_key
YOUTUBE_API_KEY = settings.youtube_api_key
BPMFINDER_EMAIL = settings.bpmfinder_email
BPMFINDER_PASSWORD = settings.bpmfinder_password

# Application
DEBUG = settings.debug
LOG_LEVEL = settings.log_level

# Scraping
SELENIUM_TIMEOUT = settings.selenium_timeout
MAX_RETRIES = settings.max_retries
DELAY_BETWEEN_REQUESTS = settings.delay_between_requests

# Genius API
GENIUS_TIMEOUT = settings.genius_timeout
GENIUS_RETRIES = settings.genius_retries
GENIUS_SLEEP_TIME = settings.genius_sleep_time

# Interface
WINDOW_WIDTH = settings.window_width
WINDOW_HEIGHT = settings.window_height
THEME = settings.theme

# YouTube
YOUTUBE_QUOTA_LIMIT = settings.youtube_quota_limit
YOUTUBE_CACHE_TTL_HOURS = settings.youtube_cache_ttl_hours
YOUTUBE_AUTO_SELECT_ALBUM_TRACKS = settings.youtube_auto_select_album_tracks
YOUTUBE_VERIFY_OFFICIAL_CHANNELS = settings.youtube_verify_official_channels
YOUTUBE_CONFIDENCE_THRESHOLD = settings.youtube_confidence_threshold
YOUTUBE_PERSIST_CONFIDENCE = settings.youtube_persist_confidence

# Désambiguïsation canal YTM (gate d'identité)
YTM_IDENTITY_MIN_MATCHED = settings.ytm_identity_min_matched
YTM_IDENTITY_MIN_RATIO = settings.ytm_identity_min_ratio

# Chemins dérivés (non configurables par l'environnement)
DATABASE_URL = f"sqlite:///{DATA_DIR}/music_credits.db"
BPMFINDER_SESSION_FILE = DATA_DIR / ".bpmfinder_session.json"
DATA_PATH = str(DATA_DIR)
