"""Configuration centralisée du projet"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Charger les variables d'environnement depuis .env seulement si elles n'existent pas dans Windows
# Cela privilégie les variables Windows mais permet le fallback sur .env pour le dev
if not os.getenv("GENIUS_API_KEY"):
    load_dotenv()

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
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Configuration du scraping
SELENIUM_TIMEOUT = 30  # Timeout en secondes
MAX_RETRIES = 3  # Nombre de tentatives en cas d'erreur
DELAY_BETWEEN_REQUESTS = 1  # Délai entre les requêtes (en secondes)

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

# Certifications
DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')