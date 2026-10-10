"""Les icônes de `data/icons` (Tabler, licence MIT — `data/icons/LICENSE`),
recolorées à la volée : une icône est un trait `currentColor`, la couleur vient
du thème. Les logos de marque sans `currentColor` reçoivent un `fill`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from src.config import DATA_DIR
from src.gui_qt.theme import TOKENS
from src.utils.logger import get_logger

logger = get_logger(__name__)

DOSSIER = Path(DATA_DIR) / "icons"


def svg_colore(nom: str, couleur: str) -> bytes | None:
    """Le SVG de l'icône avec sa couleur, ou None si l'icône n'existe pas."""
    chemin = DOSSIER / f"{nom}.svg"
    try:
        texte = chemin.read_text(encoding="utf-8")
    except OSError:
        logger.debug(f"Icône absente : {chemin}")
        return None
    if "currentColor" in texte:
        texte = texte.replace("currentColor", couleur)
    else:
        texte = texte.replace("<svg ", f'<svg fill="{couleur}" ', 1)
    return texte.encode("utf-8")


@lru_cache(maxsize=256)
def pixmap(nom: str, couleur: str | None = None, taille: int = 20) -> QPixmap:
    pm = QPixmap(QSize(taille, taille))
    pm.fill(Qt.GlobalColor.transparent)
    donnees = svg_colore(nom, couleur or TOKENS["texte_doux"])
    if donnees is None:
        return pm
    rendu = QSvgRenderer(QByteArray(donnees))
    peintre = QPainter(pm)
    rendu.render(peintre, QRectF(0, 0, taille, taille))
    peintre.end()
    return pm


def icone(nom: str, couleur: str | None = None, taille: int = 20) -> QIcon:
    """Une QIcon de l'icône, recolorée (couleur de texte doux par défaut)."""
    ic = QIcon()
    ic.addPixmap(pixmap(nom, couleur, taille))
    ic.addPixmap(pixmap(nom, TOKENS["desactive"], taille), QIcon.Mode.Disabled)
    return ic
