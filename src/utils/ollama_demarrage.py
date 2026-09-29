"""Ollama au démarrage : le lancer s'il ne tourne pas, le DIRE s'il reste absent.

Décision utilisateur 2026-09-29 : le run du matin avait tourné sans Ollama
(« Ollama/llama3.2 non disponible — le fallback BeautifulSoup sera utilisé »),
ce que seul le journal disait. Au lancement de l'app, on vérifie le serveur ;
s'il ne répond pas, on démarre `ollama serve` en arrière-plan (sans fenêtre),
on lui laisse jusqu'à 45 s, et l'utilisateur n'est prévenu qu'en cas d'échec.

Démarrer le serveur ne coûte presque rien : llama3.2 n'occupe la VRAM qu'au
premier appel, et Ollama l'en retire après quelques minutes d'inactivité. Le
serveur lancé ici survit à l'app, comme celui de l'icône Ollama.

Sans GUI : la fenêtre principale l'appelle dans un `start_worker`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

import httpx
import ollama

from src.utils.logger import get_logger

logger = get_logger(__name__)

MODELE = "llama3.2"
#: Mesuré le 2026-09-29 sur le poste (RTX 3050 Ti) : 13 à 27 s de démarrage à
#: froid. 10 s ne suffisaient pas ; l'attente est en tâche de fond, elle ne
#: bloque pas la fenêtre.
ATTENTE_S = 45.0
_PAS_S = 0.5
_SONDE_S = 2.0


@dataclass(frozen=True)
class EtatOllama:
    pret: bool
    #: Le serveur a été démarré par l'app (il ne tournait pas).
    lance: bool
    message: str


def executable_ollama() -> str | None:
    """`ollama` du PATH, sinon l'emplacement de l'installeur Windows."""
    trouve = shutil.which("ollama")
    if trouve:
        return trouve
    local = os.environ.get("LOCALAPPDATA")
    if local:
        chemin = os.path.join(local, "Programs", "Ollama", "ollama.exe")
        if os.path.isfile(chemin):
            return chemin
    return None


def serveur_repond() -> bool:
    # Délai COURT : sous Windows une connexion refusée sur localhost peut
    # bloquer plusieurs secondes, et la boucle d'attente dépassait ses 10 s
    # (mesuré : 29,7 s au premier essai réel).
    try:
        ollama.Client(timeout=_SONDE_S).list()
        return True
    except (ollama.RequestError, ollama.ResponseError, httpx.HTTPError, OSError):
        return False


def modele_present(modele: str = MODELE) -> bool:
    """Même règle que `LLMExtractor.is_available` : « llama3.2 » accepte
    « llama3.2:3b », « llama3.2:latest »…"""
    base = modele.split(":")[0]
    try:
        return any(m.model.startswith(base) for m in ollama.list().models)
    except (ollama.RequestError, ollama.ResponseError, httpx.HTTPError, OSError):
        return False


def _lancer_serveur(executable: str) -> None:
    drapeaux = 0
    if sys.platform == "win32":
        drapeaux = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    subprocess.Popen(  # exécutable local, sans shell
        [executable, "serve"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=drapeaux,
    )


def assurer_ollama(
    modele: str = MODELE,
    attente: float = ATTENTE_S,
    *,
    repond=serveur_repond,
    a_le_modele=modele_present,
    executable=executable_ollama,
    lancer=_lancer_serveur,
    horloge=time.monotonic,
    dormir=time.sleep,
) -> EtatOllama:
    """Vérifie Ollama, le démarre au besoin. Rend l'état et, s'il n'est pas
    prêt, le message à montrer. Les dépendances sont injectables (tests)."""
    lance = False
    if not repond():
        exe = executable()
        if exe is None:
            return EtatOllama(
                False,
                False,
                "Ollama n'est pas installé (ou introuvable) : les extractions par LLM "
                "sont désactivées, le repli BeautifulSoup sera utilisé.",
            )
        try:
            lancer(exe)
        except OSError as e:
            return EtatOllama(False, False, f"Impossible de lancer Ollama ({exe}) : {e}")
        lance = True
        logger.info(f"🦙 Ollama ne tournait pas : serveur lancé ({exe})")
        fin = horloge() + attente
        while not repond():
            if horloge() >= fin:
                return EtatOllama(
                    False,
                    True,
                    f"Ollama a été lancé mais ne répond pas après {attente:.0f} s : "
                    "les extractions par LLM utiliseront le repli BeautifulSoup.",
                )
            dormir(_PAS_S)
    if not a_le_modele(modele):
        return EtatOllama(
            False,
            lance,
            f"Ollama tourne mais le modèle « {modele} » est absent : "
            f"lancer « ollama pull {modele} ».",
        )
    logger.info(f"🦙 Ollama prêt ({modele}){' — lancé par l’app' if lance else ''}")
    return EtatOllama(True, lance, "")
