"""Dialogues « Ajout d'artiste » et « Base de données · Artistes ».

Ajout (comportement décidé par l'utilisateur le 2026-10-11) :
  1. pendant la frappe, les artistes DÉJÀ EN BASE qui correspondent — en
     choisir un l'ouvre, au lieu d'en ajouter un doublon ;
  2. sans correspondance, la recherche Genius part (`services.artiste.resoudre`) ;
  3. plusieurs candidats ⇒ « Résultats proposés » ; on ne choisit JAMAIS un
     homonyme à la place de l'utilisateur, « Ajouter » reste grisé sans choix.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from src.gui_qt import icones
from src.gui_qt.pont import en_fond
from src.gui_qt.theme import TOKENS
from src.presentation.artistes import ResumeArtiste, correspondance_exacte, suggestions
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DialogueAjout(QDialog):
    """Émet `ouvrir(nom)` pour un artiste en base, `ajoute(nom)` après un ajout."""

    ouvrir = pyqtSignal(str)
    ajoute = pyqtSignal(str)

    def __init__(self, runtime, artistes: list[ResumeArtiste], parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.artistes = artistes
        self.setWindowTitle("Ajout d'artiste")
        self.resize(560, 560)
        self.saisie = QLineEdit()
        self.saisie.setPlaceholderText("Nom de l'artiste")
        self.saisie.textEdited.connect(self._suggerer)
        self.saisie.returnPressed.connect(self._chercher)
        self.en_base = QListWidget()
        self.en_base.setToolTip("Déjà en base — double-clic pour l'ouvrir")
        self.en_base.itemActivated.connect(
            lambda item: self._ouvrir(item.data(Qt.ItemDataRole.UserRole))
        )
        self.etat = QLabel()
        self.etat.setObjectName("discret")
        self.etat.setWordWrap(True)
        self.proposes = QListWidget()
        self.proposes.currentItemChanged.connect(
            lambda *_: self.bouton_ajouter.setEnabled(self.proposes.currentItem() is not None)
        )
        self.bouton_chercher = QPushButton(icones.icone("search"), "  Chercher sur Genius")
        self.bouton_chercher.clicked.connect(self._chercher)
        self.bouton_ajouter = QPushButton(
            icones.icone("playlist-add", TOKENS["texte_sur_accent"]), "  Ajouter"
        )
        self.bouton_ajouter.setObjectName("accent")
        self.bouton_ajouter.setEnabled(False)
        self.bouton_ajouter.clicked.connect(self._ajouter)
        haut = QHBoxLayout()
        haut.addWidget(self.saisie, 1)
        haut.addWidget(self.bouton_chercher)
        couche = QVBoxLayout(self)
        couche.addLayout(haut)
        couche.addWidget(QLabel("Déjà en base :"))
        couche.addWidget(self.en_base, 1)
        couche.addWidget(QLabel("Résultats proposés :"))
        couche.addWidget(self.proposes, 1)
        couche.addWidget(self.etat)
        couche.addWidget(self.bouton_ajouter, alignment=Qt.AlignmentFlag.AlignCenter)
        self._suggerer("")

    def _suggerer(self, texte: str) -> None:
        self.en_base.clear()
        if not texte.strip():
            return
        for a in suggestions(texte, self.artistes):
            item = QListWidgetItem(f"{a.nom}    —  {a.detail}")
            item.setData(Qt.ItemDataRole.UserRole, a.nom)
            self.en_base.addItem(item)

    def _ouvrir(self, nom: str) -> None:
        self.ouvrir.emit(nom)
        self.accept()

    def _chercher(self) -> None:
        nom = self.saisie.text().strip()
        if not nom:
            return
        deja = correspondance_exacte(nom, self.artistes)
        if deja is not None:
            if (
                QMessageBox.question(
                    self, "Déjà en base", f"« {deja.nom} » est déjà en base. L'ouvrir ?"
                )
                == QMessageBox.StandardButton.Yes
            ):
                self._ouvrir(deja.nom)
            return
        self.proposes.clear()
        self.bouton_chercher.setEnabled(False)
        self.etat.setText(f"Recherche de « {nom} » sur Genius…")
        en_fond(lambda: self._resoudre(nom), self._resolu, self._echec, nom="qt:resolution-genius")

    def _resoudre(self, nom: str):
        from src.services import artiste as artiste_service

        try:
            return [artiste_service.resoudre(self.runtime, nom)], True
        except artiste_service.ArtisteAmbigu as ambigu:
            return list(ambigu.candidats), False

    def _resolu(self, resultat) -> None:
        candidats, sur = resultat
        self.bouton_chercher.setEnabled(True)
        for a in candidats:
            item = QListWidgetItem(f"{a.name}    —  ID Genius {a.genius_id}")
            item.setData(Qt.ItemDataRole.UserRole, a)
            self.proposes.addItem(item)
        if not candidats:
            self.etat.setText("Aucun artiste trouvé sur Genius. Vérifiez l'orthographe.")
        elif sur:
            self.etat.setText("Page Genius trouvée. Vérifiez puis « Ajouter ».")
        else:
            self.etat.setText(
                "Plusieurs candidats : choisissez le bon (on ne choisit jamais un homonyme "
                "à votre place)."
            )

    def _echec(self, message: str) -> None:
        self.bouton_chercher.setEnabled(True)
        self.etat.setText(f"Recherche impossible : {message}")

    def _ajouter(self) -> None:
        item = self.proposes.currentItem()
        if item is None:
            return
        artiste = item.data(Qt.ItemDataRole.UserRole)
        self.runtime.data_manager.save_artist(artiste)
        logger.info(f"✅ Artiste ajouté : {artiste.name} (ID Genius {artiste.genius_id})")
        self.ajoute.emit(artiste.name)
        self.accept()


class DialogueArtistes(QDialog):
    """La base, artiste par artiste (titres, dernier run). Double-clic = ouvrir."""

    ouvrir = pyqtSignal(str)

    def __init__(self, artistes: list[ResumeArtiste], parent=None) -> None:
        super().__init__(parent)
        self.artistes = artistes
        self.setWindowTitle("Base de données • Artistes")
        self.resize(520, 640)
        self.filtre = QLineEdit()
        self.filtre.setPlaceholderText("Filtrer…")
        self.filtre.textEdited.connect(self._remplir)
        self.liste = QListWidget()
        self.liste.itemActivated.connect(self._ouvrir)
        ouvrir = QPushButton(icones.icone("login-2", TOKENS["texte_sur_accent"]), "  Ouvrir")
        ouvrir.setObjectName("accent")
        ouvrir.clicked.connect(lambda: self._ouvrir(self.liste.currentItem()))
        couche = QVBoxLayout(self)
        couche.addWidget(self.filtre)
        couche.addWidget(self.liste, 1)
        couche.addWidget(ouvrir, alignment=Qt.AlignmentFlag.AlignCenter)
        self._remplir("")

    def _remplir(self, texte: str) -> None:
        self.liste.clear()
        for a in suggestions(texte, self.artistes, limite=10_000):
            item = QListWidgetItem(f"{a.nom}\n{a.detail}")
            item.setData(Qt.ItemDataRole.UserRole, a.nom)
            self.liste.addItem(item)

    def _ouvrir(self, item) -> None:
        if item is not None:
            self.ouvrir.emit(item.data(Qt.ItemDataRole.UserRole))
            self.accept()
