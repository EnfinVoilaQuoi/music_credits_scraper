"""Console de log : NON BLOQUANTE, UNIQUE, et en mode récit (cf. `src/utils/logger`).

Deux défauts tenus ici :

* **2026-09-06** — l'application se figeait dès qu'on sélectionnait du texte
  dans la console Windows (QuickEdit suspend les écritures, le handler bloque
  dans `emit`, et le verrou de `logging.Handler` fige tous les threads qui
  loggent). Remède : une FILE entre les threads et la console, qui LÂCHE quand
  elle sature.
* **2026-09-30** — la console était muette : son handler n'était posé que si
  `DEBUG=true`. Le seul texte visible venait de `logging.lastResort` (un
  WARNING orphelin d'un module sans handler). Remède : configuration posée sur
  la RACINE, console toujours active, filtrée en « récit + avertissements ».
"""

import logging
import queue
import re

import pytest

import src.utils.logger as mod

#: Les codes couleur ANSI que pose `coloredlogs`.
_SANS_COULEUR = re.compile(chr(27) + r"\[[0-9;]*m")


@pytest.fixture
def racine_propre(monkeypatch):
    """Isole l'état global (handlers de la racine, listener, mode console)."""
    racine = logging.getLogger()
    handlers_initiaux = list(racine.handlers)
    niveau_initial = racine.level
    listener, file_console = mod._LISTENER, mod._FILE_CONSOLE
    racine.handlers = []
    mod._LISTENER = None
    mod._FILE_CONSOLE = None
    monkeypatch.setattr(mod, "_console_tout", False)

    yield racine

    if mod._LISTENER is not None and mod._LISTENER is not listener:
        mod._LISTENER.stop()
    racine.handlers = handlers_initiaux
    racine.setLevel(niveau_initial)
    mod._LISTENER, mod._FILE_CONSOLE = listener, file_console


def _consoles_directes(logger):
    return [
        h
        for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler | logging.handlers.QueueHandler)
    ]


def _capturer(racine):
    """Remplace le handler console derrière la file par une capture (en
    gardant le FILTRE et le FORMAT réels)."""
    vrai = mod._LISTENER.handlers[0]
    recu = []

    class _Capture(logging.Handler):
        def emit(self, record):
            recu.append(_SANS_COULEUR.sub("", self.format(record)))

    capture = _Capture()
    for f in vrai.filters:
        capture.addFilter(f)
    capture.setFormatter(vrai.formatter)
    mod._LISTENER.stop()
    mod._LISTENER.handlers = (capture,)
    mod._LISTENER.start()

    def vider():
        mod._LISTENER.stop()
        return recu

    return vider


class TestConsoleDeportee:
    def test_aucun_handler_console_direct(self, racine_propre):
        mod._installer(racine_propre, fichiers=False)
        assert _consoles_directes(racine_propre) == []

    def test_un_seul_echo_console(self, racine_propre):
        mod._installer(racine_propre, fichiers=False)
        mod._installer(racine_propre, fichiers=False)  # idempotent
        files = [h for h in racine_propre.handlers if isinstance(h, mod._ConsoleNonBloquante)]
        assert len(files) == 1
        assert len(mod._LISTENER.handlers) == 1

    def test_un_basicconfig_d_import_est_retire(self, racine_propre):
        """Cas réel : `logging.basicConfig()` au chargement d'un module pose un
        `StreamHandler` direct sur la racine — bloquant ET doublant."""
        racine_propre.addHandler(logging.StreamHandler())
        mod._installer(racine_propre, fichiers=False)
        assert _consoles_directes(racine_propre) == []

    def test_un_logger_brut_atteint_la_console(self, racine_propre):
        """Le défaut du 2026-09-30 : SongBPM async (`logging.getLogger`) n'avait
        aucun handler — ses avertissements sortaient par `lastResort`, nus."""
        mod._installer(racine_propre, fichiers=False)
        vider = _capturer(racine_propre)
        logging.getLogger("essai.brut").warning("Aucune correspondance")
        lignes = vider()
        assert any("WARNING essai.brut — Aucune correspondance" in ligne for ligne in lignes)

    def test_les_tiers_bruyants_sont_plafonnes(self, racine_propre):
        mod._installer(racine_propre, fichiers=False)
        assert logging.getLogger("httpx").level == logging.WARNING


class TestFiltreRecit:
    def test_par_defaut_recit_et_avertissements_seulement(self, racine_propre):
        mod._installer(racine_propre, fichiers=False)
        vider = _capturer(racine_propre)
        logging.getLogger("recit").info("▶ Identité — Isha")
        logging.getLogger("essai.bavard").info("• Discogs ID: None")
        logging.getLogger("essai.bavard").warning("parseur cassé")
        lignes = vider()
        assert any(ligne.endswith("▶ Identité — Isha") for ligne in lignes)
        assert not any("Discogs ID" in ligne for ligne in lignes)
        assert any("parseur cassé" in ligne for ligne in lignes)

    def test_le_recit_est_en_format_court(self, racine_propre):
        mod._installer(racine_propre, fichiers=False)
        vider = _capturer(racine_propre)
        logging.getLogger("recit").info("▶ Streams — Isha")
        (ligne,) = vider()
        assert "recit" not in ligne and "INFO" not in ligne

    def test_mode_tout_montre_l_info_de_tous(self, racine_propre, monkeypatch):
        """`DEBUG=true` / `configurer_journaux(console="tout")` / `cli -v`."""
        mod._installer(racine_propre, fichiers=False)
        monkeypatch.setattr(mod, "_console_tout", True)
        vider = _capturer(racine_propre)
        logging.getLogger("essai.bavard").info("• Discogs ID: None")
        assert any("Discogs ID" in ligne for ligne in vider())

    def test_le_mode_se_change_apres_coup(self, monkeypatch):
        """Un script passe en « tout » APRÈS que l'import d'un module a déjà
        configuré les journaux : le mode est lu à chaque ligne, pas figé."""
        monkeypatch.setattr(mod, "_console_tout", False)
        mod.configurer_journaux(console="tout")
        assert mod._console_tout is True
        with pytest.raises(ValueError):
            mod.configurer_journaux(console="bavard")


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
