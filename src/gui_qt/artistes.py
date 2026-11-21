"""La fenêtre Base de données • Artistes (maquette du 2026-11-21).

Une carte par artiste : photo, nom, « N Titres • % • date du dernier run », et
trois gestes — ↻ recharger, ⓘ détail, 🗑 supprimer (confirmation, puis
sauvegarde de la base AVANT d'écrire). Un index alphabétique à droite ; en bas,
Ouvrir et Ajouter.

Le pourcentage n'est pas recalculé ici (il faudrait charger chaque artiste) :
c'est le dernier connu, noté par la fenêtre principale à chaque chargement.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.config import IMAGES_DIR
from src.gui_qt import icones
from src.gui_qt.theme import TOKENS
from src.gui_qt.widgets import photo_arrondie
from src.presentation import format as fmt
from src.presentation.artistes import ResumeArtiste, lettre_d_index, suggestions

LETTRES = [*"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "#"]


def _icone_geste(nom: str, couleur: str, infobulle: str) -> QToolButton:
    b = QToolButton()
    b.setIcon(icones.icone(nom, couleur, 26))
    b.setIconSize(QSize(26, 26))
    b.setObjectName("discret")
    b.setToolTip(infobulle)
    b.setAccessibleName(infobulle)
    b.setMinimumSize(40, 40)
    return b


class CarteArtiste(QFrame):
    ouvrir = pyqtSignal(str)
    clic = pyqtSignal(str)
    recharger = pyqtSignal(str)
    detail = pyqtSignal(str)
    supprimer = pyqtSignal(str)

    def __init__(self, a: ResumeArtiste, pourcentage: str | None, parent=None) -> None:
        super().__init__(parent)
        self.nom = a.nom
        self.setObjectName("carte")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 10, 16, 10)
        h.setSpacing(16)
        photo = QLabel()
        chemin = Path(IMAGES_DIR) / a.image if a.image else None
        photo.setPixmap(photo_arrondie(chemin, 56))
        h.addWidget(photo)
        texte = QVBoxLayout()
        texte.setSpacing(0)
        nom = QLabel(a.nom)
        nom.setObjectName("titreCarte")
        morceaux = [f"{fmt.nombre(a.titres)} Titre{'s' if a.titres > 1 else ''}"]
        if pourcentage:
            morceaux.append(pourcentage)
        if a.derniere_maj:
            morceaux.append(fmt.format_date(a.derniere_maj, vide=""))
        detail = QLabel(" • ".join(morceaux))
        detail.setObjectName("recap")
        texte.addWidget(nom)
        texte.addWidget(detail)
        h.addLayout(texte, 1)
        for signal, icone, couleur, infobulle in (
            (self.recharger, "reload", TOKENS["accent"], "Recharger l'artiste"),
            (self.detail, "info-circle", TOKENS["accent"], "Détail de l'artiste"),
            (self.supprimer, "trash", TOKENS["erreur"], "Supprimer l'artiste et ses données"),
        ):
            b = _icone_geste(icone, couleur, infobulle)
            b.clicked.connect(lambda _=False, s=signal: s.emit(self.nom))
            h.addWidget(b)

    def mouseDoubleClickEvent(self, event) -> None:
        self.ouvrir.emit(self.nom)
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        self.clic.emit(self.nom)
        super().mousePressEvent(event)


class FenetreArtistes(QDialog):
    """`ouvrir(nom)` charge l'artiste ; `ajouter()` ouvre l'ajout ;
    `supprime(nom)` dit que la base a changé."""

    ouvrir = pyqtSignal(str)
    ajouter = pyqtSignal()
    supprime = pyqtSignal(str)

    def __init__(
        self,
        runtime,
        artistes: list[ResumeArtiste],
        pourcentages: dict[str, str] | None = None,
        sauvegarder: Callable[[str], object] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.artistes = list(artistes)
        self.pourcentages = pourcentages or {}
        #: Sauvegarde de la base AVANT une suppression (injectée : un test ne
        #: doit jamais copier la vraie base). Rend un chemin, ou None = échec.
        self.sauvegarder = sauvegarder
        self.choisi: str | None = None
        self.cartes: dict[str, CarteArtiste] = {}
        self.setWindowTitle("Base de Données • Artistes")
        self.resize(640, 820)
        couche = QVBoxLayout(self)
        couche.setContentsMargins(12, 12, 12, 12)
        self.filtre = QLineEdit()
        self.filtre.setPlaceholderText("Filtrer…")
        self.filtre.setClearButtonEnabled(True)
        self.filtre.textEdited.connect(self._remplir)
        couche.addWidget(self.filtre)
        corps = QHBoxLayout()
        self.zone = QScrollArea()
        self.zone.setWidgetResizable(True)
        self.contenu = QWidget()
        self.liste = QVBoxLayout(self.contenu)
        self.liste.setSpacing(10)
        self.zone.setWidget(self.contenu)
        corps.addWidget(self.zone, 1)
        index = QVBoxLayout()
        index.setSpacing(0)
        self.lettres: dict[str, QToolButton] = {}
        for lettre in LETTRES:
            b = QToolButton()
            b.setText(lettre)
            b.setObjectName("discret")
            b.clicked.connect(lambda _=False, x=lettre: self.aller_a(x))
            self.lettres[lettre] = b
            index.addWidget(b)
        index.addStretch()
        corps.addLayout(index)
        couche.addLayout(corps, 1)
        pied = QHBoxLayout()
        pied.addStretch()
        self.b_ouvrir = QPushButton(
            icones.icone("login-2", TOKENS["texte_sur_accent"], 22), "  Ouvrir"
        )
        self.b_ajouter = QPushButton(
            icones.icone("playlist-add", TOKENS["texte_sur_accent"], 22), "  Ajouter"
        )
        for b in (self.b_ouvrir, self.b_ajouter):
            b.setObjectName("accent")
            b.setMinimumSize(200, 44)
            pied.addWidget(b)
        pied.addStretch()
        couche.addLayout(pied)
        self.b_ouvrir.clicked.connect(lambda: self._ouvrir(self.choisi))
        self.b_ajouter.clicked.connect(self._ajouter)
        self._remplir("")

    # ── contenu ──
    def _remplir(self, texte: str = "") -> None:
        while self.liste.count():
            item = self.liste.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.cartes = {}
        visibles = (
            suggestions(texte, self.artistes, limite=10_000)
            if texte.strip()
            else sorted(self.artistes, key=lambda a: fmt.normalize_text(a.nom))
        )
        for a in visibles:
            carte = CarteArtiste(a, self.pourcentages.get(a.nom))
            carte.ouvrir.connect(self._ouvrir)
            carte.clic.connect(self.choisir)
            carte.recharger.connect(self._ouvrir)
            carte.detail.connect(self._detail)
            carte.supprimer.connect(self._supprimer)
            self.cartes[a.nom] = carte
            self.liste.addWidget(carte)
        self.liste.addStretch()
        presentes = {lettre_d_index(a.nom) for a in visibles}
        for lettre, b in self.lettres.items():
            b.setEnabled(lettre in presentes)
        self.b_ouvrir.setEnabled(self.choisi in self.cartes)

    def choisir(self, nom: str) -> None:
        self.choisi = nom
        for n, carte in self.cartes.items():
            carte.setStyleSheet(
                f"QFrame#carte {{ border: 2px solid {TOKENS['accent']}; }}" if n == nom else ""
            )
        self.b_ouvrir.setEnabled(True)

    def aller_a(self, lettre: str) -> None:
        for nom, carte in self.cartes.items():
            if lettre_d_index(nom) == lettre:
                self.zone.ensureWidgetVisible(carte, 0, 0)
                self.zone.verticalScrollBar().setValue(carte.y())
                return

    # ── gestes ──
    def _ouvrir(self, nom: str | None) -> None:
        if nom:
            self.ouvrir.emit(nom)
            self.accept()

    def _ajouter(self) -> None:
        self.ajouter.emit()
        self.accept()

    def _detail(self, nom: str) -> None:
        a = next((x for x in self.artistes if x.nom == nom), None)
        if a is None:
            return
        lignes = [f"{fmt.nombre(a.titres)} titre(s)", f"{fmt.nombre(a.credits)} crédit(s)"]
        if a.ajoute_le:
            lignes.append(f"Ajouté le {fmt.format_date(a.ajoute_le, vide='')}")
        if a.derniere_maj:
            lignes.append(f"Dernier run le {fmt.format_date(a.derniere_maj, vide='')}")
        if self.pourcentages.get(nom):
            lignes.append(f"Validé à {self.pourcentages[nom]} (dernier chargement)")
        QMessageBox.information(self, nom, "\n".join(lignes))

    def _supprimer(self, nom: str) -> None:
        r = QMessageBox.warning(
            self,
            "Supprimer l'artiste",
            f"Supprimer « {nom} » ?\n\nL'artiste, tous ses morceaux, leurs crédits et leurs "
            "données partent de la base. Une sauvegarde de la base est faite avant.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        if self.sauvegarder is None or not self.sauvegarder(nom):
            QMessageBox.critical(
                self, "Suppression annulée", "La sauvegarde de la base a échoué : rien n'est fait."
            )
            return
        if not self.runtime.data_manager.delete_artist(nom):
            QMessageBox.critical(self, "Suppression", f"Impossible de supprimer « {nom} ».")
            return
        self.artistes = [a for a in self.artistes if a.nom != nom]
        if self.choisi == nom:
            self.choisi = None
        self._remplir(self.filtre.text())
        self.supprime.emit(nom)
