"""La liste de tâches (icône ☰✓ de la barre du haut) : le HUB de ce que fait
l'application — en cours, en attente, planifié, terminé dans la session
(décision utilisateur 2026-11-22).

En cours : progression, étape, temps restant estimé, RÉCIT live, arrêt (entre
deux morceaux, jamais au milieu d'un enregistrement). En attente : les runs
demandés pendant qu'un autre tournait, lancés l'un après l'autre (un seul
écrivain à la fois). Planifié : rien encore (la MàJ des certifs viendra).
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt, pyqtSignal
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


@dataclass(frozen=True)
class EnAttente:
    """Un run demandé pendant qu'un autre tournait."""

    cle: str
    libelle: str
    artiste: str
    options: object

    @property
    def titre(self) -> str:
        return f"{self.libelle} — {self.artiste}"


def _duree(secondes: float) -> str:
    m, s = divmod(int(secondes), 60)
    h, m = divmod(m, 60)
    return f"{h} h {m:02d}" if h else f"{m} min {s:02d}"


def resume_tache(t: Tache) -> str:
    morceaux = [t.titre, t.etat.value]
    if t.active and t.pourcentage is not None:
        morceaux.append(f"{t.pourcentage} %")
    morceaux.append(_duree(t.duree))
    if t.restant is not None:
        morceaux.append(f"reste ~{_duree(t.restant)}")
    return " · ".join(morceaux)


_ROLE_CLE = Qt.ItemDataRole.UserRole
_ROLE_ATTENTE = Qt.ItemDataRole.UserRole + 1


class CentreTaches(QDialog):
    """`retirer(index)` : retirer un run de la file d'attente."""

    retirer = pyqtSignal(int)

    def __init__(self, taches: Taches, parent=None, file_attente=None) -> None:
        super().__init__(parent)
        self.taches = taches
        #: Liste PARTAGÉE avec la fenêtre principale (lue, jamais écrite ici).
        self.file_attente: list[EnAttente] = file_attente if file_attente is not None else []
        self.setWindowTitle("Liste de tâches")
        self.resize(760, 600)
        self.liste = QListWidget()
        self.liste.setObjectName("liste")
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
        self.b_retirer = QPushButton("Retirer de la file")
        self.b_retirer.clicked.connect(self._retirer)
        bas = QHBoxLayout()
        bas.addWidget(self.etape, 1)
        bas.addWidget(self.b_retirer)
        bas.addWidget(self.arreter)
        couche = QVBoxLayout(self)
        couche.addWidget(self.liste, 2)
        couche.addLayout(bas)
        couche.addWidget(self.barre)
        couche.addWidget(self.message)
        titre = QLabel("Récit du run")
        titre.setObjectName("discret")
        couche.addWidget(titre)
        couche.addWidget(self.recit, 2)
        taches.changee.connect(self._rafraichir)
        taches.terminee.connect(self._rafraichir)
        taches.recit.connect(self._ajouter_recit)
        self._rafraichir()

    # ── liste ──
    def _section(self, titre: str) -> None:
        item = QListWidgetItem(titre)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        police = item.font()
        police.setBold(True)
        item.setFont(police)
        self.liste.addItem(item)

    def _vide(self, texte: str) -> None:
        item = QListWidgetItem(f"   {texte}")
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.liste.addItem(item)

    def _cle_courante(self) -> str | None:
        item = self.liste.currentItem()
        return item.data(_ROLE_CLE) if item else None

    def _attente_courante(self) -> int | None:
        item = self.liste.currentItem()
        return item.data(_ROLE_ATTENTE) if item else None

    def _rafraichir(self, *_) -> None:
        courante, attente = self._cle_courante(), self._attente_courante()
        self.liste.blockSignals(True)
        self.liste.clear()
        toutes = sorted(self.taches.taches.values(), key=lambda t: -t.debut)
        groupes = (
            ("En cours", [t for t in toutes if t.active], "Aucun run en cours."),
            ("Terminées dans la session", [t for t in toutes if not t.active], ""),
        )
        premier = None
        for titre, taches, vide in groupes[:1]:
            self._section(titre)
            if not taches:
                self._vide(vide)
            for t in taches:
                premier = premier or self._item_tache(t, courante)
        self._section("En attente")
        if not self.file_attente:
            self._vide("Rien en attente.")
        for i, e in enumerate(self.file_attente):
            item = QListWidgetItem(e.titre)
            item.setData(_ROLE_ATTENTE, i)
            self.liste.addItem(item)
            if attente == i:
                self.liste.setCurrentItem(item)
        self._section("Planifiées")
        self._vide("Aucune tâche planifiée (la MàJ des bases de certifs viendra ici).")
        titre, taches, _ = groupes[1]
        if taches:
            self._section(titre)
            for t in taches:
                premier = premier or self._item_tache(t, courante)
        if self.liste.currentItem() is None and premier is not None:
            self.liste.setCurrentItem(premier)
        self.liste.blockSignals(False)
        self._afficher_courante()

    def _item_tache(self, t: Tache, courante: str | None) -> QListWidgetItem:
        item = QListWidgetItem(resume_tache(t))
        item.setData(_ROLE_CLE, t.cle)
        self.liste.addItem(item)
        if t.cle == courante:
            self.liste.setCurrentItem(item)
        return item

    # ── détail ──
    def _afficher_courante(self) -> None:
        t = self.taches.tache(self._cle_courante() or "")
        self.arreter.setEnabled(bool(t and t.active and not t.arret_demande))
        self.b_retirer.setEnabled(self._attente_courante() is not None)
        if t is None:
            self.etape.setText("" if self.taches.taches else "Aucun run dans cette session.")
            self.barre.setVisible(False)
            self.message.setText("")
            self.recit.clear()
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

    def _retirer(self) -> None:
        i = self._attente_courante()
        if i is not None:
            self.retirer.emit(i)
