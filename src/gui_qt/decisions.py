"""Le panneau Décisions • Erreurs (icône ⚠, pastille « ! »).

Pendant Qt de la fenêtre CustomTkinter « À trancher » : mêmes services, mêmes
gestes. Vues : À trancher (les seules décisions), Un run tranchera, Pour
information, Marqués normaux, Corrections auto (journal, « ↩ Rétablir »).
Par cas : ses actions (`revue_actions`), « Ouvrir » la fiche, « ✓ Normal »
(verdict mémorisé : le cas ne revient plus tant que ses preuves ne changent
pas). « ⚡ Corriger les N cas sûrs » applique `revue_auto` à la demande.

Calculé à l'ouverture sur la discographie chargée — zéro réseau, sauf une
action marquée `reseau` (lancée en fond).
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.gui_qt.pont import en_fond
from src.gui_qt.theme import TOKENS
from src.presentation import decisions as presentation
from src.presentation.decisions import Vue
from src.services import revue_actions, revue_auto
from src.services.revue import Cas, Revue, analyser, par_detecteur, tous_les_detecteurs
from src.services.vues_comptees import apres_verdict
from src.utils.logger import get_logger

logger = get_logger(__name__)


class FenetreDecisions(QDialog):
    """`modifie` : une écriture a eu lieu (la fenêtre principale relit
    l'artiste) ; `ouvrir_fiche(track_id)` : montrer la fiche d'un morceau."""

    modifie = pyqtSignal()
    ouvrir_fiche = pyqtSignal(int)
    #: Le nombre de décisions qui attendent (pastille de la barre du haut).
    compte = pyqtSignal(int)

    def __init__(
        self, runtime, artiste, renvois: dict[str, Callable] | None = None, parent=None
    ) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.dm = runtime.data_manager
        self.artiste = artiste
        self.tracks = {t.id: t for t in artiste.tracks or []}
        self.defs = {d.code: d for d in tous_les_detecteurs()}
        self.ctx_action = revue_actions.ContexteAction(
            self.dm, artiste, self.tracks, renvois=renvois or {}
        )
        self.vue = Vue.DECISIONS
        self.revue = Revue([], [])
        self.journal: list[dict] = []
        self.setWindowTitle(f"Décisions • Erreurs — {artiste.name}")
        self.resize(1080, 760)
        couche = QVBoxLayout(self)
        haut = QHBoxLayout()
        self.titre = QLabel()
        self.titre.setObjectName("titreCarte")
        haut.addWidget(self.titre, 1)
        self.filtre = QComboBox()
        self.filtre.setMinimumWidth(320)
        self.filtre.currentIndexChanged.connect(lambda _: self._afficher())
        haut.addWidget(self.filtre)
        couche.addLayout(haut)
        bandeau = QHBoxLayout()
        self.boutons_vue: dict[Vue, QToolButton] = {}
        for v in Vue:
            b = QToolButton()
            b.setObjectName("bascule")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, x=v: self.changer_vue(x))
            self.boutons_vue[v] = b
            bandeau.addWidget(b)
        bandeau.addStretch()
        self.b_surs = QPushButton()
        self.b_surs.setObjectName("accent")
        self.b_surs.clicked.connect(self._corriger_surs)
        bandeau.addWidget(self.b_surs)
        couche.addLayout(bandeau)
        aide = QLabel(
            "« ✓ Normal » : le cas ne revient plus tant que ses preuves ne changent pas. "
            "« Ouvrir » : la fiche du morceau. Les autres boutons corrigent directement."
        )
        aide.setObjectName("discret")
        aide.setWordWrap(True)
        couche.addWidget(aide)
        self.compte_rendu = QLabel()
        self.compte_rendu.setWordWrap(True)
        couche.addWidget(self.compte_rendu)
        zone = QScrollArea()
        zone.setWidgetResizable(True)
        self.corps = QWidget()
        self.liste = QVBoxLayout(self.corps)
        self.liste.setSpacing(6)
        zone.setWidget(self.corps)
        couche.addWidget(zone, 1)
        self.recalculer()

    # ── données ──
    def recalculer(self) -> None:
        self.revue = analyser(self.dm, self.artiste)
        self.journal = self.dm.corrections_revue(self.artiste.id)
        self._rafraichir()

    @property
    def cas(self) -> list[Cas]:
        return presentation.cas_de_la_vue(self.revue, self.vue)

    def changer_vue(self, vue: Vue) -> None:
        self.vue = vue
        self._rafraichir()

    def _rafraichir(self) -> None:
        for v, libelle in presentation.libelles_vues(self.revue, self.journal).items():
            self.boutons_vue[v].setText(libelle)
            self.boutons_vue[v].setChecked(v is self.vue)
        surs = revue_auto.cas_surs(self.revue)
        self.b_surs.setText(f"⚡ Corriger les {len(surs)} cas sûrs")
        self.b_surs.setEnabled(bool(surs))
        self.compte.emit(presentation.a_trancher(self.revue))
        ancien = self.filtre.currentData()
        self.filtre.blockSignals(True)
        self.filtre.clear()
        if self.vue is Vue.JOURNAL:
            self.filtre.addItem("Tout", None)
            self.titre.setText(presentation.titre_de_la_vue(self.vue, len(self.journal)))
        else:
            cas = self.cas
            self.filtre.addItem(f"Tout ({len(cas)})", None)
            compte = par_detecteur(cas)
            for d in tous_les_detecteurs():
                if compte.get(d.code):
                    self.filtre.addItem(f"{d.icone} {d.libelle} ({compte[d.code]})", d.code)
            i = self.filtre.findData(ancien)
            self.filtre.setCurrentIndex(max(i, 0))
            self.titre.setText(presentation.titre_de_la_vue(self.vue, len(cas)))
        self.filtre.blockSignals(False)
        self._afficher()

    # ── affichage ──
    def _vider(self) -> None:
        while self.liste.count():
            item = self.liste.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _afficher(self) -> None:
        self._vider()
        if self.vue is Vue.JOURNAL:
            self._afficher_journal()
            return
        code = self.filtre.currentData()
        visibles = [c for c in self.cas if code is None or c.detecteur == code]
        if not visibles:
            self._message("Rien à trancher pour ce filtre.")
        for c in visibles[: presentation.MAX_LIGNES]:
            self.liste.addWidget(self._ligne(c))
        if len(visibles) > presentation.MAX_LIGNES:
            self._message(
                f"… {len(visibles) - presentation.MAX_LIGNES} cas de moindre impact non affichés"
            )
        self.liste.addStretch()

    def _message(self, texte: str) -> None:
        m = QLabel(texte)
        m.setObjectName("discret")
        self.liste.addWidget(m)

    def _carte(self, icone: str, titre: str, sous_titre: str) -> tuple[QWidget, QVBoxLayout]:
        carte = QWidget()
        carte.setObjectName("carte")
        carte.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        h = QHBoxLayout(carte)
        h.setContentsMargins(12, 8, 12, 8)
        i = QLabel(icone)
        i.setFixedWidth(28)
        h.addWidget(i)
        texte = QVBoxLayout()
        t = QLabel(titre)
        t.setStyleSheet("font-weight: 600;")
        s = QLabel(sous_titre)
        s.setObjectName("discret")
        s.setWordWrap(True)
        texte.addWidget(t)
        texte.addWidget(s)
        h.addLayout(texte, 1)
        return carte, texte

    def _ligne(self, c: Cas) -> QWidget:
        d = self.defs.get(c.detecteur)
        carte, texte = self._carte(d.icone if d else "•", c.morceau, c.motif)
        h = carte.layout()
        actions = [] if self.vue is Vue.NORMAUX else revue_actions.actions_pour(c)
        if actions:
            barre = QHBoxLayout()
            for a in actions:
                b = QPushButton(a.libelle)
                b.clicked.connect(lambda _=False, a=a, x=c: self.agir(a, x))
                barre.addWidget(b)
            barre.addStretch()
            texte.addLayout(barre)
        impact = QLabel(presentation.format_impact(c.impact))
        impact.setFixedWidth(70)
        impact.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(impact)
        ouvrir = QPushButton("Ouvrir")
        ouvrir.setEnabled(c.track_id in self.tracks)
        ouvrir.clicked.connect(lambda _=False, i=c.track_id: self.ouvrir_fiche.emit(i))
        h.addWidget(ouvrir)
        if self.vue is Vue.NORMAUX:
            b = QPushButton("↩ Rétablir")
            b.clicked.connect(lambda _=False, x=c: self.retablir(x))
        else:
            b = QPushButton("✓ Normal")
            b.setStyleSheet(f"background: {TOKENS['ok']}; color: {TOKENS['texte_sur_accent']};")
            b.clicked.connect(lambda _=False, x=c: self.normal(x))
        h.addWidget(b)
        return carte

    def _afficher_journal(self) -> None:
        if not self.journal:
            self._message("Aucune correction automatique.")
        for corr in self.journal[: presentation.MAX_LIGNES]:
            d = self.defs.get(corr["detecteur"])
            quand = str(corr.get("applied_at") or "")[:16]
            carte, _ = self._carte(
                d.icone if d else "⚡",
                corr.get("compte_rendu") or corr.get("morceau") or "",
                f"{quand} — {corr.get('motif') or ''}",
            )
            h = carte.layout()
            track_id = corr.get("track_id")
            ouvrir = QPushButton("Ouvrir")
            ouvrir.setEnabled(track_id in self.tracks)
            ouvrir.clicked.connect(lambda _=False, i=track_id: self.ouvrir_fiche.emit(i))
            h.addWidget(ouvrir)
            b = QPushButton("↩ Rétablir")
            b.clicked.connect(lambda _=False, x=corr: self.retablir_correction(x))
            h.addWidget(b)
            self.liste.addWidget(carte)
        self.liste.addStretch()

    # ── gestes ──
    def normal(self, c: Cas) -> None:
        self.dm.trancher_cas(self.artiste.id, c.detecteur, c.cle, morceau=c.morceau, motif=c.motif)
        apres_verdict(self.dm, c)
        self.revue.actifs.remove(c)
        self.revue.masques.append(c)
        self._rafraichir()

    def retablir(self, c: Cas) -> None:
        self.dm.annuler_verdict(self.artiste.id, c.detecteur, c.cle)
        apres_verdict(self.dm, c)
        self.revue.masques.remove(c)
        self.revue.actifs.append(c)
        self.revue.actifs.sort(key=lambda x: (-x.impact, x.detecteur, x.morceau))
        self._rafraichir()

    def _corriger_surs(self) -> None:
        surs = revue_auto.cas_surs(self.revue)
        if not surs:
            return
        r = QMessageBox.question(
            self,
            "Décisions",
            f"Corriger les {len(surs)} cas à preuve formelle ?\n\n"
            "Chaque correction est consignée et reste rétablissable (vue « Corrections auto »).",
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        bilan = revue_auto.corriger(self.dm, self.artiste)
        self.recalculer()
        self.compte_rendu.setText(bilan.resume() or "Rien à corriger")
        self.modifie.emit()

    def retablir_correction(self, correction: dict) -> None:
        try:
            texte = revue_auto.retablir(self.dm, self.artiste, correction)
        except (LookupError, ValueError) as e:
            self._echec(str(e))
            return
        self.recalculer()
        self.compte_rendu.setText(f"↩ {texte} — le cas est marqué normal")
        self.modifie.emit()

    def agir(self, action, c: Cas) -> None:
        if action.confirmation:
            r = QMessageBox.question(self, "Décisions", action.confirmation)
            if r != QMessageBox.StandardButton.Yes:
                return
        if action.reseau:
            self.compte_rendu.setText(f"⏳ {action.libelle} — « {c.morceau} »…")
            en_fond(
                lambda: revue_actions.executer(action, self.ctx_action, c),
                lambda texte: self._resolu(action, c, texte),
                self._echec,
                nom="qt:decision-action",
            )
            return
        try:
            texte = revue_actions.executer(action, self.ctx_action, c)
        except Exception as e:  # noqa: BLE001 - l'échec s'affiche, rien n'est retiré
            logger.exception("Action du panneau échouée")
            self._echec(str(e))
            return
        self._resolu(action, c, texte)

    def _echec(self, message: str) -> None:
        self.compte_rendu.setText("")
        QMessageBox.critical(self, "Décisions", f"L'action a échoué : {message}")

    def _resolu(self, action, c: Cas, compte_rendu: str) -> None:
        if not action.resout:
            return  # un renvoi : la décision se prend dans la fenêtre ouverte
        if c in self.revue.actifs:
            self.revue.actifs.remove(c)
        self.compte_rendu.setText(f"✅ {compte_rendu}")
        self._rafraichir()
        self.modifie.emit()
