"""Gestion d'un Chrome « debug » pour la route CDP (contournement Cloudflare).

Certains Cloudflare « managed » (ex: ultratop.be) bouclent tout navigateur LANCÉ
par de l'automation — même le vrai Chrome via `channel="chrome"`. La seule voie
fiable : démarrer un Chrome NORMAL avec le port de remote-debugging, puis laisser
patchright s'y ATTACHER via CDP (`GENIUS_CDP_URL`).

Ce module automatise ça côté programme (bouton GUI) : il vérifie si un Chrome
debug écoute déjà, sinon le lance (profil dédié, exclu du VPN côté utilisateur),
attend que le port réponde, et retourne l'URL CDP. Le profil persistant garde le
cookie `cf_clearance` → après la 1ʳᵉ résolution manuelle, c'est transparent.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_PORT = 9222
DEFAULT_PROFILE = str(Path.home() / "chrome-debug")


def _port_alive(port: int) -> bool:
    """Vrai si un endpoint CDP répond sur 127.0.0.1:port."""
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
            return r.status == 200
    except OSError:  # URLError ⊂ OSError, + timeout/connexion refusée
        return False


def find_chrome() -> str | None:
    """Localise l'exécutable Google Chrome (pas Chromium/Brave)."""
    env_path = os.getenv("CHROME_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    candidates = [
        Path(os.getenv("PROGRAMFILES", r"C:\Program Files"))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.getenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.getenv("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for c in candidates:
        try:
            if c and c.exists():
                return str(c)
        except OSError:
            continue
    return shutil.which("chrome")


def ensure_cdp_chrome(
    port: int = DEFAULT_PORT, profile: str | None = None, wait_seconds: float = 15.0
) -> str | None:
    """Garantit qu'un Chrome debug écoute sur `port` et retourne son URL CDP.

    - Si le port répond déjà → on réutilise (retourne l'URL).
    - Sinon → lance Chrome (profil dédié) avec --remote-debugging-port et attend.
    Retourne None si Chrome est introuvable ou que le port ne répond pas à temps.
    """
    if _port_alive(port):
        logger.info(f"CDP Chrome déjà en écoute sur {port}")
        return f"http://127.0.0.1:{port}"

    chrome = find_chrome()
    if not chrome:
        logger.error("Google Chrome introuvable (définir CHROME_PATH au besoin)")
        return None

    profile = profile or DEFAULT_PROFILE
    Path(profile).mkdir(parents=True, exist_ok=True)
    logger.info(f"Lancement de Chrome debug (port {port}, profil {profile})")
    try:
        subprocess.Popen(
            [chrome, f"--remote-debugging-port={port}", f"--user-data-dir={profile}"],
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as e:
        logger.error(f"Échec lancement Chrome debug : {e}")
        return None

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        time.sleep(0.5)
        if _port_alive(port):
            logger.info(f"CDP Chrome prêt sur {port}")
            return f"http://127.0.0.1:{port}"

    logger.error(f"Le port de debug {port} ne répond pas après {wait_seconds}s")
    return None
