"""Journaux du projet : UNE configuration, trois canaux, un rôle chacun.

**Refonte du 2026-09-30.** La console était devenue muette : son handler n'était
posé que si `DEBUG=true` (faux par défaut depuis le 2026-07-12), et les handlers
FICHIER étaient accrochés logger par logger dans `get_logger` — si bien qu'un
module passant par `logging.getLogger` (SongBPM async, `models/track.py`,
httpx, crawl4ai…) n'écrivait NULLE PART. Le seul texte encore visible en
console venait du secours interne de Python (`logging.lastResort`), qui imprime
tout WARNING orphelin sans heure ni nom : « ❌ Aucune correspondance parmi 5
résultat(s) » — une ABSENCE, affichée comme seul signe de vie.

Les trois canaux :

* **Fichiers** (`<date>_scraper.log` INFO+, `<date>_errors.log` ERROR+) —
  le DIAGNOSTIC : tout ce que disent tous les modules, bibliothèques tierces
  comprises (plafonnées à WARNING). Posés sur la RACINE : aucun trou possible.
* **Console** — le RÉCIT d'un run : étapes annoncées (▶ / ▸), avancement
  espacé, fin (✔ / ⚠ / ✖), plus les WARNING et ERROR de tous les modules.
  `DEBUG=true` (ou `configurer_journaux(console="tout")`, que prennent les
  scripts en ligne de commande) y ajoute tout l'INFO — l'ancien comportement.
* **Bilan** (`resume()` des services) — le compte rendu final, inchangé.

Le récit passe par le logger `recit` (`etape`, `annoncer`, `avancement`) ;
c'est lui seul que la console montre en INFO. Il atterrit AUSSI dans les
fichiers : les jalons d'étape y découpent enfin les 25 000 lignes d'une journée.

**Console NON BLOQUANTE (2026-09-06).** Sélectionner du texte dans la console
Windows (mode QuickEdit) SUSPEND les écritures ; un handler console direct
bloque alors dans son `emit`, et le verrou de `logging.Handler` fige derrière
lui tous les threads qui loggent, GUI compris. D'où la FILE entre les threads et
la console (`QueueHandler` + `QueueListener`) : seul le listener attend. Si la
file sature, l'écho console est LÂCHÉ plutôt que de bloquer — les fichiers,
hors de la file, ne perdent rien.
"""

from __future__ import annotations

import functools
import inspect
import logging
import queue
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import QueueHandler, QueueListener

import coloredlogs

from src.config import DEBUG, LOG_LEVEL, LOGS_DIR

#: Le logger du récit : seul à passer en console au niveau INFO (hors debug).
RECIT = "recit"

#: Bibliothèques tierces plafonnées à WARNING : leur INFO/DEBUG (une ligne par
#: requête HTTP chez httpx) noierait les fichiers sans rien diagnostiquer.
_TIERS_BRUYANTS = (
    "asyncio",
    "charset_normalizer",
    "crawl4ai",
    "filelock",
    "hpack",
    "httpcore",
    "httpx",
    "patchright",
    "PIL",
    "playwright",
    "urllib3",
    "websockets",
)

#: File et listener PARTAGÉS par tout le process. Un seul handler console
#: derrière la file, monté sur la RACINE : chaque ligne s'affiche une fois.
_FILE_CONSOLE: queue.Queue | None = None
_LISTENER: QueueListener | None = None

#: Au-delà, on abandonne l'écho console plutôt que de bloquer l'application.
#: 10 000 lignes = de quoi absorber une sélection de texte de plusieurs minutes.
_TAILLE_FILE = 10_000

_FORMAT_FICHIER = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_FORMAT_RECIT = "%(asctime)s %(message)s"
_FORMAT_AUTRES = "%(asctime)s %(levelname)s %(name)s — %(message)s"
_HEURE = "%H:%M:%S"

_configure = False
#: Mode de la console, LU À CHAQUE LIGNE (jamais figé à la configuration) : un
#: script peut donc passer en « tout » après que l'import d'un module a déjà
#: configuré les journaux par défaut.
_console_tout = DEBUG


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


class _FiltreConsole(logging.Filter):
    """Récit + WARNING/ERROR de tous ; tout l'INFO en mode « tout »."""

    def filter(self, record: logging.LogRecord) -> bool:
        if _console_tout:
            return record.levelno >= logging.getLevelName(LOG_LEVEL)
        return est_du_recit(record) or record.levelno >= logging.WARNING


