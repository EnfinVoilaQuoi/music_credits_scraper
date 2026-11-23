"""La vue Album : une ligne par projet, ses éditions dépliables (maquettes du
2026-11-21). Même DA que la vue Titre ; elle AFFICHE `presentation.albums`.
"""

from __future__ import annotations

from PyQt6.QtCore import QSettings, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTreeView

from src.gui_qt import icones
from src.gui_qt.theme import TOKENS
from src.presentation.albums import COLONNES_ALBUM, LigneAlbum
from src.presentation.table import Alignement

ROLE_TRI = Qt.ItemDataRole.UserRole + 1
ROLE_ALBUM = Qt.ItemDataRole.UserRole + 2

_ALIGN = {
    Alignement.GAUCHE: Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    Alignement.CENTRE: Qt.AlignmentFlag.AlignCenter,
    Alignement.DROITE: Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
}
_LOGOS = {"spotify": ("brand-spotify", "spotify"), "youtube": ("brand-youtube", "youtube")}


class _Item(QStandardItem):
    """Les regroupements (Featurings, Singles) restent APRÈS les projets, quel
    que soit le sens du tri : le rang passe avant la valeur, et s'inverse avec
    lui."""

    def __lt__(self, autre) -> bool:
        a, b = self.data(ROLE_ALBUM), autre.data(ROLE_ALBUM)
        if a is not None and b is not None and a.rang != b.rang:
            decroissant = getattr(self.model(), "decroissant", False)
            return (a.rang < b.rang) != decroissant
        va, vb = self.data(ROLE_TRI), autre.data(ROLE_TRI)
        if va is None or vb is None:
            return va is None and vb is not None
        try:
            return va < vb
        except TypeError:
            return str(va) < str(vb)


def section_album(cid: str) -> int:
    return next(i for i, c in enumerate(COLONNES_ALBUM) if c.id == cid)


class VueAlbums(QTreeView):
    """L'arbre des projets. Elle ÉMET la ligne choisie ; la fenêtre décide."""

    menu_album = pyqtSignal(object, object)  # LigneAlbum, position globale

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.modele = QStandardItemModel(self)
        self.modele.setHorizontalHeaderLabels([c.libelle.upper() for c in COLONNES_ALBUM])
        self.modele.setSortRole(ROLE_TRI)
        self.setModel(self.modele)
        self.albums: list[LigneAlbum] = []
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setIconSize(icones.pixmap("disc", None, 16).size())
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        e = self.header()
        e.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        e.setStretchLastSection(False)
        e.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        e.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        e.customContextMenuRequested.connect(self._menu_colonnes)
        largeurs = {
            "titre": 340,
            "date": 110,
            "type": 90,
            "morceaux": 100,
            "credits": 80,
            "paroles": 80,
            "duree": 100,
            "certif": 70,
            "editions": 80,
            "label": 180,
            "streams": 150,
        }
        for cid, largeur in largeurs.items():
            e.resizeSection(section_album(cid), largeur)
        for c in COLONNES_ALBUM:
            self.setColumnHidden(section_album(c.id), not c.visible)
        self.modele.decroissant = True
        # Notre sens AVANT le tri de la vue : le slot de la vue est relié par
        # `setSortingEnabled`, donc après celui-ci.
        e.sortIndicatorChanged.connect(self._sur_tri)
        e.setSortIndicator(section_album("date"), Qt.SortOrder.DescendingOrder)
        self.setSortingEnabled(True)

    def _sur_tri(self, _section: int, ordre: Qt.SortOrder) -> None:
        self.modele.decroissant = ordre == Qt.SortOrder.DescendingOrder

    # ── contenu ──
    def _rangee(self, a: LigneAlbum) -> list[QStandardItem]:
        rangee = []
        for c in COLONNES_ALBUM:
            item = _Item(a.valeurs.get(c.id, ""))
            item.setData(a.tri.get(c.id), ROLE_TRI)
            item.setData(a, ROLE_ALBUM)
            item.setTextAlignment(_ALIGN[c.alignement])
            infobulle = a.infobulles.get(c.id) or c.infobulle
            if infobulle:
                item.setToolTip(infobulle)
            if c.id == "titre":
                item.setIcon(icones.icone(a.icone, TOKENS["accent"], 16))
            if c.id == "streams" and a.streams_seule in _LOGOS:
                nom, couleur = _LOGOS[a.streams_seule]
                item.setIcon(icones.icone(nom, TOKENS[couleur], 14))
            rangee.append(item)
        return rangee

    def charger(self, albums: list[LigneAlbum]) -> None:
        tri = self.header().sortIndicatorSection(), self.header().sortIndicatorOrder()
        self.albums = albums
        self.modele.removeRows(0, self.modele.rowCount())
        for a in albums:
            parent = self._rangee(a)
            for e in a.editions:
                parent[0].appendRow(self._rangee(e))
            self.modele.appendRow(parent)
        if 0 <= tri[0] < len(COLONNES_ALBUM) and self.isSortingEnabled():
            self.sortByColumn(*tri)

    # ── menus ──
    def _menu(self, point) -> None:
        index = self.indexAt(point)
        a = index.data(ROLE_ALBUM) if index.isValid() else None
        if a is not None:
            self.menu_album.emit(a, self.viewport().mapToGlobal(point))

    def _menu_colonnes(self, point) -> None:
        menu = QMenu(self)
        for c in COLONNES_ALBUM:
            action = QAction(c.libelle, menu)
            action.setCheckable(True)
            action.setChecked(not self.isColumnHidden(section_album(c.id)))
            action.setEnabled(not c.obligatoire)
            section = section_album(c.id)
            action.toggled.connect(lambda oui, s=section: self.setColumnHidden(s, not oui))
            menu.addAction(action)
        menu.exec(self.header().mapToGlobal(point))

    # ── préférences ──
    def sauver(self, reglages: QSettings) -> None:
        reglages.setValue("albums/v1/etat_entete", self.header().saveState())

    def restaurer(self, reglages: QSettings) -> None:
        etat = reglages.value("albums/v1/etat_entete")
        if etat is not None:
            self.header().restoreState(etat)
