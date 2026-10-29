"""La fiche d'un morceau — UN widget, posé dans le panneau latéral (qui suit la
sélection) ou dans une fenêtre détachée (double-clic). Lecture seule : son
contenu vient de `presentation.fiche`.
"""

from __future__ import annotations

import html

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.gui_qt import icones
from src.gui_qt.theme import TOKENS
from src.presentation import fiche as presentation_fiche
from src.presentation.fiche import Fiche, Onglet, Section

_LOGOS = {
    "genius": ("brand-genius", "genius"),
    "spotify": ("brand-spotify", "spotify"),
    "youtube": ("brand-youtube", "youtube"),
}


def _lien(texte: str, url: str | None) -> str:
    t = html.escape(texte)
    if not url:
        return t
    return f'<a style="color:{TOKENS["texte_doux"]}" href="{html.escape(url)}">{t}</a>'


def _label(texte: str, *, discret: bool = False, riche: bool = False) -> QLabel:
    lbl = QLabel(texte)
    lbl.setWordWrap(True)
    lbl.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    if riche:
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setOpenExternalLinks(True)
    else:
        lbl.setTextFormat(Qt.TextFormat.PlainText)
    if discret:
        lbl.setObjectName("discret")
    return lbl


def _section(section: Section) -> QWidget:
    cadre = QFrame()
    couche = QVBoxLayout(cadre)
    couche.setContentsMargins(16, 12, 16, 12)
    titre = QHBoxLayout()
    if section.icone:
        ic = QLabel()
        ic.setPixmap(icones.pixmap(section.icone, TOKENS["accent"], 20))
        titre.addWidget(ic)
    t = QLabel(f"<b>{html.escape(section.titre)}</b>")
    t.setStyleSheet("font-size: 13pt;")
    titre.addWidget(t)
    titre.addStretch()
    couche.addLayout(titre)

    if not section.entrees and not section.texte and section.vide:
        couche.addWidget(_label(section.vide, discret=True))

    techniques = any(e.source or e.date for e in section.entrees)
    if techniques:
        grille = QGridLayout()
        grille.setHorizontalSpacing(16)
        for rang, e in enumerate(section.entrees):
            grille.addWidget(_label(e.libelle, discret=e.discret), rang, 0)
            grille.addWidget(_label(_lien(e.valeur, e.url), riche=True), rang, 1)
            grille.addWidget(_label(e.source, discret=True), rang, 2)
            grille.addWidget(_label(e.date, discret=True), rang, 3)
        grille.setColumnStretch(1, 1)
        couche.addLayout(grille)
    else:
        for e in section.entrees:
            texte = e.libelle + (f" — {e.valeur}" if e.valeur else "")
            lbl = _label(_lien(texte, e.url), discret=e.discret, riche=True)
            if e.discret:
                lbl.setContentsMargins(12, 0, 0, 0)
            couche.addWidget(lbl)
    if section.texte:
        couche.addWidget(_label(section.texte))
    return cadre


def _onglet(onglet: Onglet) -> QWidget:
    page = QWidget()
    couche = QVBoxLayout(page)
    couche.setContentsMargins(0, 0, 0, 0)
    couche.setSpacing(0)
    bandeau = QLabel(onglet.bandeau)
    bandeau.setObjectName("bandeau")
    bandeau.setWordWrap(True)
    couche.addWidget(bandeau)
    defile = QScrollArea()
    defile.setWidgetResizable(True)
    contenu = QWidget()
    contenu.setObjectName("carte")
    interieur = QVBoxLayout(contenu)
    for section in onglet.sections:
        interieur.addWidget(_section(section))
    interieur.addStretch()
    defile.setWidget(contenu)
    couche.addWidget(defile, 1)
    return page


class VueFiche(QWidget):
    """En-tête, liens, onglets, pied de statut. `afficher(None)` vide la fiche."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._onglet_courant = 0
        self.couche = QVBoxLayout(self)
        self.couche.setContentsMargins(8, 8, 8, 8)
        self.titre = QLabel()
        self.titre.setStyleSheet("font-size: 15pt; font-weight: 600;")
        self.entete = QWidget()
        self.entete.setObjectName("carte")
        self.grille = QGridLayout(self.entete)
        self.grille.setContentsMargins(16, 8, 16, 8)
        self.liens = QHBoxLayout()
        self.onglets = QTabWidget()
        self.onglets.currentChanged.connect(self._memoriser_onglet)
        self.pied = QLabel()
        self.pied.setObjectName("bandeau")
        self.pied.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.vide = _label("Sélectionnez un morceau pour voir sa fiche.", discret=True)
        for w in (self.titre, self.entete):
            self.couche.addWidget(w)
        self.couche.addLayout(self.liens)
        self.couche.addWidget(self.onglets, 1)
        self.couche.addWidget(self.pied)
        self.couche.addWidget(self.vide)
        self.afficher(None)

    def _memoriser_onglet(self, i: int) -> None:
        if i >= 0:
            self._onglet_courant = i

    def _vider(self, couche) -> None:
        while couche.count():
            item = couche.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def afficher(self, ligne) -> None:
        """Affiche la fiche d'une `LigneMorceau` (ou rien)."""
        visible = ligne is not None
        for w in (self.titre, self.entete, self.onglets, self.pied):
            w.setVisible(visible)
        self.vide.setVisible(not visible)
        if not visible:
            return
        f: Fiche = presentation_fiche.construire(ligne.track, ligne.constat, ligne.contenu_dans)
        self.titre.setText(f.titre)
        self._vider(self.grille)
        moitie = (len(f.entete) + 1) // 2
        for i, e in enumerate(f.entete):
            colonne, rang = divmod(i, moitie)
            self.grille.addWidget(_label(f"{e.libelle} :", discret=True), rang, colonne * 2)
            self.grille.addWidget(_label(e.valeur or "—"), rang, colonne * 2 + 1)
        self._vider(self.liens)
        for lien in f.liens:
            nom, couleur = _LOGOS[lien.libelle]
            ic = QLabel()
            ic.setPixmap(icones.pixmap(nom, TOKENS[couleur], 20))
            self.liens.addWidget(ic)
            self.liens.addWidget(_label(_lien(lien.valeur, lien.url), riche=True))
            self.liens.addSpacing(12)
        self.liens.addStretch()
        courant = self._onglet_courant
        self.onglets.blockSignals(True)
        self.onglets.clear()
        for onglet in f.onglets:
            self.onglets.addTab(_onglet(onglet), onglet.titre)
        self.onglets.setCurrentIndex(min(courant, self.onglets.count() - 1))
        self.onglets.blockSignals(False)
        self.pied.setText(f.pied)


class FenetreFiche(QDialog):
    """La fiche détachée (double-clic, Entrée avec la touche Maj)."""

    def __init__(self, ligne, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Détails • {ligne.track.title}")
        self.resize(780, 900)
        self.setModal(False)
        couche = QVBoxLayout(self)
        couche.setContentsMargins(0, 0, 0, 0)
        self.vue = VueFiche(self)
        couche.addWidget(self.vue)
        self.vue.afficher(ligne)
        from src.gui_qt.fenetres_windows import colorer_barre_titre

        colorer_barre_titre(self, TOKENS["cadre"], TOKENS["texte_sur_accent"])
