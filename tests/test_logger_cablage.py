"""Deux défauts opposés du même câblage de logs, tous deux mesurés le 2026-09-09.

**②a** — la suite de tests écrivait dans les journaux RÉELS. `utils/logger.py`
attachait ses `FileHandler` sans la garde `pytest` que portent les
reconfigurations de `stdout`. Mesuré sur la seule journée du 8 : 23 Mo,
190 128 lignes, et un journal d'erreurs à ~95 % de doubles de test
(« navigateur mort », « page morte », « BPI : le gabarit du site a changé »).
Un journal pollué est PIRE qu'un journal absent : il a l'air d'être une preuve,
et qui l'ouvre pour diagnostiquer une panne court après un fantôme.

**②b** — quatorze modules n'écrivaient NULLE PART. Les handlers fichier sont
posés sur les loggers NOMMÉS par `Logger.get_logger` ; un `logging.getLogger`
brut ne les hérite pas. Le témoin : le mot « relevance » n'apparaissait pas une
seule fois dans les 23 Mo, alors que le scraper Spotify ID le journalise à
chaque sélection. **C'est ce trou qui a rendu les 34 % d'identifiants faux
invisibles** — la preuve n'était jamais écrite.
"""

import ast
import logging
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

#: Les deux seules exceptions légitimes, et leurs raisons.
#: · `logger.py` EST le fournisseur — il ne peut pas s'importer lui-même ;
#: · `models/track.py` est du DOMAINE : y importer `src.utils.logger` déclenche
#:   `src.utils.__init__` → `data_enricher` → `src.api` → `src.models`, donc un
#:   ImportError sur un module partiellement initialisé. VÉRIFIÉ, pas supposé —
#:   c'est la raison qui a fait de `src/observability` un package séparé.
EXCEPTIONS = {"logger.py", "track.py"}


def _modules_avec_logger_brut() -> list[str]:
    """Modules qui appellent `logging.getLogger` sans passer par `get_logger`."""
    fautifs = []
    for chemin in SRC.rglob("*.py"):
        source = chemin.read_text(encoding="utf-8")
        if "from src.utils.logger import" in source or chemin.name in EXCEPTIONS:
            continue
        arbre = ast.parse(source, filename=str(chemin))
        for noeud in ast.walk(arbre):
            if (
                isinstance(noeud, ast.Call)
                and isinstance(noeud.func, ast.Attribute)
                and noeud.func.attr == "getLogger"
            ):
                fautifs.append(f"{chemin.relative_to(SRC).as_posix()}:{noeud.lineno}")
                break
    return fautifs


class TestToutModuleEcritQuelquePart:
    def test_aucun_logger_brut(self):
        """Un module qui journalise doit atteindre les fichiers, sinon son
        diagnostic n'existe pas — et son absence ne se signale pas d'elle-même."""
        fautifs = _modules_avec_logger_brut()
        assert fautifs == [], (
            "Ces modules journalisent sans atteindre les fichiers de log "
            f"(utiliser `get_logger`) : {fautifs}"
        )

    def test_le_crible_voit_bien_quelque_chose(self):
        """Un crible qui ne trouverait rien passerait au vert pour de mauvaises
        raisons — le travers du garde-fou muet."""
        assert (SRC / "utils" / "logger.py").exists()
        assert len(list(SRC.rglob("*.py"))) > 50

    def test_deezer_ne_configure_plus_la_RACINE(self):
        """`logging.basicConfig()` au chargement d'une brique de bibliothèque
        posait un handler sur la racine — d'où chaque ligne affichée EN DOUBLE
        avec celui de `coloredlogs` (défaut consigné dans CLAUDE.md).

        Cherché par AST et non par `in` : le mot figure dans le commentaire qui
        explique le retrait, et une recherche textuelle le trouverait là — un
        garde-fou qui s'accroche à sa propre explication ne garde rien.
        """
        arbre = ast.parse((SRC / "api" / "deezer_api.py").read_text(encoding="utf-8"))
        appels = [
            n.lineno
            for n in ast.walk(arbre)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "basicConfig"
        ]
        assert appels == [], f"basicConfig appelé ligne(s) {appels}"


class TestAucunHandlerFichierSousPytest:
    """La garde ②a. Elle se vérifie sur le logger lui-même : nous SOMMES sous
    pytest, donc aucun logger fraîchement créé ne doit porter de handler
    fichier."""

    def test_un_logger_neuf_n_a_pas_de_handler_fichier(self):
        from src.utils.logger import get_logger

        logger = get_logger("test_cablage_journal_temoin")
        fichiers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        assert fichiers == [], f"handler(s) fichier posé(s) sous pytest : {fichiers}"

    @pytest.mark.parametrize("nom", ["src.api.deezer_api", "src.utils.track_repository"])
    def test_les_loggers_deja_creees_non_plus(self, nom):
        """Les modules importés par la suite ont créé leurs loggers à l'import :
        eux aussi doivent être vierges de handler fichier."""
        fichiers = [
            h for h in logging.getLogger(nom).handlers if isinstance(h, logging.FileHandler)
        ]
        assert fichiers == []
