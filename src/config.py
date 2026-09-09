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
from typing import Literal

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Chemins du projet — dérivés du code source, jamais de l'environnement.
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
ARTISTS_DIR = DATA_DIR / "artists"
LOGS_DIR = DATA_DIR / "logs"
# Chantier « Media » : images téléchargées (photos d'artistes, covers, vignettes
# YouTube). Sous-dossiers dédiés par catégorie. Contenu gitignoré (data/images/*).
IMAGES_DIR = DATA_DIR / "images"
ARTIST_IMAGES_DIR = IMAGES_DIR / "artistes"
COVER_IMAGES_DIR = IMAGES_DIR / "covers"
VIGNETTE_IMAGES_DIR = IMAGES_DIR / "vignettes"
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
for _img_dir in (ARTIST_IMAGES_DIR, COVER_IMAGES_DIR, VIGNETTE_IMAGES_DIR):
    _img_dir.mkdir(parents=True, exist_ok=True)

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
    # Dossier des exports « studio » (SVG Bubble Prod…). Vide → BASE_DIR/exports.
    # Pas de mkdir à l'import : création lazy dans dataviz.bubble_prod (gitignoré).
    exports_dir: str = ""

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

    # --- Spotify ID : plancher de pertinence du scraper ---
    # CALIBRÉ sur données le 2026-09-09, jamais au jugé (36 requêtes réelles,
    # chaque identifiant retenu confronté à l'oracle embed) :
    #
    #   score ≥ 0,80  →  15 identifiants, 0 faux
    #   score = 0,60  →   1 juste, 3 faux        (zone grise, arbitrée par le LLM)
    #   score ≤ 0,50  →   0 juste, 16 faux
    #
    # 0,60 est le seul plancher qui écarte les faux SANS perdre un seul
    # identifiant juste : à 0,70 on en perd un, à 0,50 on garde 14 faux.
    # YouTube avait son seuil depuis toujours ; ce chemin-là n'en avait aucun,
    # et rendait donc `found_tracks[0]` quel que fût son score.
    spotify_id_min_relevance: float = 0.60

    # --- Musixmatch : fenêtre de repos après un jeton refusé ---
    # `token.get` est l'endpoint que Musixmatch bride le plus par IP. Quand il
    # cesse de rendre un jeton utilisable, insister ne sert à rien : chaque
    # morceau rejouait DEUX requêtes de plus sur une IP déjà bridée, et un
    # WARNING par morceau. On cesse d'interroger la source pendant ce délai,
    # puis on reprend seul — jamais de disjoncteur définitif, la source doit
    # pouvoir revenir dans le même run. 0 désactive la mise au repos.
    musixmatch_token_cooldown_s: int = 600

    # --- Désambiguïsation canal YTM (gate d'identité, update_ytmusic) ---
    # Un canal inféré/recherché est jugé suspect (→ abort sans écriture) si trop
    # peu de titres communs avec la base, ou ratio faible SANS album commun.
    ytm_identity_min_matched: int = 2  # plancher de titres YTM communs avec la base
    ytm_identity_min_ratio: float = 0.3  # part min des titres YTM retrouvés en base

    # --- ReccoBeats : péremption du cache NÉGATIF ---
    # Un Spotify ID / ISRC inconnu de ReccoBeats est mémorisé comme tel. Sans
    # péremption, une absence serait figée pour toujours ; sans lecture du tout
    # (le cas jusqu'au 2026-09-04), l'ID était re-demandé à CHAQUE passage — 164
    # entrées sur 930 dans le cache réel. Un mois est le compromis : le catalogue
    # ReccoBeats bouge lentement, mais un morceau ajouté finit par être repêché.
    reccobeats_not_found_ttl_days: int = 30

    # --- Streams Spotify : qui écrit la colonne, et à quel rythme scraper ---
    # `streams_master` désigne la source qui écrit `tracks.spotify_streams` ;
    # l'autre ne pose que son observation. La priorité vit ICI et NULLE PART
    # ailleurs : ni en dur dans un updater, ni déduite de l'ordre des appels du
    # worker. C'est ce qui rend la bascule triviale — et réversible — le jour où
    # la comparaison des deux sources tranchera. Les deux updaters restent
    # symétriques et ignorants de qui est maître.
    streams_master: Literal["kworb", "spotify_web"] = "kworb"
    # Le scrape Spotify coûte UNE PAGE PAR MORCEAU (aucune page ne rend un album
    # d'un coup, mesuré le 2026-09-04) : sans plafond, un gros catalogue ferait
    # un run interminable. Le rattrapage s'étale sur plusieurs sessions.
    spotify_web_max_pages_per_run: int = 120
    # Péremption d'un compteur : en deçà, on ne redépense pas une page. Vaut pour
    # toute observation `spotify_web`, quel que soit le run qui l'a écrite — une
    # valeur récoltée sur la page d'un autre artiste compte comme fraîche.
    spotify_web_freshness_days: int = 14

    # --- BPI (certifications UK) : plafond de pagination ---
    # Le corpus fait ~26 500 lignes à 24 par page, soit ~1 105 pages (mesuré le
    # 2026-09-07). Le plafond n'est PAS une limite de collecte mais un cran
    # d'arrêt : il n'existe que pour qu'une pagination qui ne se terminerait plus
    # (page N rendant éternellement des lignes) s'arrête au lieu de tourner sans
    # fin. Le dépassement est SIGNALÉ, jamais silencieux. À relever si le corpus
    # grossit — le franchir doit rester un événement, pas la normale.
    bpi_max_pages: int = 1500

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
SPOTIFY_ID_MIN_RELEVANCE = settings.spotify_id_min_relevance

# Musixmatch (fenêtre de repos après un jeton refusé)
MUSIXMATCH_TOKEN_COOLDOWN_S = settings.musixmatch_token_cooldown_s

# ReccoBeats (péremption du cache négatif)
RECCOBEATS_NOT_FOUND_TTL_DAYS = settings.reccobeats_not_found_ttl_days

# Désambiguïsation canal YTM (gate d'identité)
YTM_IDENTITY_MIN_MATCHED = settings.ytm_identity_min_matched
YTM_IDENTITY_MIN_RATIO = settings.ytm_identity_min_ratio

# BPI (cran d'arrêt de pagination)
BPI_MAX_PAGES = settings.bpi_max_pages

# Chemins dérivés (non configurables par l'environnement)
DATABASE_URL = f"sqlite:///{DATA_DIR}/music_credits.db"
BPMFINDER_SESSION_FILE = DATA_DIR / ".bpmfinder_session.json"
DATA_PATH = str(DATA_DIR)

# Dossier des exports studio (SVG Bubble Prod…). PAS de mkdir ici : la création
# est lazy (dataviz.bubble_prod.default_output_path). Dossier gitignoré.
EXPORTS_DIR = Path(settings.exports_dir) if settings.exports_dir else (BASE_DIR / "exports")
