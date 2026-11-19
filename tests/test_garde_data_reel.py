"""La garde du `conftest` : aucun test n'ouvre le `data/` réel (2026-11-19).

Récidive du 2026-11-18 (`test_certifs_update`, tout le corpus de certifs) ;
posée, elle a montré une SAUVEGARDE de la vraie base faite par un test de la
discographie et les décisions Kworb réelles d'A2H lues par la fenêtre du panneau.
"""

import os
import sqlite3
from pathlib import Path

import pytest

from src import config
from tests.conftest import chemin_sous


def test_chemin_sous():
    dossier = os.path.normcase(os.path.abspath("/racine/data"))
    assert chemin_sous("/racine/data/x.db", dossier)
    assert chemin_sous(b"/racine/data", dossier)
    assert not chemin_sous("/racine/database/x.db", dossier)
    assert not chemin_sous(3, dossier)


def test_la_base_reelle_ne_s_ouvre_pas(_aucune_lecture_de_data_reel):
    fautes = _aucune_lecture_de_data_reel
    with pytest.raises(PermissionError):
        sqlite3.connect(os.path.join(config.DATA_DIR, "jamais_ouverte.db"))
    with pytest.raises(PermissionError):
        Path(config.DATA_DIR, "jamais_lu.json").read_text()
    # Notées, même si le code testé avalait l'erreur : le test échouerait.
    assert len(fautes) == 2
    fautes.clear()


def test_un_fichier_versionne_se_lit():
    chemin = os.path.join(config.DATA_DIR, "corrections", "fiches.json")
    if os.path.exists(chemin):
        with open(chemin, encoding="utf-8") as f:
            f.read(1)
