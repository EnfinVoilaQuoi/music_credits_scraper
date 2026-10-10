"""Barre de titre NATIVE colorée (Windows 11) — décision de l'ébauche : garder les
fenêtres Windows (Snap Layouts, redimensionnement, accessibilité) plutôt qu'une
barre faite main, et leur donner la couleur du thème par le gestionnaire de
fenêtres (`DwmSetWindowAttribute`).

Sans effet hors Windows et sur Windows 10 (l'attribut de couleur n'y existe
pas : l'appel échoue, on le note et on garde la barre système).
"""

from __future__ import annotations

import sys

from src.utils.logger import get_logger

logger = get_logger(__name__)

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36


def colorref(couleur: str) -> int:
    """« #7450a9 » → COLORREF Windows (0x00BBGGRR)."""
    c = couleur.lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return (b << 16) | (g << 8) | r


def colorer_barre_titre(widget, fond: str, texte: str) -> bool:
    """Colore la barre de titre de la fenêtre de `widget`. Rend True si Windows
    l'a acceptée."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    try:
        hwnd = wintypes.HWND(int(widget.winId()))
        dwm = ctypes.windll.dwmapi
        ok = True
        for attribut, valeur in (
            (_DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
            (_DWMWA_CAPTION_COLOR, colorref(fond)),
            (_DWMWA_TEXT_COLOR, colorref(texte)),
        ):
            v = ctypes.c_int(valeur)
            res = dwm.DwmSetWindowAttribute(hwnd, attribut, ctypes.byref(v), ctypes.sizeof(v))
            ok = ok and res == 0
        return ok
    except (OSError, AttributeError, ValueError) as e:
        logger.debug(f"Barre de titre non colorée : {e}")
        return False