class _FormatConsole(logging.Formatter):
    """Le récit en format court (heure + message) ; le reste avec niveau et nom
    du module — un avertissement doit dire d'où il vient."""

    def __init__(self) -> None:
        super().__init__()
        self._recit = coloredlogs.ColoredFormatter(_FORMAT_RECIT, datefmt=_HEURE)
        self._autres = coloredlogs.ColoredFormatter(_FORMAT_AUTRES, datefmt=_HEURE)

    def format(self, record: logging.LogRecord) -> str:
        return (self._recit if est_du_recit(record) else self._autres).format(record)


def est_du_recit(record: logging.LogRecord) -> bool:
    return record.name == RECIT or record.name.startswith(RECIT + ".")


def _handlers_console(logger: logging.Logger) -> list[logging.Handler]:
    """Handlers qui écrivent DIRECTEMENT sur la console (donc bloquants)."""
    return [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler | QueueHandler)
    ]


def _monter_console(racine: logging.Logger) -> None:
    """Pose l'écho console derrière la file partagée. Idempotent.

    Un handler console direct déjà présent sur la racine (un `basicConfig`
    appelé à l'import d'un module) est retiré : laissé en place, il bloquerait
    sous QuickEdit ET doublerait chaque ligne.
    """
    global _FILE_CONSOLE, _LISTENER

    for handler in _handlers_console(racine):
        racine.removeHandler(handler)
    if _LISTENER is not None:
        return
    try:
        from humanfriendly.terminal import enable_ansi_support

        enable_ansi_support()  # conhost classique : couleurs ANSI à activer
    except (ImportError, OSError):
        pass
    console = logging.StreamHandler(sys.stderr)
    console.addFilter(_FiltreConsole())
    console.setFormatter(_FormatConsole())
    _FILE_CONSOLE = queue.Queue(maxsize=_TAILLE_FILE)
    _LISTENER = QueueListener(_FILE_CONSOLE, console, respect_handler_level=True)
    _LISTENER.start()  # thread daemon (pas de blocage à la fermeture)
    racine.addHandler(_ConsoleNonBloquante(_FILE_CONSOLE))


def _monter_fichiers(racine: logging.Logger) -> None:
    horodatage = datetime.now().strftime("%Y%m%d")
    formatter = logging.Formatter(_FORMAT_FICHIER)
    niveau = min(logging.INFO, logging.getLevelName(LOG_LEVEL))
    for nom, seuil in (("scraper", niveau), ("errors", logging.ERROR)):
        handler = logging.FileHandler(LOGS_DIR / f"{horodatage}_{nom}.log", encoding="utf-8")
        handler.setLevel(seuil)
        handler.setFormatter(formatter)
        racine.addHandler(handler)


def _installer(racine: logging.Logger, *, fichiers: bool) -> None:
    racine.setLevel(min(logging.INFO, logging.getLevelName(LOG_LEVEL)))
    _monter_console(racine)
    if fichiers:
        _monter_fichiers(racine)
    for nom in _TIERS_BRUYANTS:
        logging.getLogger(nom).setLevel(logging.WARNING)


def configurer_journaux(console: str | None = None) -> None:
    """Point d'entrée UNIQUE de la configuration des journaux. Idempotent.

    `console` : « recit » (défaut hors debug) ou « tout » (tout l'INFO, ce que
    veulent les scripts en ligne de commande, dont la sortie EST le récit). Le
    mode peut changer après coup ; les handlers, eux, ne sont posés qu'une fois.

    Sous pytest, rien n'est installé : la suite versait ses doubles de test
    (« navigateur mort », « le gabarit du site a changé ») dans les journaux de
    production — 23 Mo le seul 2026-09-08. Un journal pollué est PIRE qu'un
    journal absent : il a l'air d'être une preuve.
    """
    global _configure, _console_tout

    if console is not None:
        if console not in ("recit", "tout"):
            raise ValueError(f"mode console inconnu : {console!r}")
        _console_tout = console == "tout"
    if _configure or "pytest" in sys.modules:
        return
    _configure = True
    # Même règle que pour stdout : `reconfigure`, JAMAIS re-wrapper. Sans elle,
    # un stderr redirigé (sous-processus des certifs) est en cp1252 et le
    # premier « ▶ » lève UnicodeEncodeError dans le listener.
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    _installer(logging.getLogger(), fichiers=True)


