"""Console de log NON BLOQUANTE (cf. `src/utils/logger`).

L'application se figeait entièrement dès qu'on sélectionnait du texte dans la
console Windows (mode QuickEdit) : l'écriture console est suspendue, le handler
bloque dans son `emit`, et le verrou de `logging.Handler` fige derrière lui tous
les threads qui loggent — GUI compris. Ctrl+C sortait du mode marque et tout
repartait, ce qui donnait l'illusion que la copie « relançait » le programme.

Ces tests tiennent les trois propriétés du remède : plus aucun handler console
DIRECT, un seul écho (pas de doublon), et une file pleine qui LÂCHE au lieu de
bloquer.
"""

import logging
import queue

import pytest

import src.utils.logger as mod


@pytest.fixture
def logging_propre(monkeypatch):
    """Isole l'état global de logging (racine, cache de loggers, listener)."""
    racine = logging.getLogger()
    handlers_initiaux = list(racine.handlers)
    listener = mod._LISTENER
    file_console = mod._FILE_CONSOLE
    cache = dict(mod.Logger._loggers)

    racine.handlers = []
    mod._LISTENER = None
    mod._FILE_CONSOLE = None
    mod.Logger._loggers = {}
    monkeypatch.setattr(mod, "DEBUG", True)

    yield racine

    if mod._LISTENER is not None and mod._LISTENER is not listener:
        mod._LISTENER.stop()
    for nom in list(mod.Logger._loggers):
        logging.getLogger(nom).handlers = []
    racine.handlers = handlers_initiaux
    mod._LISTENER = listener
    mod._FILE_CONSOLE = file_console
    mod.Logger._loggers = cache


def _consoles_directes(logger):
    return [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler | logging.handlers.QueueHandler)
    ]


class TestConsoleDeportee:
    def test_aucun_handler_console_direct_ne_subsiste(self, logging_propre):
        """Le point du correctif : plus rien n'écrit sur la console depuis le
        thread appelant."""
        log = mod.get_logger("essai.deporte")

        assert _consoles_directes(log) == []
        assert _consoles_directes(logging_propre) == []

    def test_un_seul_echo_console(self, logging_propre):
        """Un handler par logger derrière la MÊME file afficherait chaque ligne
        autant de fois qu'il existe de loggers."""
        mod.get_logger("essai.a")
        mod.get_logger("essai.b")
        mod.get_logger("essai.c")

        files = [h for h in logging_propre.handlers if isinstance(h, mod._ConsoleNonBloquante)]
        assert len(files) == 1
        assert len(mod._LISTENER.handlers) == 1

    def test_un_handler_pose_sur_la_racine_est_aussi_deporte(self, logging_propre):
        """Cas réel : `logging.basicConfig()` au chargement d'un module pose un
        `StreamHandler` sur la racine. Ignoré, il continuait de bloquer — et
        doublait l'affichage."""
        logging_propre.addHandler(logging.StreamHandler())

        mod.get_logger("essai.racine")

        assert _consoles_directes(logging_propre) == []

    def test_les_enregistrements_atteignent_la_console(self, logging_propre):
        """Déporter ne doit pas faire disparaître les logs."""
        log = mod.get_logger("essai.transit")
        recu = []

        class _Capture(logging.Handler):
            def emit(self, record):
                recu.append(record.getMessage())

        mod._LISTENER.stop()
        mod._LISTENER.handlers = (_Capture(),)
        mod._LISTENER.start()

        log.warning("message de contrôle")
        mod._LISTENER.stop()  # vide la file avant l'assertion

        assert "message de contrôle" in recu


class TestFilePleine:
    def test_une_file_pleine_lache_au_lieu_de_lever(self):
        """`QueueHandler` remonterait `queue.Full` à `handleError`, qui écrit sur
        `sys.stderr` — la console bloquée qu'on cherche justement à éviter."""
        pleine = queue.Queue(maxsize=1)
        handler = mod._ConsoleNonBloquante(pleine)
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "m", None, None)

        handler.enqueue(record)  # remplit
        handler.enqueue(record)  # doit être lâché, sans exception

        assert pleine.qsize() == 1
