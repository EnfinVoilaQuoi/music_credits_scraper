"""Le pont entre les services et l'interface : des SIGNAUX Qt, jamais un widget
touché depuis un worker.

Deux outils :

  · `Taches` — le centre des tâches de la session. `lancer(cle, titre, corps)`
    exécute `corps(hooks)` par `lifecycle.run_worker` (thread daemon ENREGISTRÉ,
    joint à la fermeture) ; la progression, la fin et le récit arrivent sur le
    fil de l'interface par signal. Le bilan d'un service (`Bilan.complete`,
    booléen d'HONNÊTETÉ) décide de l'état final : un run coupé est « partiel »,
    même après 300 écritures.
  · `en_fond(fonction, rappel)` — un appel ponctuel hors du fil de l'interface
    (charger un artiste, compter « À trancher »), par `lifecycle.start_worker`.

Un seul run qui ÉCRIT à la fois, comme la GUI CustomTkinter (`is_scraping`) :
deux flux qui écrivent la même discographie se marcheraient dessus.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import StrEnum

from PyQt6.QtCore import QObject, pyqtSignal

from src.concurrency import lifecycle
from src.services.runtime import Hooks
from src.utils.logger import ecouter_recit, get_logger, ne_plus_ecouter_recit

logger = get_logger(__name__)


class Etat(StrEnum):
    EN_COURS = "en cours"
    COMPLET = "terminé"
    PARTIEL = "partiel"
    ARRETE = "arrêté"
    ERREUR = "erreur"


@dataclass
class Tache:
    cle: str
    titre: str
    debut: float = field(default_factory=time.monotonic)
    fin: float | None = None
    courant: int = 0
    total: int = 0
    etape: str = ""
    libelle: str = ""
    etat: Etat = Etat.EN_COURS
    resultat: object = None
    message: str = ""
    arret_demande: bool = False
    journal: list[str] = field(default_factory=list)

    @property
    def pourcentage(self) -> int | None:
        if self.total <= 1:
            return None
        return min(100, self.courant * 100 // self.total)

    @property
    def duree(self) -> float:
        return (self.fin or time.monotonic()) - self.debut

    @property
    def active(self) -> bool:
        return self.etat is Etat.EN_COURS


def etat_final(resultat, erreur: str | None, arret_demande: bool) -> tuple[Etat, str]:
    """L'état final d'un run et son message — PUR, testé.

    Le bilan d'un service dit lui-même s'il est complet (`complete`) et le
    résume (`resume()`) ; un résultat sans bilan est complet."""
    if erreur:
        return Etat.ERREUR, erreur
    message = ""
    resume = getattr(resultat, "resume", None)
    if callable(resume):
        try:
            message = resume()
        except (AttributeError, TypeError, ValueError) as e:
            message = f"(résumé illisible : {e})"
    complet = getattr(resultat, "complete", True)
    if complet:
        return Etat.COMPLET, message
    if arret_demande:
        return Etat.ARRETE, message or "Arrêté à la demande"
    motif = getattr(resultat, "motif", "")
    return Etat.PARTIEL, message or motif or "Run incomplet"


class _RecitVersSignal(logging.Handler):
    """Le récit (logger `recit`) vers un signal : `emit` ne bloque jamais."""

    def __init__(self, signal) -> None:
        super().__init__(logging.INFO)
        self._signal = signal
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._signal.emit(self.format(record))
        except RuntimeError:  # objet Qt détruit pendant la fermeture
            pass


class Taches(QObject):
    """Les runs de la session."""

    changee = pyqtSignal(str)
    terminee = pyqtSignal(str)
    recit = pyqtSignal(str)

    # Signaux INTERNES, émis depuis les workers : Qt les remet sur le fil de
    # l'objet (connexion « queued »), seul endroit où l'état est modifié.
    _progression = pyqtSignal(str, int, int, str, str)
    _fin = pyqtSignal(str, object, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.taches: dict[str, Tache] = {}
        self._progression.connect(self._sur_progression)
        self._fin.connect(self._sur_fin)
        self.recit.connect(self._au_journal)
        self._ecouteur = ecouter_recit(_RecitVersSignal(self.recit))

    # ── lecture ──
    def tache(self, cle: str) -> Tache | None:
        return self.taches.get(cle)

    def en_cours(self) -> list[Tache]:
        return [t for t in self.taches.values() if t.active]

    @property
    def occupe(self) -> bool:
        return bool(self.en_cours())

    # ── actions ──
    def hooks(self, tache: Tache) -> Hooks:
        def progress(courant: int, total: int, libelle: str, etape: str = "") -> None:
            self._progression.emit(tache.cle, courant, total, libelle or "", etape or "")

        return Hooks(
            progress=progress,
            should_stop=lambda: lifecycle.stop_requested() or tache.arret_demande,
        )

    def lancer(self, cle: str, titre: str, corps: Callable[[Hooks], object]) -> bool:
        """Lance `corps(hooks)` en fond. Refuse (False) si un run écrit déjà."""
        if self.occupe:
            logger.info(f"Run « {titre} » refusé : un run est déjà en cours")
            return False
        tache = Tache(cle, titre)
        self.taches[cle] = tache
        hooks = self.hooks(tache)
        future: Future = lifecycle.run_worker(lambda: corps(hooks), name=f"qt:{cle}")
        future.add_done_callback(lambda f: self._fin.emit(cle, *self._issue(f)))
        self.changee.emit(cle)
        return True

    @staticmethod
    def _issue(future: Future) -> tuple[object, str | None]:
        if future.cancelled():
            return None, "annulé à la fermeture"
        exc = future.exception()
        if exc is not None:
            logger.error(f"Run en échec : {exc!r}")
            return None, f"{type(exc).__name__} : {exc}"
        return future.result(), None

    def demander_arret(self, cle: str) -> None:
        """Arrêt ENTRE deux morceaux (les services testent `should_stop`)."""
        tache = self.taches.get(cle)
        if tache and tache.active:
            tache.arret_demande = True
            tache.etape = "Arrêt demandé — fin du morceau en cours…"
            self.changee.emit(cle)

    def fermer(self) -> None:
        ne_plus_ecouter_recit(self._ecouteur)

    # ── fil de l'interface ──
    def _sur_progression(self, cle: str, courant: int, total: int, libelle: str, etape: str):
        tache = self.taches.get(cle)
        if tache is None or not tache.active:
            return
        tache.courant, tache.total, tache.libelle = courant, total, libelle
        if etape and not tache.arret_demande:
            tache.etape = etape
        self.changee.emit(cle)

    def _sur_fin(self, cle: str, resultat, erreur) -> None:
        tache = self.taches.get(cle)
        if tache is None:
            return
        tache.fin = time.monotonic()
        tache.resultat = resultat
        tache.etat, tache.message = etat_final(resultat, erreur, tache.arret_demande)
        self.changee.emit(cle)
        self.terminee.emit(cle)

    def _au_journal(self, ligne: str) -> None:
        for tache in self.en_cours():
            tache.journal.append(ligne)


class _Appel(QObject):
    ok = pyqtSignal(object)
    echec = pyqtSignal(str)


#: Les appels en cours gardés vivants (sans référence, Qt détruirait l'objet
#: avant que le signal n'arrive).
_appels: set[_Appel] = set()


def en_fond(
    fonction: Callable[[], object],
    rappel: Callable[[object], None],
    en_cas_d_echec: Callable[[str], None] | None = None,
    nom: str = "qt:fond",
) -> None:
    """Exécute `fonction()` hors du fil de l'interface, puis `rappel(résultat)`
    SUR le fil de l'interface."""
    appel = _Appel()
    _appels.add(appel)

    def fini(_=None):
        _appels.discard(appel)

    appel.ok.connect(rappel)
    appel.ok.connect(fini)
    if en_cas_d_echec is not None:
        appel.echec.connect(en_cas_d_echec)
    appel.echec.connect(fini)

    def corps():
        try:
            resultat = fonction()
        except Exception as e:  # dernier ressort : remonté à l'interface, tracé
            logger.exception(f"{nom} : échec")
            appel.echec.emit(f"{type(e).__name__} : {e}")
            return
        appel.ok.emit(resultat)

    lifecycle.start_worker(corps, name=nom)
