"""La fenêtre Santé des sources (icône « santé » de la barre du haut).

Pendant de la fenêtre CustomTkinter « État des sources » : sonde (Vérif rapide
/ complète, en fond, n'écrit que `sources_health.json`) et usage réel des runs
(lecture de `source_usage_*`, période et artiste au choix). Double-clic sur
une source : ses derniers échecs et la ventilation de ses appels par flux.

Lecture et sonde sont INJECTÉES (`charger_sante`, `sonder`) : un test ne lit
jamais `data/` réel ni le réseau.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from src.gui_qt.pont import en_fond
from src.gui_qt.theme import TOKENS
from src.observability import rollup
from src.presentation import sante as presentation

_COULEURS = {"ok": "ok", "degraded": "alerte", "broken": "erreur", "unknown": "texte_discret"}
_COLONNES = ("Source", "Sonde", "Appels", "Échec comm.", "Nature", "Dernière erreur")
_TOUS = "Tous les artistes"


class FenetreSante(QDialog):
    """`sondee(nb_cassees)` après une vérification (pastille de l'icône)."""

    sondee = pyqtSignal(int)

    def __init__(
        self,
        repo,
        sources,
        charger_sante: Callable[[], dict],
        sonder: Callable[[str], dict] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.repo = repo
        self.sources = sources
        self.charger_sante = charger_sante
        #: niveau (« fast » / « full ») → nouvel état par clé ; None = grisé.
        self.sonder = sonder
        self.vues: dict[str, rollup.SourceUsageView] = {}
        self._artistes: list[tuple[int, str]] = []
        self.setWindowTitle("Santé des sources")
        self.resize(1180, 760)
        couche = QVBoxLayout(self)
        haut = QHBoxLayout()
        self.etat = QLabel()
        self.etat.setObjectName("discret")
        haut.addWidget(self.etat, 1)
        self.periode = QComboBox()
        self.periode.addItems(list(presentation.FENETRES))
        self.periode.currentIndexChanged.connect(lambda _: self.relire_usage())
        self.artiste = QComboBox()
        self.artiste.addItem(_TOUS)
        self.artiste.currentIndexChanged.connect(lambda _: self.relire_usage())
        self.b_rapide = QPushButton("Vérif rapide")
        self.b_complete = QPushButton("Vérif complète")
        self.b_rapide.clicked.connect(lambda: self.verifier("fast"))
        self.b_complete.clicked.connect(lambda: self.verifier("full"))
        for w in (self.periode, self.artiste, self.b_rapide, self.b_complete):
            haut.addWidget(w)
            w.setEnabled(w not in (self.b_rapide, self.b_complete) or sonder is not None)
        couche.addLayout(haut)
        self.bandeau = QLabel()
        self.bandeau.setStyleSheet(f"color: {TOKENS['alerte']};")
        self.bandeau.setVisible(False)
        couche.addWidget(self.bandeau)
        self.arbre = QTreeWidget()
        self.arbre.setColumnCount(len(_COLONNES))
        self.arbre.setHeaderLabels([c.upper() for c in _COLONNES])
        self.arbre.setAlternatingRowColors(True)
        self.arbre.itemDoubleClicked.connect(self._detail)
        for i, largeur in enumerate((330, 130, 170, 130, 200, 360)):
            self.arbre.setColumnWidth(i, largeur)
        couche.addWidget(self.arbre, 1)
        self.sante = self.charger_sante()
        self._remplir()
        self.relire_usage()

    # ── contenu ──
    def _remplir(self) -> None:
        ouvertes = {
            self.arbre.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
            for i in range(self.arbre.topLevelItemCount())
            if not self.arbre.topLevelItem(i).isExpanded()
        }
        self.arbre.clear()
        for f in presentation.familles(self.sources, self.sante, self.vues):
            tete = QTreeWidgetItem([f.titre, "", f.agregat])
            tete.setData(0, Qt.ItemDataRole.UserRole, f.cle)
            police = tete.font(0)
            police.setBold(True)
            tete.setFont(0, police)
            for ligne in f.lignes:
                item = QTreeWidgetItem(
                    [
                        ligne.source,
                        ligne.sonde,
                        ligne.appels,
                        ligne.echecs,
                        ligne.nature,
                        ligne.derniere_erreur,
                    ]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, ligne.cle)
                item.setToolTip(1, ligne.sonde_detail)
                item.setToolTip(5, ligne.derniere_erreur)
                item.setForeground(1, QBrush(QColor(TOKENS[_COULEURS.get(ligne.statut, "texte")])))
                if ligne.en_echec:
                    item.setForeground(3, QBrush(QColor(TOKENS["erreur"])))
                tete.addChild(item)
            self.arbre.addTopLevelItem(tete)
            tete.setExpanded(f.cle not in ouvertes)

    def relire_usage(self) -> None:
        if self.repo is None:
            return
        jours = presentation.FENETRES[self.periode.currentText()]
        choix = self.artiste.currentText()
        artist_id = next((i for i, nom in self._artistes if nom == choix), None)
        repo = self.repo

        def lire():
            lignes = repo.daily(since_day=rollup.since_day_for(jours), artist_id=artist_id)
            echecs = repo.recent_failures(limit=400)
            vues = rollup.summarize(lignes, echecs, window_days=jours)
            return vues, rollup.network_episodes(echecs), repo.artists_with_usage()

        en_fond(lire, self._sur_usage, self._echec, nom="qt:sante-usage")

    def _sur_usage(self, resultat) -> None:
        self.vues, episodes, artistes = resultat
        if artistes != self._artistes:
            self._artistes = artistes
            courant = self.artiste.currentText()
            self.artiste.blockSignals(True)
            self.artiste.clear()
            self.artiste.addItems([_TOUS, *(nom for _, nom in artistes)])
            self.artiste.setCurrentText(courant if courant in {n for _, n in artistes} else _TOUS)
            self.artiste.blockSignals(False)
        texte = presentation.bandeau(episodes)
        self.bandeau.setText(texte)
        self.bandeau.setVisible(bool(texte))
        self._remplir()

    def _echec(self, message: str) -> None:
        self.etat.setText(f"Lecture impossible : {message}")

    # ── sonde ──
    def verifier(self, niveau: str) -> None:
        if self.sonder is None:
            return
        for b in (self.b_rapide, self.b_complete):
            b.setEnabled(False)
        self.etat.setText(f"Vérification {'complète' if niveau == 'full' else 'rapide'}…")
        en_fond(lambda: self.sonder(niveau), self._sur_sonde, self._echec_sonde, nom="qt:sonde")

    def _sur_sonde(self, sante: dict) -> None:
        for b in (self.b_rapide, self.b_complete):
            b.setEnabled(True)
        self.sante = sante
        cassees = presentation.sources_cassees(sante)
        self.etat.setText(f"Vérification terminée : {cassees} source(s) cassée(s).")
        self._remplir()
        self.sondee.emit(cassees)

    def _echec_sonde(self, message: str) -> None:
        for b in (self.b_rapide, self.b_complete):
            b.setEnabled(True)
        self.etat.setText(f"Vérification impossible : {message}")

    # ── détail d'une source ──
    def _detail(self, item: QTreeWidgetItem, _colonne: int) -> None:
        cle = item.data(0, Qt.ItemDataRole.UserRole)
        if item.parent() is None or self.repo is None:
            return
        echecs = self.repo.recent_failures(cle, limit=50)
        vue = self.vues.get(cle)
        lignes = []
        if vue and vue.by_flow:
            lignes.append(
                "Appels par flux — "
                + " · ".join(f"{flux} : {n}" for flux, n in sorted(vue.by_flow.items()))
            )
            lignes.append("")
        for e in echecs:
            quand = str(e.get("occurred_at") or "").replace("T", " ")
            nature = presentation.LIBELLES_NATURE.get(e.get("issue"), e.get("issue"))
            lignes.append(f"{quand}  ·  {nature}  ·  {e.get('message') or ''}")
        if not echecs:
            lignes.append("Aucun échec enregistré pour cette source.")
        d = QDialog(self)
        d.setWindowTitle(f"Derniers échecs — {item.text(0)}")
        d.resize(820, 520)
        texte = QPlainTextEdit("\n".join(lignes))
        texte.setReadOnly(True)
        QVBoxLayout(d).addWidget(texte)
        d.show()
