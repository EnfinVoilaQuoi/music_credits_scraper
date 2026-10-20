"""La base en WAL (décision utilisateur 2026-10-20) : une lecture en cours ne
bloque plus une écriture. En mode `delete`, l'écrivain attendait puis échouait
(« database is locked ») — 56 compteurs Spotify de Travis Scott perdus pendant
une mesure, 8 durées de Kid Cudi pendant un audit."""

import sqlite3
import time
from contextlib import closing

from sqlalchemy import text

from src.models import Artist


def _chemin(data_manager):
    return data_manager.engine.url.database


def test_la_base_est_en_wal(data_manager):
    with data_manager.engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"


def test_une_lecture_en_cours_ne_bloque_pas_une_ecriture(data_manager):
    with closing(sqlite3.connect(_chemin(data_manager))) as lecteur:
        curseur = lecteur.execute("SELECT name FROM sqlite_master")
        curseur.fetchone()  # transaction de lecture OUVERTE, curseur non épuisé
        debut = time.monotonic()
        artiste = Artist(name="Travis Scott")
        artiste.id = data_manager.save_artist(artiste)
        assert artiste.id
        assert time.monotonic() - debut < 3
