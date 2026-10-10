"""Le centre des tâches : les runs de la session, leur progression, leur étape,
leur durée, leur état final (« partiel » dit en clair), le RÉCIT live et
l'arrêt (entre deux morceaux, jamais au milieu d'un enregistrement).

La planification persistante (file d'attente, icône `list-check`) est V2.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from src.gui_qt.pont import Etat, Tache, Taches


def _duree(secondes: float) -> str:
    m, s = divmod(int(secondes), 60)
    h, m = divmod(m, 60)
    return f"{h} h {m:02d}" if h else f"{m} min {s:02d}"


def resume_tache(t: Tache) -> str:
    pct = f" — {t.pourcentage} %" if t.pourcentage is not None and t.active else ""
    return f"{t.titre} · {t.etat.value}{pct} · {_duree(t.duree)}"


class CentreTaches(QDialog):
    def __init__(self, taches: Taches, parent=None) -> None:
        super().__init__(parent)
        self.taches = taches
        self.setWindowTitle("Tâches de la session")
        self.resize(720, 520)
        self.liste = QListWidget()
        self.liste.currentItemChanged.connect(lambda *_: self._afficher_courante())
        self.etape = QLabel()
        self.etape.setWordWrap(True)
        self.barre = QProgressBar()
        self.barre.setTextVisible(True)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.recit = QPlainTextEdit()
        self.recit.setReadOnly(True)
        self.recit.setMaximumBlockCount(5000)
        self.arreter = QPushButton("Arrêter")
        self.arreter.setToolTip("Arrêt ENTRE deux morceaux : le morceau en cours se termine.")
        self.arreter.clicked.connect(self._arreter)
        bas = QHBoxLayout()
        bas.addWidget(self.etape, 1)
        bas.addWidget(self.arreter)
        couche = QVBoxLayout(self)
        couche.addWidget(self.liste, 1)
        couche.addLayout(bas)
        couche.addWidget(self.barre)
        couche.addWidget(self.message)
        couche.addWidget(QLabel("Récit du run"))
        couche.addWidget(self.recit, 2)
        taches.changee.connect(self._rafraichir)
        taches.recit.connect(self._ajouter_recit)
        self._rafraichir()

    def _cle_courante(self) -> str | None:
        item = self.liste.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _rafraichir(self, *_) -> None:
        courante = self._cle_courante()
        self.liste.blockSignals(True)
        self.liste.clear()
        for t in sorted(self.taches.taches.values(), key=lambda t: -t.debut):
            item = QListWidgetItem(resume_tache(t))
            item.setData(Qt.ItemDataRole.UserRole, t.cle)
            self.liste.addItem(item)
            if t.cle == courante or (courante is None and self.liste.count() == 1):
                self.liste.setCurrentItem(item)
        self.liste.blockSignals(False)
        self._afficher_courante()

    def _afficher_courante(self) -> None:
        t = self.taches.tache(self._cle_courante() or "")
        self.arreter.setEnabled(bool(t and t.active and not t.arret_demande))
        if t is None:
            self.etape.setText("Aucun run dans cette session.")
            self.barre.setVisible(False)
            self.message.setText("")
            return
        self.etape.setText(" — ".join(x for x in (t.etape, t.libelle) if x))
        pct = t.pourcentage
        self.barre.setVisible(t.active)
        self.barre.setRange(0, 0 if pct is None else 100)
        if pct is not None:
            self.barre.setValue(pct)
        self.message.setText("" if t.etat is Etat.EN_COURS else t.message)
        texte = "\n".join(t.journal)
        if self.recit.toPlainText() != texte:
            self.recit.setPlainText(texte)

    def _ajouter_recit(self, ligne: str) -> None:
        t = self.taches.tache(self._cle_courante() or "")
        if t is not None and t.active:
            self.recit.appendPlainText(ligne)

    def _arreter(self) -> None:
        cle = self._cle_courante()
        if cle:
            self.taches.demander_arret(cle)
