"""La vue de la table : liseré de catégorie, badges, logo de plateforme, en-tête.

Ébauche (2026-10-11) :
  · la catégorie se lit par un LISERÉ dans la cellule de coche (principal,
    feat, prod, rôle secondaire) ; « Afficher les lignes colorées » étend la
    couleur à toute la ligne (piste à l'essai) ;
  · badges discrets INÉDIT / INSTRU à droite du titre ;
  · un logo de plateforme dans la colonne Streams SEULEMENT quand l'autre manque ;
  · en-tête : colonne triée en gras, clic droit = colonnes visibles, la coche
    d'en-tête (vide / partielle / pleine) ne trie pas : elle coche les lignes
    VISIBLES et cochables ;
  · clavier : Entrée = fiche, Espace = cocher, Suppr = désactiver.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QRect, QSettings, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QTableView,
    QToolTip,
)

from src.gui_qt import icones
from src.gui_qt.modele import COLONNE_COCHE, ROLE_LIGNE, ModeleMorceaux, ProxyMorceaux
from src.gui_qt.theme import TOKENS, couleur_categorie
from src.presentation import pied
from src.presentation.table import COLONNES, LIBELLE_PARTICIPATION, TRI_PAR_DEFAUT

_RANG = {c.id: i + 1 for i, c in enumerate(COLONNES)}


def section_de(colonne_id: str) -> int:
    return _RANG[colonne_id]


class Affichage:
    """Options d'affichage partagées par les délégués (case du filtre)."""

    def __init__(self) -> None:
        self.lignes_colorees = False
        self.badges = True


class _DelegueBase(QStyledItemDelegate):
    def __init__(self, affichage: Affichage, parent=None) -> None:
        super().__init__(parent)
        self.affichage = affichage

    def _teinte(self, painter: QPainter, option, ligne) -> None:
        if not self.affichage.lignes_colorees or ligne is None or ligne.desactive:
            return
        couleur = QColor(couleur_categorie(ligne.participation))
        couleur.setAlpha(55)
        painter.fillRect(option.rect, couleur)

    def paint(self, painter, option, index) -> None:
        self._teinte(painter, option, index.data(ROLE_LIGNE))
        super().paint(painter, option, index)


class DelegueCoche(_DelegueBase):
    """La case, plus le liseré de la catégorie à gauche de la cellule."""

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        ligne = index.data(ROLE_LIGNE)
        if ligne is None:
            return
        couleur = TOKENS["desactive"] if ligne.desactive else couleur_categorie(ligne.participation)
        r = option.rect
        painter.fillRect(QRect(r.left(), r.top() + 2, 4, r.height() - 4), QColor(couleur))


