"""Widgets de la fenêtre principale : cartouche, pied de table, bouton-jauge.

Ils AFFICHENT ce que `src/presentation` calcule ; aucun ne juge un morceau.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.config import IMAGES_DIR
from src.gui_qt import icones
from src.gui_qt.theme import POLICE_LEGERE, TOKENS
from src.presentation.cartouche import Cartouche
from src.presentation.filtres import ChampPied
from src.presentation.pied import Pied, Portee

_DELAI_INFOBULLE_MS = 600


# ── Cartouche ────────────────────────────────────────────────────────────────


def photo_arrondie(chemin: Path | None, taille: int = 120) -> QPixmap:
    pm = QPixmap(taille, taille)
    pm.fill(Qt.GlobalColor.transparent)
    source = QPixmap(str(chemin)) if chemin and Path(chemin).exists() else QPixmap()
    peintre = QPainter(pm)
    peintre.setRenderHint(QPainter.RenderHint.Antialiasing)
    trace = QPainterPath()
    trace.addRoundedRect(QRectF(0, 0, taille, taille), 10, 10)
    peintre.setClipPath(trace)
    if source.isNull():
        peintre.fillRect(0, 0, taille, taille, QColor(TOKENS["accent"]))
    else:
        mise = source.scaled(
            taille,
            taille,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        peintre.drawPixmap((taille - mise.width()) // 2, (taille - mise.height()) // 2, mise)
    peintre.end()
    return pm


class CartoucheArtiste(QWidget):
    """Photo · nom • alias | groupes | collectifs · décompte · audience."""

    formations_cliquees = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.photo = QLabel()
        self.photo.setFixedSize(120, 120)
        self.nom = QLabel()
        self.nom.setObjectName("nomArtiste")
        self.nom.setTextFormat(Qt.TextFormat.RichText)
        self.nom.setCursor(Qt.CursorShape.PointingHandCursor)
        self.nom.setToolTip("Groupes et formations")
        self.titres = QLabel()
        self.titres.setObjectName("recap")
        self.audience = QLabel()
        self.audience.setObjectName("recap")
        texte = QVBoxLayout()
        texte.setSpacing(2)
        texte.addStretch()
        for w in (self.nom, self.titres, self.audience):
            texte.addWidget(w)
        texte.addStretch()
        couche = QHBoxLayout(self)
        couche.setContentsMargins(24, 12, 12, 12)
        couche.setSpacing(24)
        couche.addWidget(self.photo)
        couche.addLayout(texte, 1)
        self.afficher(None)

    def mousePressEvent(self, event) -> None:
        if self.nom.geometry().contains(event.position().toPoint()):
            self.formations_cliquees.emit()
        super().mousePressEvent(event)

    def afficher(self, c: Cartouche | None, image: str | None = None) -> None:
        if c is None:
            self.nom.setText("Aucun artiste chargé")
            self.titres.setText("Choisissez un artiste dans la barre de recherche.")
            self.audience.setText("")
            self.photo.setPixmap(photo_arrondie(None))
            return
        accent, leger = TOKENS["accent"], POLICE_LEGERE
        morceaux = [f"<b>{c.nom}</b>"]
        if c.alias:
            morceaux[0] += " • " + " • ".join(a.texte for a in c.alias)
        html = morceaux[0]
        if c.groupes:
            html += " | " + " • ".join(g.texte for g in c.groupes)
        if c.collectifs:
            noms = " • ".join(x.texte for x in c.collectifs)
            html += f" | <span style=\"font-family:'{leger}'\">{noms}</span>"
        self.nom.setText(f'<span style="color:{accent}">{html}</span>')
        self.titres.setText(c.ligne_titres)
        self.audience.setText(c.ligne_audience)
        self.audience.setToolTip(c.infobulle_audience)
        chemin = Path(IMAGES_DIR) / image if image else None
        self.photo.setPixmap(photo_arrondie(chemin))


# ── Bouton de flux / jauge ───────────────────────────────────────────────────


class BoutonFlux(QPushButton):
    """Un bouton de flux qui devient une JAUGE pendant son run : fond accent,
    progression foncée, pourcentage et étape (ébauche)."""

    def __init__(self, texte: str, icone: str, parent=None) -> None:
        super().__init__(
            icones.icone(icone, TOKENS["accent"], 22), "  " + texte.replace("&", "&&"), parent
        )
        self._texte = texte
        self._fraction: float | None = None
        self._etiquette = ""
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def progression(self, fraction: float | None, etiquette: str) -> None:
        self._fraction = fraction
        self._etiquette = etiquette
        self.update()

    def au_repos(self) -> None:
        self._fraction = None
        self._etiquette = ""
        self.update()

    @property
    def en_cours(self) -> bool:
        return self._fraction is not None

    def paintEvent(self, event) -> None:
        if self._fraction is None:
            super().paintEvent(event)
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        trace = QPainterPath()
        trace.addRoundedRect(r, 6, 6)
        p.fillPath(trace, QColor(TOKENS["jauge_fond"]))
        p.save()
        p.setClipPath(trace)
        p.fillRect(
            QRectF(0, 0, r.width() * max(0.0, min(1.0, self._fraction)), r.height()),
            QColor(TOKENS["jauge_progression"]),
        )
        p.restore()
        p.setPen(QColor(TOKENS["texte_sur_accent"]))
        p.drawText(
            r.adjusted(8, 0, -8, 0),
            Qt.AlignmentFlag.AlignCenter,
            self.fontMetrics().elidedText(
                self._etiquette, Qt.TextElideMode.ElideRight, int(r.width()) - 16
            ),
        )
        p.end()


# ── Pied de table ────────────────────────────────────────────────────────────


class PiedTable(QWidget):
    """Sélection (à gauche) · portée GLOBAL/FILTRÉ · compteurs cliquables · %."""

    portee_changee = pyqtSignal(object)  # Portee
    manque_demande = pyqtSignal(object)  # ChampPied | None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("pied")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.portee = Portee.GLOBAL
        self.selection = QLabel()
        self.bouton_portee = QToolButton()
        self.bouton_portee.setToolTip(
            "Portée des compteurs — GLOBAL : toute la discographie chargée ; "
            "FILTRÉ : les seules lignes affichées. Clic pour basculer."
        )
        self.bouton_portee.clicked.connect(self._basculer)
        self.compteurs: dict[ChampPied, QToolButton] = {}
        couche = QHBoxLayout(self)
        couche.setContentsMargins(12, 4, 12, 4)
        couche.addWidget(self.selection)
        couche.addStretch()
        couche.addWidget(self.bouton_portee)
        couche.addSpacing(12)
        for champ in ChampPied:
            b = QToolButton()
            b.setCheckable(True)
            b.clicked.connect(lambda oui, c=champ: self._demander(c, oui))
            self.compteurs[champ] = b
            couche.addWidget(b)
        self.certifs = QLabel()
        self.certifs.setToolTip(
            "Certifications reliées aux lignes de la portée / à toute la discographie "
            "chargée (les échos d'une version ne comptent pas). Hors pourcentage."
        )
        self.pourcentage = QLabel()
        self.pourcentage.setToolTip(
            "Morceaux validés sur tous les champs / morceaux à valider "
            "(désactivés, inédits, à venir et pages sans info exclus)."
        )
        couche.addWidget(self.certifs)
        couche.addSpacing(8)
        couche.addWidget(self.pourcentage)
        self._afficher_portee()

    def _afficher_portee(self) -> None:
        """L'état reste TOUJOURS affiché : le pourcentage n'est jamais ambigu."""
        if self.portee is Portee.GLOBAL:
            self.bouton_portee.setText("GLOBAL ●○ FILTRÉ")
        else:
            self.bouton_portee.setText("GLOBAL ○● FILTRÉ")
        self.bouton_portee.setAccessibleName(f"Portée {self.portee.value}")

    def _basculer(self) -> None:
        self.portee = self.portee.basculee()
        self._afficher_portee()
        self.portee_changee.emit(self.portee)

    def _demander(self, champ: ChampPied, oui: bool) -> None:
        for c, b in self.compteurs.items():
            if c is not champ:
                b.setChecked(False)
        self.manque_demande.emit(champ if oui else None)

    def relacher_filtre(self) -> None:
        for b in self.compteurs.values():
            b.setChecked(False)

    def afficher(self, p: Pied | None, texte_selection: str) -> None:
        self.selection.setText(texte_selection)
        if p is None:
            for b in self.compteurs.values():
                b.setText("")
            self.certifs.setText("")
            self.pourcentage.setText("")
            return
        for champ, b in self.compteurs.items():
            c = p.compteur(champ)
            b.setText(f"{champ.value} {c.texte}")
            manque = f"{c.manquants} morceau{'x' if c.manquants > 1 else ''} sans {champ.value}"
            b.setToolTip(
                f"{champ.value} : {c.valides} validés sur {c.eligibles}.\n"
                f"Clic : afficher les {manque}."
            )
        self.certifs.setText(f"Certifs ({p.certifs_portee}/{p.certifs_total})")
        self.pourcentage.setText(f"<b>{p.texte_pourcentage}</b>")


def delai_infobulles(app) -> None:
    """Infobulles à ~600 ms (2 s paraît cassé)."""
    from PyQt6.QtWidgets import QProxyStyle, QStyle

    class _Style(QProxyStyle):
        def styleHint(self, hint, option=None, widget=None, retour=None):
            if hint == QStyle.StyleHint.SH_ToolTip_WakeUpDelay:
                return _DELAI_INFOBULLE_MS
            return super().styleHint(hint, option, widget, retour)

    app.setStyle(_Style(app.style()))
