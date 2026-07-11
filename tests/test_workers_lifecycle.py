"""Tests du cycle de vie des workers GUI (arrêt propre à la fermeture).

AUDIT §4 « threads démons sans arrêt propre » : les workers restent daemon,
mais sont enregistrés, testent un drapeau entre deux unités de travail, et
sont joints avec un budget de temps à la fermeture — un save en cours se
termine au lieu d'être tué net.
"""

import threading
import time

import pytest

from src.gui.workers import lifecycle


@pytest.fixture(autouse=True)
def _reset_lifecycle():
    lifecycle.reset()
    yield
    lifecycle.reset()


class TestStartWorker:
    def test_lance_et_enregistre_un_thread_daemon(self):
        done = threading.Event()
        thread = lifecycle.start_worker(done.set, name="w-test")
        assert done.wait(timeout=2)
        assert thread.daemon is True  # un scraper bloqué ne bloque jamais l'exit
        assert thread.name == "w-test"

    def test_nom_par_defaut_derive_de_la_cible(self):
        def ma_tache():
            pass

        thread = lifecycle.start_worker(ma_tache)
        thread.join(timeout=2)
        assert "ma_tache" in thread.name


class TestShutdownWorkers:
    def test_le_drapeau_arrete_une_boucle_cooperative(self):
        compteur = {"n": 0}

        def boucle():
            while not lifecycle.stop_requested():
                compteur["n"] += 1
                time.sleep(0.01)

        lifecycle.start_worker(boucle, name="w-boucle")
        time.sleep(0.05)
        survivants = lifecycle.shutdown_workers(total_timeout=2.0)
        assert survivants == []
        assert compteur["n"] > 0

    def test_le_travail_en_cours_se_termine_avant_le_join(self):
        """Le scénario qui motive tout le module : un « save » démarré doit
        se TERMINER pendant le join, pas être tué au milieu."""
        fini = {"save": False}

        def worker():
            time.sleep(0.2)  # unité de travail en cours (save_track…)
            fini["save"] = True

        lifecycle.start_worker(worker, name="w-save")
        survivants = lifecycle.shutdown_workers(total_timeout=2.0)
        assert survivants == []
        assert fini["save"] is True

    def test_un_thread_bloque_est_abandonne_apres_le_budget(self):
        blocage = threading.Event()  # jamais levé pendant le join

        def bloque():
            blocage.wait(timeout=5)

        lifecycle.start_worker(bloque, name="w-bloque")
        survivants = lifecycle.shutdown_workers(total_timeout=0.2)
        assert survivants == ["w-bloque"]
        blocage.set()  # libérer le thread pour ne pas polluer les tests suivants

    def test_sans_worker_actif_retourne_vide(self):
        assert lifecycle.shutdown_workers(total_timeout=0.1) == []

    def test_stop_requested_reflete_le_drapeau(self):
        assert lifecycle.stop_requested() is False
        lifecycle.shutdown_workers(total_timeout=0.1)
        assert lifecycle.stop_requested() is True
