"""Ollama au démarrage (2026-09-29) : lancé s'il ne tourne pas, signalé s'il
reste absent. Aucun test ne parle à Ollama ni ne lance de processus."""

import subprocess
import sys

import pytest

from src.utils.ollama_demarrage import assurer_ollama, drapeaux_de_lancement


class _Horloge:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def dormir(self, s):
        self.t += s


def _assurer(reponses, *, modele=True, exe="C:/ollama.exe", lancer=None):
    """`reponses` : ce que rend chaque sonde du serveur, dans l'ordre (la
    dernière valeur se répète)."""
    file = list(reponses)
    lances = []
    h = _Horloge()

    def repond():
        return file.pop(0) if len(file) > 1 else file[0]

    etat = assurer_ollama(
        repond=repond,
        a_le_modele=lambda m: modele,
        executable=lambda: exe,
        lancer=lancer or lances.append,
        horloge=h,
        dormir=h.dormir,
    )
    return etat, lances


def test_deja_lance_rien_a_faire():
    etat, lances = _assurer([True])
    assert etat.pret and not etat.lance and lances == []


def test_eteint_puis_demarre():
    etat, lances = _assurer([False, False, True])
    assert etat.pret and etat.lance
    assert lances == ["C:/ollama.exe"]


def test_ne_repond_pas_apres_l_attente():
    etat, lances = _assurer([False])
    assert not etat.pret and etat.lance
    assert "ne répond pas après 45 s" in etat.message
    assert len(lances) == 1  # une seule tentative de lancement


def test_introuvable():
    etat, lances = _assurer([False], exe=None)
    assert not etat.pret and not etat.lance and lances == []
    assert "pas installé" in etat.message


def test_lancement_impossible():
    def refuse(exe):
        raise OSError("accès refusé")

    etat, _ = _assurer([False], lancer=refuse)
    assert not etat.pret and "accès refusé" in etat.message


def test_modele_absent():
    etat, _ = _assurer([True], modele=False)
    assert not etat.pret and "ollama pull llama3.2" in etat.message


@pytest.mark.skipif(sys.platform != "win32", reason="drapeaux de création Windows")
def test_le_serveur_a_une_console_cachee_pas_aucune_console():
    """Sans console (DETACHED_PROCESS), chaque processus qu'Ollama lance ouvre
    sa propre fenêtre visible : la boucle de fenêtres du 2026-10-12."""
    drapeaux = drapeaux_de_lancement()
    assert drapeaux & subprocess.CREATE_NO_WINDOW
    assert not drapeaux & subprocess.DETACHED_PROCESS