def ajouter_journal_fichier(chemin, niveau: int = logging.INFO) -> logging.Handler | None:
    """Journal PROPRE à un run (en plus des journaux du jour), sur la racine.

    Remplace les `logging.basicConfig(filename=…)` des scripts, devenus NO-OP
    dès que la racine a des handlers — c'est-à-dire toujours. Rien sous pytest.
    """
    if "pytest" in sys.modules:
        return None
    handler = logging.FileHandler(chemin, encoding="utf-8")
    handler.setLevel(niveau)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logging.getLogger().addHandler(handler)
    return handler


# ── Récit ────────────────────────────────────────────────────────────────────

_recit = logging.getLogger(RECIT)
_verrou = threading.Lock()
_profondeur = 0
#: tâche → (palier de 10 % déjà annoncé, instant de la dernière ligne, total)
_avancements: dict[str, tuple[int, float, int]] = {}
_derniere_annonce: tuple[str, str] | None = None

#: Une ligne d'avancement au plus tous les 10 %, et au moins toutes les 30 s.
_PAS_POURCENT = 10
_PAS_SECONDES = 30.0


def _retrait() -> str:
    return "  " * _profondeur


def _duree(depuis: float) -> str:
    s = int(time.monotonic() - depuis)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {s % 3600 // 60:02d} min"


def annoncer(message: str) -> None:
    """Une ligne de récit, au retrait de l'étape en cours."""
    _recit.info(f"{_retrait()}{message}")


class Etape:
    """Ce que l'étape dira en se refermant : ✔ si complète, ⚠ sinon."""

    def __init__(self) -> None:
        self.complete = True
        self.motif = ""

    def conclure(self, bilan):
        """Reprend le verdict d'un `Bilan` (booléen d'HONNÊTETÉ, cf. services)
        et le rend tel quel, pour `return e.conclure(bilan)`."""
        self.complete = bool(bilan.complete)
        self.motif = bilan.motif or ""
        return bilan


@contextmanager
def etape(nom: str, sujet: str = "") -> Iterator[Etape]:
    """Annonce une étape (▶ au premier niveau, ▸ en dessous) et sa fin.

    Les étapes s'emboîtent : un cycle annonce ses flux, l'Identité ses passages
    par morceau. La profondeur est une PILE DE PROCESS (comme `run_scope`), pas
    une contextvar : un flux entre sur le fil d'un worker et continue sur celui
    de la boucle asyncio.
    """
    global _profondeur, _derniere_annonce

    retrait = _retrait()
    marque = "▶" if _profondeur == 0 else "▸"
    # Emboîtée, l'étape porte le sujet de son parent : ne pas le répéter.
    suffixe = f" — {sujet}" if sujet and _profondeur == 0 else ""
    _recit.info(f"{retrait}{marque} {nom}{suffixe}")
    with _verrou:
        _profondeur += 1
        _derniere_annonce = None
    debut = time.monotonic()
    e = Etape()
    try:
        yield e
    except BaseException as exc:
        _recit.error(f"{retrait}✖ {nom} — {type(exc).__name__} : {exc} ({_duree(debut)})")
        raise
    else:
        if e.complete:
            _recit.info(f"{retrait}✔ {nom} — {_duree(debut)}")
        else:
            _recit.warning(f"{retrait}⚠ {nom} — incomplet : {e.motif} ({_duree(debut)})")
    finally:
        with _verrou:
            _profondeur = max(0, _profondeur - 1)
            _derniere_annonce = None


def _sujet_artiste(args, kwargs) -> str:
    """Convention des flux : `(runtime | dm, artist, …)` — l'artiste en 2ᵉ."""
    artist = args[1] if len(args) > 1 else kwargs.get("artist", kwargs.get("artiste"))
    return getattr(artist, "name", "") if artist is not None else ""


def _verdict_bilan(resultat) -> tuple[bool, str]:
    """Un `Bilan` porte son verdict (`complete`, `motif`) ; tout autre résultat
    (liste, compte…) vaut « terminé » — seule une exception dit le contraire."""
    if hasattr(resultat, "complete"):
        return bool(resultat.complete), resultat.motif or ""
    return True, ""


def etape_de_flux(
    nom: str,
    *,
    nom_depuis: str | None = None,
    sujet: Callable[[tuple, dict], str] = _sujet_artiste,
    verdict: Callable[[object], tuple[bool, str]] = _verdict_bilan,
) -> Callable:
    """Décore un flux pour qu'il s'annonce, dise sa durée et son verdict.

    Par défaut : `run(runtime, artist, …) -> Bilan` — le sujet est le nom de
    l'artiste, le verdict celui du bilan. `nom_depuis` = un argument nommé qui
    REMPLACE le nom (l'enrichissement réutilisé par l'Identité ne s'annonce pas
    « Enrichissement ») ; `sujet(args, kwargs)` et `verdict(résultat)` adaptent
    les flux d'une autre forme (un script de certifs rend `(code, sortie)`)."""

    def libelle(args, kwargs) -> tuple[str, str]:
        titre = (kwargs.get(nom_depuis) or nom) if nom_depuis else nom
        return titre, sujet(args, kwargs)

    def conclure(e: Etape, resultat):
        e.complete, e.motif = verdict(resultat)
        return resultat

    def deco(fn):
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def enveloppe_async(*args, **kwargs):
                with etape(*libelle(args, kwargs)) as e:
                    return conclure(e, await fn(*args, **kwargs))

            return enveloppe_async

        @functools.wraps(fn)
        def enveloppe(*args, **kwargs):
            with etape(*libelle(args, kwargs)) as e:
                return conclure(e, fn(*args, **kwargs))

        return enveloppe

    return deco


def avancement(courant: int, total: int, libelle: str = "", tache: str = "") -> None:
    """L'avancement d'un flux, ESPACÉ pour la console.

    `total > 1` : une ligne au franchissement de chaque palier de 10 % ou après
    30 s de silence (les flux appellent le hook avant CHAQUE morceau — le
    répéter tel quel rendrait la console aussi illisible que le fichier).
    `total <= 1` : c'est une annonce de sous-couche (« Deezer — Artiste et
    catalogue ») ou un message (certifs) ; répétée à l'identique, elle ne
    s'affiche qu'une fois.
    """
    global _derniere_annonce

    with _verrou:
        if total <= 1:
            cle = (tache, libelle)
            if cle == _derniere_annonce:
                return
            _derniere_annonce = cle
            texte = f"▸ {tache} — {libelle}" if tache and libelle else f"▸ {tache or libelle}"
            _recit.info(f"{_retrait()}{texte}")
            return
        maintenant = time.monotonic()
        palier = courant * 100 // total // _PAS_POURCENT
        etat = _avancements.get(tache)
        if etat is None or etat[2] != total or courant == 0:
            _avancements[tache] = (palier, maintenant, total)
            if courant == 0:
                _recit.info(f"{_retrait()}  {tache} : {total} à traiter")
            return
        if palier <= etat[0] and maintenant - etat[1] < _PAS_SECONDES:
            return
        _avancements[tache] = (max(palier, etat[0]), maintenant, total)
        pourcent = courant * 100 // total
        suite = f" — {libelle}" if libelle else ""
        _recit.info(f"{_retrait()}  {tache} {courant}/{total} ({pourcent} %){suite}")


# ── Accès ────────────────────────────────────────────────────────────────────


class Logger:
    """Gestionnaire de logs (compat : `get_logger`, `log_api`)."""

    _loggers: dict[str, logging.Logger] = {}

    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        """Le logger du module. Configure les journaux au premier appel : un
        process qui n'a rien configuré (script lancé à la main) a quand même
        ses fichiers et son récit — le filet, pas la règle."""
        configurer_journaux()
        if name in cls._loggers:
            return cls._loggers[name]
        logger = logging.getLogger(name)
        # Le filtrage se fait aux HANDLERS (fichier INFO, console filtrée) ; le
        # niveau DEBUG laisse passer ce que capturent les tests (`caplog`).
        logger.setLevel(logging.DEBUG)
        cls._loggers[name] = logger
        return logger

    @classmethod
    def log_scraping_error(cls, track_title: str, error: str, source: str):
        """Log spécifique pour les erreurs de scraping"""
        logger = cls.get_logger("scraping_errors")
        logger.error(f"[{source}] Erreur sur '{track_title}': {error}")

    @classmethod
    def log_api_call(cls, api_name: str, endpoint: str, success: bool):
        """Trace d'un appel d'API. En INFO dans les DEUX cas : `success=False`
        signifie le plus souvent « pas de correspondance » (une absence), et les
        vraies pannes sont journalisées par l'appelant, avec leur exception —
        en ERROR, cette trace remplissait le journal d'erreurs et la console."""
        logger = cls.get_logger("api_calls")
        if success:
            logger.info(f"[{api_name}] Appel réussi: {endpoint}")
        else:
            logger.info(f"[{api_name}] Appel sans résultat: {endpoint}")


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
