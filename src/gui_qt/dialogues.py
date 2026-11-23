"""Dialogue « Ajout d'artiste » (maquette du 2026-11-21 ; la fenêtre Artistes est
dans `artistes.py`).

Comportement décidé par l'utilisateur le 2026-10-11 :
  1. pendant la frappe, les artistes DÉJÀ EN BASE qui correspondent — en
     choisir un l'ouvre, au lieu d'en ajouter un doublon ;
  2. sans correspondance, la recherche Genius part (`services.artiste.resoudre`) ;
  3. plusieurs candidats ⇒ « Résultats proposés » ; on ne choisit JAMAIS un
     homonyme à la place de l'utilisateur, « Ajouter » reste grisé sans choix
     (c'est ici que se fait le choix entre homonymes, `artist_selection` en CTk).

La loupe du champ (ou Entrée) lance la recherche Genius.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
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
        self.setWindowTitle("Ajout d'Artiste")
        self.resize(640, 660)
        self.saisie = QLineEdit()
        self.saisie.setPlaceholderText("Nom de l'artiste")
        self.saisie.setObjectName("grand")
        self.saisie.textEdited.connect(self._suggerer)
        self.saisie.returnPressed.connect(self._chercher)
        self.loupe = self.saisie.addAction(
            icones.icone("search", TOKENS["texte_discret"], 24),
            QLineEdit.ActionPosition.TrailingPosition,
        )
        self.loupe.setToolTip("Chercher sur Genius (Entrée)")
        self.loupe.triggered.connect(self._chercher)
        self.en_base = QListWidget()
        self.en_base.setObjectName("liste")
        self.en_base.setToolTip("Déjà en base — double-clic pour l'ouvrir")
        self.en_base.itemActivated.connect(
            lambda item: self._ouvrir(item.data(Qt.ItemDataRole.UserRole))
        )
        self.etat = QLabel()
        self.etat.setObjectName("discret")
        self.etat.setWordWrap(True)
        self.proposes = QListWidget()
        self.proposes.setObjectName("liste")
        self.proposes.currentItemChanged.connect(
            lambda *_: self.bouton_ajouter.setEnabled(self.proposes.currentItem() is not None)
        )
        self.bouton_ajouter = QPushButton(
            icones.icone("playlist-add", TOKENS["texte_sur_accent"], 22), "  Ajouter"
        )
        self.bouton_ajouter.setObjectName("accent")
        self.bouton_ajouter.setMinimumSize(220, 44)
        self.bouton_ajouter.setEnabled(False)
        self.bouton_ajouter.clicked.connect(self._ajouter)
        couche = QVBoxLayout(self)
        couche.setContentsMargins(16, 16, 16, 12)
        couche.setSpacing(4)
        couche.addWidget(self.saisie)
        couche.addWidget(self._carte(None, self.en_base), 2)
        couche.addSpacing(12)
        couche.addWidget(self._carte("Résultats proposés :", self.proposes, self.etat), 3)
        couche.addSpacing(8)
        couche.addWidget(self.bouton_ajouter, alignment=Qt.AlignmentFlag.AlignCenter)
        self._suggerer("")

    @staticmethod
    def _carte(titre: str | None, liste: QListWidget, etat: QLabel | None = None) -> QWidget:
        carte = QWidget()
        carte.setObjectName("carte")
        carte.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v = QVBoxLayout(carte)
        v.setContentsMargins(16, 10, 16, 10)
        if titre:
            t = QLabel(titre)
            t.setObjectName("titreCarte")
            v.addWidget(t)
        v.addWidget(liste, 1)
        if etat is not None:
            v.addWidget(etat)
        return carte

    def _suggerer(self, texte: str) -> None:
        """Saisie vide : toute la base, pour la parcourir ; sinon ce qui
        correspond, les débuts de nom d'abord."""
        self.en_base.clear()
        for a in suggestions(texte, self.artistes, limite=10_000):
            item = QListWidgetItem(a.nom)
            item.setToolTip(a.detail)
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
        self.loupe.setEnabled(False)
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
        self.loupe.setEnabled(True)
        for a in candidats:
            item = QListWidgetItem(a.name)
            item.setToolTip(f"ID Genius {a.genius_id}")
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
        self.loupe.setEnabled(True)
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
