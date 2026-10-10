"""Modèle Qt de la table des morceaux : une coquille mince sur `LigneMorceau`.

Valeurs MÉTIER pour le tri (`ROLE_TRI`), texte formaté seulement pour
l'affichage ; la sélection (les coches) porte des IDENTIFIANTS, si bien qu'elle
survit au tri, au filtre et au rafraîchissement d'une ligne.
"""

from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor

from src.gui_qt.theme import TOKENS
from src.presentation import filtres
from src.presentation.table import COLONNES, Alignement, LigneMorceau

ROLE_LIGNE = Qt.ItemDataRole.UserRole + 1
ROLE_TRI = Qt.ItemDataRole.UserRole + 2

#: La colonne 0 est la coche ; les suivantes sont `COLONNES`, dans l'ordre.
COLONNE_COCHE = 0

_ALIGN = {
    Alignement.GAUCHE: Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    Alignement.CENTRE: Qt.AlignmentFlag.AlignCenter,
    Alignement.DROITE: Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
}


def colonne_de(section: int):
    """La `Colonne` d'une section du modèle (None pour la coche)."""
    return None if section == COLONNE_COCHE else COLONNES[section - 1]


class ModeleMorceaux(QAbstractTableModel):
    selection_changee = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.lignes: list[LigneMorceau] = []
        self.selection: set[int] = set()
        self._rang_par_id: dict[int, int] = {}

    # ── chargement ──
    def charger(self, lignes: list[LigneMorceau], *, garder_selection: bool = False) -> None:
        self.beginResetModel()
        self.lignes = list(lignes)
        self._indexer()
        if not garder_selection:
            self.selection.clear()
        else:
            self.selection &= {ligne.id for ligne in self.lignes if ligne.cochable}
        self.endResetModel()
        self.selection_changee.emit()

    def _indexer(self) -> None:
        self._rang_par_id = {ligne.id: i for i, ligne in enumerate(self.lignes) if ligne.id}

    def remplacer(self, nouvelles: list[LigneMorceau]) -> None:
        """Rafraîchit SEULEMENT les lignes données (par identifiant), sans
        réinitialiser le modèle : tri, filtre, sélection et défilement restent.
        Une ligne inconnue (morceau créé par le run) est ajoutée à la fin."""
        ajoutees = []
        for ligne in nouvelles:
            rang = self._rang_par_id.get(ligne.id)
            if rang is None:
                ajoutees.append(ligne)
                continue
            self.lignes[rang] = ligne
            if ligne.desactive:
                self.selection.discard(ligne.id)
            self.dataChanged.emit(self.index(rang, 0), self.index(rang, self.columnCount() - 1))
        if ajoutees:
            debut = len(self.lignes)
            self.beginInsertRows(QModelIndex(), debut, debut + len(ajoutees) - 1)
            self.lignes.extend(ajoutees)
            self._indexer()
            self.endInsertRows()
        self.selection_changee.emit()

    def ligne(self, rang: int) -> LigneMorceau:
        return self.lignes[rang]

    # ── coches ──
    def cocher(self, ids, oui: bool = True) -> None:
        cochables = {ligne.id for ligne in self.lignes if ligne.cochable and ligne.id in set(ids)}
        if oui:
            self.selection |= cochables
        else:
            self.selection -= set(ids)
        if self.lignes:
            self.dataChanged.emit(
                self.index(0, COLONNE_COCHE),
                self.index(len(self.lignes) - 1, COLONNE_COCHE),
                [Qt.ItemDataRole.CheckStateRole],
            )
        self.selection_changee.emit()

    # ── API du modèle ──
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: B008 — signature Qt
        return 0 if parent.isValid() else len(self.lignes)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: B008 — signature Qt
        return 0 if parent.isValid() else len(COLONNES) + 1

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        colonne = colonne_de(section)
        if role == Qt.ItemDataRole.DisplayRole:
            return "" if colonne is None else colonne.libelle.upper()
        if role == Qt.ItemDataRole.ToolTipRole:
            if colonne is None:
                return "Cocher / décocher les morceaux visibles"
            return colonne.infobulle or None
        return None

    def flags(self, index):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == COLONNE_COCHE and self.lignes[index.row()].cochable:
            base |= Qt.ItemFlag.ItemIsUserCheckable
        return base

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        ligne = self.lignes[index.row()]
        colonne = colonne_de(index.column())
        if role == ROLE_LIGNE:
            return ligne
        if colonne is None:  # la coche
            if role == Qt.ItemDataRole.CheckStateRole:
                if not ligne.cochable:
                    return None
                coche = ligne.id in self.selection
                return Qt.CheckState.Checked if coche else Qt.CheckState.Unchecked
            if role == ROLE_TRI:
                return ligne.id in self.selection
            if role == Qt.ItemDataRole.ToolTipRole:
                return ligne.infobulles.get("titre")
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return ligne.valeurs.get(colonne.id, "")
        if role == ROLE_TRI:
            return ligne.tri.get(colonne.id)
        if role == Qt.ItemDataRole.ToolTipRole:
            return ligne.infobulles.get(colonne.id) or None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN[colonne.alignement]
        if role == Qt.ItemDataRole.ForegroundRole and ligne.desactive:
            return QBrush(QColor(TOKENS["desactive"]))
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if index.column() != COLONNE_COCHE or role != Qt.ItemDataRole.CheckStateRole:
            return False
        ligne = self.lignes[index.row()]
        if not ligne.cochable:
            return False
        coche = Qt.CheckState(value) == Qt.CheckState.Checked
        if coche:
            self.selection.add(ligne.id)
        else:
            self.selection.discard(ligne.id)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        self.selection_changee.emit()
        return True


class ProxyMorceaux(QSortFilterProxyModel):
    """Filtre (`presentation.filtres.garde`) et tri par valeur métier."""

    filtre_change = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.filtre = filtres.Filtre()
        self.setSortRole(ROLE_TRI)
        self.setDynamicSortFilter(True)

    def definir_filtre(self, filtre: filtres.Filtre) -> None:
        if filtre == self.filtre:
            return
        self.filtre = filtre
        self.invalidateFilter()
        self.filtre_change.emit()

    def filterAcceptsRow(self, rang: int, parent) -> bool:
        ligne = self.sourceModel().ligne(rang)
        return filtres.garde(ligne, self.filtre)

    def lessThan(self, gauche, droite) -> bool:
        a, b = gauche.data(ROLE_TRI), droite.data(ROLE_TRI)
        if a is None or b is None:
            return b is not None
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)

    def lignes_visibles(self) -> list[LigneMorceau]:
        source = self.sourceModel()
        return [
            source.ligne(self.mapToSource(self.index(r, 0)).row()) for r in range(self.rowCount())
        ]
