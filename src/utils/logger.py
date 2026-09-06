"""Système de logging centralisé.

**Console NON BLOQUANTE (2026-09-06).** Symptôme observé : l'application se
figeait entièrement au premier morceau d'un enrichissement, ne répondait plus,
et se débloquait *instantanément* quand on appuyait sur Ctrl+C après avoir
sélectionné du texte dans la console. Rien n'était cassé — c'est le mode
**QuickEdit** de la console Windows : sélectionner du texte SUSPEND les
écritures sur la console. Le handler de log bloque alors dans son `emit`, et
comme `logging.Handler` sérialise ses écritures avec un verrou, TOUS les threads
qui loggent se bloquent derrière lui, y compris la boucle Tk. Ctrl+C sort du mode
marque, l'écriture reprend, tout repart — d'où l'illusion que la copie « relance »
le programme.

Le remède n'est pas de désactiver QuickEdit (on perdrait le copier-coller, qui
sert justement à rapporter un bug) : c'est de mettre une FILE entre les threads
et la console (`QueueHandler` + `QueueListener`). Seul le thread du listener
attend ; les workers et le GUI continuent. Si la console reste bloquée assez
longtemps pour saturer la file, on DÉCROCHE l'écho console plutôt que de bloquer
— les fichiers de log, eux, ne perdent rien (ils ont leurs propres handlers,
hors de la file).
"""

import logging
import queue
from datetime import datetime
from logging.handlers import QueueHandler, QueueListener

import coloredlogs

from src.config import DEBUG, LOG_LEVEL, LOGS_DIR

#: File et listener PARTAGÉS par tous les loggers du projet. Un seul handler
#: console derrière la file : le listener réémet chaque enregistrement vers
#: TOUS ses handlers, donc en poser un par logger afficherait chaque ligne
#: autant de fois qu'il existe de loggers.
_FILE_CONSOLE: queue.Queue | None = None
_LISTENER: QueueListener | None = None

#: Au-delà, on abandonne l'écho console plutôt que de bloquer l'application.
#: 10 000 lignes = très largement de quoi absorber une sélection de texte de
#: plusieurs minutes ; au-delà, c'est que la console ne repart pas.
_TAILLE_FILE = 10_000


class _ConsoleNonBloquante(QueueHandler):
    """QueueHandler qui LÂCHE les lignes quand la file est pleine.

    Le `QueueHandler` standard laisse remonter `queue.Full` à `handleError`,
    lequel écrit sur `sys.stderr`… c'est-à-dire sur la console bloquée qu'on
    cherche justement à contourner. On préfère perdre l'écho console : les
    fichiers de log gardent tout.
    """

    def enqueue(self, record):
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            pass


def _handlers_console(logger: logging.Logger) -> list[logging.Handler]:
    """Handlers qui écrivent DIRECTEMENT sur la console (donc bloquants)."""
    return [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler | QueueHandler)
    ]


def _console_non_bloquante(logger: logging.Logger) -> None:
    """Déporte tout écho console derrière la file partagée. Idempotent.

    On balaie le logger NOMMÉ **et** la RACINE : selon l'ordre des imports,
    `coloredlogs.install()` pose son handler sur l'un ou sur l'autre (les deux
    cas ont été observés dans ce projet), et `logging.basicConfig()` — appelé au
    chargement de certains modules — en pose un de plus sur la racine. Un seul
    des deux traité, les lignes s'affichaient EN DOUBLE : une fois par le
    handler direct du logger nommé, une fois par la file de la racine.

    Un unique `_ConsoleNonBloquante` est monté sur la RACINE : tous les loggers
    du projet y propagent, donc il suffit à tout afficher, une fois chacun.
    """
    global _FILE_CONSOLE, _LISTENER

    racine = logging.getLogger()
    directs = _handlers_console(logger) + _handlers_console(racine)
    if not directs:
        return
    for handler in directs:
        logger.removeHandler(handler)
        racine.removeHandler(handler)

    if _LISTENER is None:
        _FILE_CONSOLE = queue.Queue(maxsize=_TAILLE_FILE)
        # Un SEUL handler derrière la file : le listener réémet chaque
        # enregistrement vers tous ceux qu'il détient — en garder plusieurs
        # (ils sont équivalents) afficherait chaque ligne autant de fois.
        # `respect_handler_level` : le handler console garde son propre seuil.
        _LISTENER = QueueListener(_FILE_CONSOLE, directs[0], respect_handler_level=True)
        _LISTENER.start()  # thread daemon (pas de blocage à la fermeture)
        racine.addHandler(_ConsoleNonBloquante(_FILE_CONSOLE))


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
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

        # Handler console avec couleurs, puis mise derrière la file (cf. en-tête)
        if DEBUG:
            coloredlogs.install(
                level=LOG_LEVEL,
                logger=logger,
                fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            )
            _console_non_bloquante(logger)

        # Handler fichier
        log_file = LOGS_DIR / f"{datetime.now().strftime('%Y%m%d')}_scraper.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # Handler pour les erreurs
        error_file = LOGS_DIR / f"{datetime.now().strftime('%Y%m%d')}_errors.log"
        error_handler = logging.FileHandler(error_file, encoding="utf-8")
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        logger.addHandler(error_handler)

        cls._loggers[name] = logger
        return logger

    @classmethod
    def log_scraping_error(cls, track_title: str, error: str, source: str):
        """Log spécifique pour les erreurs de scraping"""
        logger = cls.get_logger("scraping_errors")
        logger.error(f"[{source}] Erreur sur '{track_title}': {error}")

    @classmethod
    def log_api_call(cls, api_name: str, endpoint: str, success: bool):
        """Log spécifique pour les appels API"""
        logger = cls.get_logger("api_calls")
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