class DelegueTitre(_DelegueBase):
    """Le titre, et à sa droite les badges INÉDIT / INSTRU."""

    def _badges(self, ligne) -> list[tuple[str, str]]:
        if not self.affichage.badges or ligne is None:
            return []
        badges = []
        if ligne.inedit:
            badges.append(("INÉDIT", TOKENS["inedit"]))
        if ligne.instrumental:
            badges.append(("INSTRU", TOKENS["instru"]))
        return badges

    def paint(self, painter, option, index) -> None:
        ligne = index.data(ROLE_LIGNE)
        badges = self._badges(ligne)
        if not badges:
            super().paint(painter, option, index)
            return
        self._teinte(painter, option, ligne)
        police = QFont(option.font)
        police.setPointSizeF(max(6.0, police.pointSizeF() * 0.7))
        fm = QFontMetrics(police)
        largeurs = [fm.horizontalAdvance(t) + 10 for t, _ in badges]
        reserve = sum(largeurs) + 6 * len(badges) + 4
        opt = type(option)(option)
        opt.rect = option.rect.adjusted(0, 0, -reserve, 0)
        QStyledItemDelegate.paint(self, painter, opt, index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(police)
        x = option.rect.right() - reserve + 4
        h = fm.height() + 2
        y = option.rect.center().y() - h // 2
        for (texte, couleur), largeur in zip(badges, largeurs, strict=True):
            boite = QRect(x, y, largeur, h)
            painter.setPen(QPen(QColor(couleur), 1))
            painter.drawRoundedRect(boite, 3, 3)
            painter.drawText(boite, Qt.AlignmentFlag.AlignCenter, texte)
            x += largeur + 6
        painter.restore()


class DelegueStreams(_DelegueBase):
    """Le nombre, précédé du logo de la plateforme SEULE comptée quand l'autre
    manque (aucun logo quand le cumul est complet ou YouTube seul légitime)."""

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        ligne = index.data(ROLE_LIGNE)
        if ligne is None or not ligne.streams_seule:
            return
        nom = "brand-spotify" if ligne.streams_seule == "spotify" else "brand-youtube"
        couleur = TOKENS[ligne.streams_seule]
        texte = index.data(Qt.ItemDataRole.DisplayRole) or ""
        largeur_texte = option.fontMetrics.horizontalAdvance(texte)
        taille = 14
        x = option.rect.right() - largeur_texte - taille - 10
        y = option.rect.center().y() - taille // 2
        painter.drawPixmap(x, y, icones.pixmap(nom, couleur, taille))


class EnTete(QHeaderView):
    """Colonne triée en gras, coche d'en-tête, menu des colonnes visibles."""

    coche_entete = pyqtSignal()
    colonnes_changees = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.etat_coche = "vide"
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu_colonnes)

    def paintSection(self, painter, rect, section) -> None:
        painter.save()
        police = QFont(painter.font())
        police.setBold(self.isSortIndicatorShown() and section == self.sortIndicatorSection())
        painter.setFont(police)
        super().paintSection(painter, rect, section)
        painter.restore()
        if section == COLONNE_COCHE:
            opt = QStyleOptionButton()
            taille = 16
            opt.rect = QRect(
                rect.center().x() - taille // 2, rect.center().y() - taille // 2, taille, taille
            )
            opt.state = (
                QStyle.StateFlag.State_Enabled
                | {
                    "vide": QStyle.StateFlag.State_Off,
                    "partielle": QStyle.StateFlag.State_NoChange,
                    "pleine": QStyle.StateFlag.State_On,
                }[self.etat_coche]
            )
            self.style().drawControl(QStyle.ControlElement.CE_CheckBox, opt, painter)

    def mousePressEvent(self, event) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.logicalIndexAt(event.position().toPoint()) == COLONNE_COCHE
        ):
            self.coche_entete.emit()
            return
        super().mousePressEvent(event)

    def _menu_colonnes(self, point) -> None:
        menu = QMenu(self)
        for colonne in COLONNES:
            action = QAction(colonne.libelle, menu)
            action.setCheckable(True)
            action.setChecked(not self.isSectionHidden(section_de(colonne.id)))
            action.setEnabled(not colonne.obligatoire)
            section = section_de(colonne.id)
            action.toggled.connect(lambda oui, s=section: self._montrer(s, oui))
            menu.addAction(action)
        menu.exec(self.mapToGlobal(point))

    def _montrer(self, section: int, oui: bool) -> None:
        self.setSectionHidden(section, not oui)
        self.colonnes_changees.emit()

    def colonnes_masquees(self) -> list[str]:
        return [c.id for c in COLONNES if self.isSectionHidden(section_de(c.id))]

    def masquer(self, ids) -> None:
        for colonne in COLONNES:
            if not colonne.obligatoire:
                self.setSectionHidden(section_de(colonne.id), colonne.id in set(ids))


