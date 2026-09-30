"""Le câblage des journaux : UN point de configuration, aucun trou, aucun test
dans les journaux réels.

**②a (2026-09-09)** — la suite de tests écrivait dans les journaux RÉELS
(23 Mo le seul 8 septembre, un journal d'erreurs à ~95 % de doubles de test).
Un journal pollué est PIRE qu'un journal absent : il a l'air d'être une preuve.

**②b (2026-09-09, puis 2026-09-30)** — des modules n'écrivaient NULLE PART :
les handlers fichier étaient posés logger par logger dans `get_logger`, et un
`logging.getLogger` brut ne les héritait pas. Le crible d'alors (« aucun
logger brut ») laissait passer tout module qui importait QUELQUE CHOSE de
`src.utils.logger` — SongBPM async importait `log_api` et journalisait par un
logger brut : ses lignes n'atteignaient aucun fichier, et ses avertissements
sortaient en console par `logging.lastResort`, nus. Depuis le 2026-09-30 les
handlers vivent sur la RACINE : tout logger y propage, le trou n'existe plus
par construction. Ce qui reste à garder, c'est l'UNICITÉ du point de
configuration — chaque handler posé ailleurs a été, un jour, un doublon, une
console bloquante ou un fichier jamais fermé.
"""

import ast
import logging
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

#: Seul `logger.py` configure les journaux.
_CONFIGURATION = {"addHandler", "basicConfig", "install"}


def _sites_de_configuration() -> list[str]:
    fautifs = []
    for chemin in SRC.rglob("*.py"):
        if chemin.name == "logger.py" and chemin.parent.name == "utils":
            continue
        arbre = ast.parse(chemin.read_text(encoding="utf-8"), filename=str(chemin))
        for noeud in ast.walk(arbre):
            appel = (
                isinstance(noeud, ast.Call)
                and isinstance(noeud.func, ast.Attribute)
                and noeud.func.attr in _CONFIGURATION
                # `coloredlogs.install`, pas n'importe quel `.install()`
                and (
                    noeud.func.attr != "install"
                    or (
                        isinstance(noeud.func.value, ast.Name)
                        and noeud.func.value.id == "coloredlogs"
                    )
                )
            )
            propagate = isinstance(noeud, ast.Assign) and any(
                isinstance(c, ast.Attribute) and c.attr == "propagate" for c in noeud.targets
            )
            if appel or propagate:
                fautifs.append(f"{chemin.relative_to(SRC).as_posix()}:{noeud.lineno}")
    return fautifs


class TestUnSeulPointDeConfiguration:
    def test_aucun_handler_pose_hors_du_module_de_journaux(self):
        """`addHandler`, `basicConfig`, `coloredlogs.install`, `propagate = …` :
        passer par `configurer_journaux` / `ajouter_journal_fichier`."""
        fautifs = _sites_de_configuration()
        assert fautifs == [], f"configuration de journaux hors de utils/logger.py : {fautifs}"

    def test_le_crible_voit_bien_quelque_chose(self):
        """Un crible qui ne trouverait rien passerait au vert pour de mauvaises
        raisons — le travers du garde-fou muet."""
        assert (SRC / "utils" / "logger.py").exists()
        assert len(list(SRC.rglob("*.py"))) > 50
        source = (SRC / "utils" / "logger.py").read_text(encoding="utf-8")
        assert "addHandler" in source  # le module exempté configure bien, lui


class TestAucunHandlerFichierSousPytest:
    """La garde ②a, vérifiée sur l'état réel : nous SOMMES sous pytest."""

    def test_get_logger_ne_configure_rien_sous_pytest(self):
        from src.utils.logger import get_logger

        logger = get_logger("test_cablage_journal_temoin")
        assert [h for h in logger.handlers if isinstance(h, logging.FileHandler)] == []

    def test_la_racine_n_a_aucun_handler_fichier(self):
        racine = logging.getLogger()
        nos_fichiers = [
            h
            for h in racine.handlers
            if isinstance(h, logging.FileHandler)
            # celui de pytest (`--log-file`, le périphérique nul par défaut) n'est pas le nôtre
            and not type(h).__module__.startswith("_pytest")
        ]
        assert nos_fichiers == []

    def test_un_journal_de_run_n_est_pas_ouvert_sous_pytest(self, tmp_path):
        from src.utils.logger import ajouter_journal_fichier

        assert ajouter_journal_fichier(tmp_path / "run.log") is None
        assert not (tmp_path / "run.log").exists()
