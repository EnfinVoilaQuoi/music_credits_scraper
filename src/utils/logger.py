"""Système de logging centralisé"""
import logging
import coloredlogs
from pathlib import Path
from datetime import datetime
from src.config import LOGS_DIR, LOG_LEVEL, DEBUG


class Logger:
    """Gestionnaire de logs centralisé"""
    
    _loggers = {}
    
    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        """Obtient ou crée un logger"""
        if name in cls._loggers:
            return cls._loggers[name]
        
        # Créer le logger
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)  # Force DEBUG temporairement
        
        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        
        # Handler console avec couleurs
        if DEBUG:
            coloredlogs.install(
                level=LOG_LEVEL,
                logger=logger,
                fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
        
        # Handler fichier
        log_file = LOGS_DIR / f"{datetime.now().strftime('%Y%m%d')}_scraper.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # Handler pour les erreurs
        error_file = LOGS_DIR / f"{datetime.now().strftime('%Y%m%d')}_errors.log"
        error_handler = logging.FileHandler(error_file, encoding='utf-8')
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        logger.addHandler(error_handler)
        
        cls._loggers[name] = logger
        return logger
    
    @classmethod
    def log_scraping_error(cls, track_title: str, error: str, source: str):
        """Log spécifique pour les erreurs de scraping"""
        logger = cls.get_logger('scraping_errors')
        logger.error(f"[{source}] Erreur sur '{track_title}': {error}")
    
    @classmethod
    def log_api_call(cls, api_name: str, endpoint: str, success: bool):
        """Log spécifique pour les appels API"""
        logger = cls.get_logger('api_calls')
        if success:
            logger.info(f"[{api_name}] Appel réussi: {endpoint}")
        else:
            logger.error(f"[{api_name}] Appel échoué: {endpoint}")


# Raccourcis pour faciliter l'usage
def get_logger(name: str) -> logging.Logger:
    """Raccourci pour obtenir un logger"""
    return Logger.get_logger(name)


def log_error(track_title: str, error: str, source: str):
    """Raccourci pour logger une erreur de scraping"""
    Logger.log_scraping_error(track_title, error, source)


def log_api(api_name: str, endpoint: str, success: bool):
    """Raccourci pour logger un appel API"""
    Logger.log_api_call(api_name, endpoint, success)