class VueMorceaux(QTableView):
    """La table. Elle ÉMET des intentions ; la fenêtre décide."""

    ouvrir = pyqtSignal(object)  # LigneMorceau — fiche (dock)
    ouvrir_fenetre = pyqtSignal(object)  # LigneMorceau — fenêtre détachée
    courante = pyqtSignal(object)  # LigneMorceau | None — la fiche du dock suit
    desactiver = pyqtSignal(list, bool)  # ids, True = désactiver / False = réactiver
    menu_ligne = pyqtSignal(object, object)  # LigneMorceau, position globale

    def __init__(self, modele: ModeleMorceaux, proxy: ProxyMorceaux, parent=None) -> None:
        super().__init__(parent)
        self.modele, self.proxy = modele, proxy
        self.affichage = Affichage()
        self.setModel(proxy)
        self.entete = EnTete(self)
        self.setHorizontalHeader(self.entete)
        self.setSortingEnabled(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(True)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(30)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.doubleClicked.connect(lambda i: self._emettre(self.ouvrir_fenetre, i))

        self.setItemDelegate(_DelegueBase(self.affichage, self))
        self.setItemDelegateForColumn(COLONNE_COCHE, DelegueCoche(self.affichage, self))
        self.setItemDelegateForColumn(section_de("titre"), DelegueTitre(self.affichage, self))
        self.setItemDelegateForColumn(section_de("streams"), DelegueStreams(self.affichage, self))

        e = self.entete
        e.setStretchLastSection(False)
        e.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        e.resizeSection(COLONNE_COCHE, 44)
        e.setSectionResizeMode(COLONNE_COCHE, QHeaderView.ResizeMode.Fixed)
        largeurs = {
            "titre": 320,
            "artiste": 170,
            "album": 190,
            "date": 110,
            "duree": 70,
            "credits": 80,
            "paroles": 80,
            "bpm": 140,
            "prod": 180,
            "certif": 70,
            "streams": 130,
            "statut": 60,
        }
        for cid, largeur in largeurs.items():
            e.resizeSection(section_de(cid), largeur)
        e.masquer([c.id for c in COLONNES if not c.visible])
        e.coche_entete.connect(self._basculer_coche_entete)
        modele.selection_changee.connect(self._rafraichir_coche_entete)
        proxy.layoutChanged.connect(self._rafraichir_coche_entete)
        proxy.rowsInserted.connect(self._rafraichir_coche_entete)
        proxy.rowsRemoved.connect(self._rafraichir_coche_entete)
        proxy.modelReset.connect(self._rafraichir_coche_entete)
        self.selectionModel().currentRowChanged.connect(
            lambda courant, _: self.courante.emit(courant.data(ROLE_LIGNE))
        )
        self.setIconSize(QSize(14, 14))

    # ── lignes ──
    def ligne_a(self, index_proxy):
        return index_proxy.data(ROLE_LIGNE) if index_proxy.isValid() else None

    def lignes_selectionnees(self) -> list:
        rangs = {i.row() for i in self.selectionModel().selectedRows()}
        return [self.proxy.index(r, 0).data(ROLE_LIGNE) for r in sorted(rangs)]

    def _emettre(self, signal, index) -> None:
        ligne = self.ligne_a(index)
        if ligne is not None:
            signal.emit(ligne)

    # ── coche d'en-tête ──
    def _rafraichir_coche_entete(self, *_) -> None:
        self.entete.etat_coche = pied.etat_coche_entete(
            self.modele.selection, self.proxy.lignes_visibles()
        )
        self.entete.viewport().update()

    def _basculer_coche_entete(self) -> None:
        visibles = self.proxy.lignes_visibles()
        ids = [ligne.id for ligne in visibles if ligne.cochable]
        pleine = pied.etat_coche_entete(self.modele.selection, visibles) == "pleine"
        self.modele.cocher(ids, not pleine)

    # ── clavier et menu ──
    def keyPressEvent(self, event) -> None:
        touche = event.key()
        if touche in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._emettre(self.ouvrir, self.currentIndex())
            return
        if touche == Qt.Key.Key_Space:
            lignes = [ligne for ligne in self.lignes_selectionnees() if ligne.cochable]
            if lignes:
                tout_coche = all(ligne.id in self.modele.selection for ligne in lignes)
                self.modele.cocher([ligne.id for ligne in lignes], not tout_coche)
            return
        if touche == Qt.Key.Key_Delete:
            lignes = [ligne for ligne in self.lignes_selectionnees() if ligne.id is not None]
            if lignes:
                desactiver = not all(ligne.desactive for ligne in lignes)
                self.desactiver.emit([ligne.id for ligne in lignes], desactiver)
            return
        super().keyPressEvent(event)

    def _menu(self, point) -> None:
        ligne = self.ligne_a(self.indexAt(point))
        if ligne is not None:
            self.menu_ligne.emit(ligne, self.viewport().mapToGlobal(point))

    def viewportEvent(self, event) -> bool:
        # Infobulle de la catégorie sur le liseré (accessibilité : jamais la
        # couleur seule — le texte la dit).
        if event.type() == QEvent.Type.ToolTip:
            index = self.indexAt(event.pos())
            ligne = self.ligne_a(index)
            if ligne is not None and index.column() == COLONNE_COCHE:
                QToolTip.showText(event.globalPos(), LIBELLE_PARTICIPATION[ligne.participation])
                return True
        return super().viewportEvent(event)

    # ── préférences ──
    def sauver(self, reglages: QSettings) -> None:
        reglages.setValue("table/masquees", self.entete.colonnes_masquees())
        reglages.setValue("table/etat_entete", self.entete.saveState())

    def restaurer(self, reglages: QSettings) -> None:
        etat = reglages.value("table/etat_entete")
        if etat is not None:
            self.entete.restoreState(etat)
        masquees = reglages.value("table/masquees")
        if masquees is not None:
            self.entete.masquer(list(masquees) if isinstance(masquees, list) else [masquees])


def colonne_triee_par_defaut(vue: VueMorceaux) -> None:
    colonne, decroissant = TRI_PAR_DEFAUT
    ordre = Qt.SortOrder.DescendingOrder if decroissant else Qt.SortOrder.AscendingOrder
    vue.sortByColumn(section_de(colonne), ordre)
