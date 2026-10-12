"""Lancement de la GUI PyQt6 : `python -m src.gui_qt`.

Mêmes dépendances que la CLI et la GUI CustomTkinter (`Runtime.build`), même
capteur d'usage des sources, même fermeture (`services.runtime.fermer`).
"""

from __future__ import annotations

import sys

from src.utils.logger import configurer_journaux, get_logger

logger = get_logger(__name__)


def creer_application(argv=None):
    from PyQt6.QtWidgets import QApplication

    from src.gui_qt.theme import feuille_de_style
    from src.gui_qt.widgets import delai_infobulles

    app = QApplication.instance() or QApplication(list(argv or sys.argv))
    app.setApplicationName("théRAPie")
    app.setOrganizationName("theRAPie")
    delai_infobulles(app)
    app.setStyleSheet(feuille_de_style())
    return app


def main(argv=None) -> int:
    configurer_journaux()
    from src.config import GENIUS_API_KEY

    if not GENIUS_API_KEY:
        print("GENIUS_API_KEY non configurée (variable d'environnement Windows ou fichier .env)")
        return 1
    app = creer_application(argv)

    from src.gui_qt.fenetre import FenetrePrincipale
    from src.observability import repository as usage_repository
    from src.services.runtime import Runtime, fermer

    runtime = Runtime.build()
    usage_repository.attach(runtime.data_manager.engine)
    fenetre = FenetrePrincipale(runtime)
    fenetre.show()
    logger.info("Interface PyQt6 lancée")
    try:
        return app.exec()
    finally:
        fermer(runtime)
        logger.info("Arrêt de l'interface PyQt6")
