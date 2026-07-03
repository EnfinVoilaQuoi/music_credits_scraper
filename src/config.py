"""Configuration centralisée du projet"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Toujours charger .env comme FALLBACK, sans écraser les variables Windows
# (override=False) : Windows reste prioritaire, mais une clé présente seulement
# dans .env (ex. BPMFINDER_*) est quand même lue. L'ancienne logique
# « charger .env seulement si GENIUS_API_KEY absente de Windows » ignorait
# silencieusement toute clé mise uniquement dans .env dès que GENIUS_API_KEY
# était définie côté Windows.
load_dotenv(override=False)

# Chemins du projet
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
ARTISTS_DIR = DATA_DIR / "artists"
LOGS_DIR = DATA_DIR / "logs"

# Créer les dossiers s'ils n'existent pas
ARTISTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Configuration API
GENIUS_API_KEY = os.getenv("GENIUS_API_KEY")
DISCOGS_TOKEN = os.getenv("DISCOGS_TOKEN")
LAST_FM_API_KEY = os.getenv("LAST_FM_API_KEY")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
GETSONGBPM_API_KEY = os.getenv("GETSONGBPM_API_KEY")
# AcousticBrainz n'a pas besoin de clé (open source)

# Configuration de l'application
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
LOG_LEVEL = "DEBUG"  # Au lieu de os.getenv("LOG_LEVEL", "INFO")

# Configuration du scraping
SELENIUM_TIMEOUT = 30  # Timeout en secondes
MAX_RETRIES = 3  # Nombre de tentatives en cas d'erreur
DELAY_BETWEEN_REQUESTS = 1  # Délai entre les requêtes (en secondes)

# Configuration Genius API
GENIUS_TIMEOUT = int(os.getenv("GENIUS_TIMEOUT", "30"))  # Timeout pour requêtes Genius (secondes)
GENIUS_RETRIES = int(os.getenv("GENIUS_RETRIES", "2"))  # Nombre de tentatives
GENIUS_SLEEP_TIME = float(os.getenv("GENIUS_SLEEP_TIME", "0.5"))  # Délai entre requêtes API

# Configuration de la base de données
DATABASE_URL = f"sqlite:///{DATA_DIR}/music_credits.db"

# Configuration de l'interface
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800
THEME = "dark"  # "dark" ou "light"

# Configuration YouTube
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")  # Optionnel, fallback sur ytmusicapi
YOUTUBE_QUOTA_LIMIT = int(os.getenv("YOUTUBE_QUOTA_LIMIT", "10000"))
YOUTUBE_CACHE_TTL_HOURS = int(os.getenv("YOUTUBE_CACHE_TTL_HOURS", "24"))

# Configuration de l'intégration YouTube
YOUTUBE_AUTO_SELECT_ALBUM_TRACKS = True  # Auto-sélection pour morceaux d'album
YOUTUBE_VERIFY_OFFICIAL_CHANNELS = True  # Vérifier les chaînes officielles
YOUTUBE_CONFIDENCE_THRESHOLD = 0.85      # Seuil de confiance pour auto-sélection
YOUTUBE_PERSIST_CONFIDENCE = 0.90        # Seuil pour PERSISTER en DB un lien trouvé par recherche
                                         # (fallback des rares cas sans lien dans le media Genius)

# BPM Finder (audioaidynamics.com/music-analyzer) — BPM/Key/Camelot via lien YouTube
# Compte email/mot de passe requis ; session Playwright persistée (reconnexion rare)
BPMFINDER_EMAIL = os.getenv("BPMFINDER_EMAIL")
BPMFINDER_PASSWORD = os.getenv("BPMFINDER_PASSWORD")
BPMFINDER_SESSION_FILE = DATA_DIR / ".bpmfinder_session.json"

# Certifications
DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')