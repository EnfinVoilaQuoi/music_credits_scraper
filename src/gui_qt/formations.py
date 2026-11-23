"""La fenêtre Alias • Formations (maquette du 2026-11-21).

Ouverture = LECTURE de la base, sans réseau ; « Rech. Manuelle » interroge
MusicBrainz et Discogs en fond (`chercher_formations`, qui n'écrit rien) et
AJOUTE le neuf. Chaque clic est une décision écrite tout de suite, par les
écrivains d'artiste (`record_artist_relations`, `propose_artist_relations`,
`set_relation_status`) — les mêmes que la fenêtre CustomTkinter. Refuser est
une mémoire : un run ne repropose jamais un lien refusé.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
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

from src.gui_qt import icones
from src.gui_qt.pont import en_fond
from src.gui_qt.theme import TOKENS
from src.presentation import formations as presentation
from src.presentation.formations import Decision, LigneFormation
from src.utils.formations import candidats_de_base, chercher_formations, reunir
from src.utils.logger import get_logger

logger = get_logger(__name__)


def bouton_decision(oui: bool, plein: bool, infobulle: str) -> QToolButton:
    """Le carré ✓ (vert) ou ✖ (rouge) : PLEIN = à décider, contour = décidé
    (convention commune à l'app, validée le 2026-11-22)."""
    b = QToolButton()
    b.setObjectName("oui" if oui else "non")
    b.setProperty("plein", plein)
    couleur = TOKENS["texte_sur_accent"] if plein else TOKENS["ok" if oui else "erreur"]
    b.setIcon(icones.icone("check" if oui else "x", couleur, 22))
    b.setIconSize(QSize(22, 22))
    b.setFixedSize(36, 36)
    b.setToolTip(infobulle)
    b.setAccessibleName(infobulle)
    return b


class FenetreFormations(QDialog):
    """Une décision = un clic. `modifie` dit à la fenêtre principale de relire
    l'artiste (ses formations changent sa discographie réunie)."""

    modifie = pyqtSignal()

    def __init__(self, runtime, artist, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.artist = artist
        self.dm = runtime.data_manager
        #: Les candidats de la dernière recherche, gardés tant qu'ils ne sont
        #: pas en base (une recherche n'écrit rien).
        self._recherche: list = []
        #: Nature choisie sur une formation PAS encore confirmée : écrite au ✓.
        self._natures: dict[tuple[str, str], str] = {}
        self.candidats: list = []
        self.setWindowTitle("Alias • Formations")
        self.resize(760, 860)
        couche = QVBoxLayout(self)
        couche.setContentsMargins(12, 12, 12, 12)
        self.etat = QLabel()
        self.etat.setObjectName("discret")
        self.etat.setWordWrap(True)
        couche.addWidget(self.etat)
        zone = QScrollArea()
        zone.setWidgetResizable(True)
        self.corps = QWidget()
        self.couche_corps = QVBoxLayout(self.corps)
        self.couche_corps.setSpacing(16)
        zone.setWidget(self.corps)
        couche.addWidget(zone, 1)
        self.bouton_chercher = QPushButton(
            icones.icone("user-search", TOKENS["texte_sur_accent"]), "  Rech. Manuelle"
        )
        self.bouton_chercher.setObjectName("accent")
        self.bouton_chercher.setToolTip(
            "Interroger MusicBrainz et Discogs (1 requête/s) ; le neuf s'ajoute aux "
            "propositions, rien n'est écrit sans un clic."
        )
        self.bouton_chercher.clicked.connect(self.chercher)
        pied = QHBoxLayout()
        pied.addStretch()
        pied.addWidget(self.bouton_chercher)
        pied.addStretch()
        couche.addLayout(pied)
        self.relire()

    # ── lecture ──
    def relire(self) -> None:
        base = candidats_de_base(self.dm.get_artist_relations(self.artist.id, status=None))
        self.candidats = reunir(base, self._recherche) if self._recherche else base
        n = presentation.a_decider(self.candidats)
        self.etat.setText(
            f"{n} lien{'s' if n > 1 else ''} à décider." if n else "Aucun lien à décider."
        )
        self._afficher()

    def _afficher(self) -> None:
        while self.couche_corps.count():
            item = self.couche_corps.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        cartes = presentation.cartes(self.candidats)
        for titre, texte in (
            (presentation.CARTE_ALIAS, presentation.TEXTE_ALIAS),
            (presentation.CARTE_NOM_LEGAL, ""),
            (presentation.CARTE_FORMATIONS, presentation.TEXTE_FORMATIONS),
        ):
            self.couche_corps.addWidget(self._carte(titre, texte, cartes[titre]))
        self.couche_corps.addStretch()

    def _carte(self, titre: str, texte: str, lignes: list[LigneFormation]) -> QWidget:
        carte = QWidget()
        carte.setObjectName("carte")
        carte.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v = QVBoxLayout(carte)
        v.setContentsMargins(20, 14, 20, 14)
        t = QLabel(titre)
        t.setObjectName("titreCarte")
        v.addWidget(t)
        if texte:
            d = QLabel(texte)
            d.setObjectName("discret")
            d.setWordWrap(True)
            v.addWidget(d)
        if not lignes:
            vide = QLabel("—")
            vide.setObjectName("discret")
            v.addWidget(vide)
        for ligne in lignes:
            v.addLayout(self._ligne(ligne))
        return carte

    def _ligne(self, ligne: LigneFormation) -> QHBoxLayout:
        h = QHBoxLayout()
        nom = QLabel(ligne.nom)
        nom.setStyleSheet("font-size: 13pt;")
        if ligne.detail:
            nom.setToolTip(ligne.detail)
        h.addWidget(nom)
        if ligne.detail:
            detail = QLabel(ligne.detail)
            detail.setObjectName("discret")
            h.addWidget(detail)
        h.addStretch()
        if ligne.est_formation:
            h.addWidget(self._bascule_nature(ligne))
            h.addSpacing(12)
        d = ligne.decision
        if d is Decision.A_DECIDER or d is Decision.INFO:
            plein = d is Decision.A_DECIDER
            oui = bouton_decision(True, plein, "Confirmer")
            non = bouton_decision(False, plein, "Refuser (jamais reproposé)")
            oui.clicked.connect(lambda: self.confirmer(ligne))
            non.clicked.connect(lambda: self.refuser(ligne))
            h.addWidget(oui)
            h.addWidget(non)
        else:
            choisi = d is Decision.CONFIRME
            b = bouton_decision(
                choisi, False, ("Confirmé" if choisi else "Refusé") + " — clic : rétablir"
            )
            b.clicked.connect(lambda: self.retablir(ligne))
            # Le carré décidé reste à SA place (✓ à gauche, ✖ à droite).
            if not choisi:
                h.addSpacing(42)
            h.addWidget(b)
            if choisi:
                h.addSpacing(42)
        return h

    def _bascule_nature(self, ligne: LigneFormation) -> QWidget:
        cle = (ligne.nom, ligne.candidat.kind)
        nature = self._natures.get(cle, ligne.nature)
        boite = QWidget()
        h = QHBoxLayout(boite)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        for valeur, libelle in (("groupe", "Groupe"), ("collectif", "Collectif")):
            b = QToolButton()
            b.setText(libelle)
            b.setObjectName("bascule")
            b.setCheckable(True)
            b.setChecked(nature == valeur)
            b.setMinimumHeight(36)
            b.clicked.connect(lambda _=False, v=valeur: self.choisir_nature(ligne, v))
            h.addWidget(b)
        return boite

    # ── gestes ──
    def _apres(self) -> None:
        self.relire()
        self.modifie.emit()

    def confirmer(self, ligne: LigneFormation) -> None:
        c = ligne.candidat
        nature = self._natures.pop((c.related_name, c.kind), None) or ligne.nature
        self.dm.record_artist_relations(self.artist.id, [c.vers_relation(nature)])
        if c.kind == "member_of" and not nature:
            QMessageBox.information(
                self,
                "Nature à choisir",
                f"« {c.related_name} » est confirmé sans nature : choisissez Groupe ou "
                "Collectif, sinon il ne change rien à la discographie.",
            )
        self._apres()

    def refuser(self, ligne: LigneFormation) -> None:
        c = ligne.candidat
        if c.status is None:
            self.dm.propose_artist_relations(self.artist.id, [c.vers_relation()])
        self.dm.set_relation_status(self.artist.id, c.related_name, c.kind, "refused")
        self._apres()

    def retablir(self, ligne: LigneFormation) -> None:
        c = ligne.candidat
        self.dm.set_relation_status(self.artist.id, c.related_name, c.kind, "proposed")
        self._apres()

    def choisir_nature(self, ligne: LigneFormation, nature: str) -> None:
        """Sur une formation confirmée, la nature s'écrit tout de suite — à la
        main, donc verrouillée contre les règles ; sinon elle attend le ✓."""
        c = ligne.candidat
        if ligne.decision is Decision.CONFIRME:
            self.dm.set_relation_status(
                self.artist.id, c.related_name, c.kind, "confirmed", formation=nature
            )
            self._apres()
        else:
            self._natures[(c.related_name, c.kind)] = nature
            self._afficher()

    def chercher(self) -> None:
        self.bouton_chercher.setEnabled(False)
        self.etat.setText("Recherche en cours (MusicBrainz est limité à 1 requête/seconde)…")
        en_fond(
            lambda: chercher_formations(self.artist, self.dm),
            self._sur_recherche,
            self._echec,
            nom="qt:formations",
        )

    def _sur_recherche(self, rapport) -> None:
        self.bouton_chercher.setEnabled(True)
        self._recherche = list(rapport.candidats)
        self.relire()
        nouveaux = sum(1 for c in self.candidats if c.status is None)
        lignes = [f"{nouveaux} nouveau(x) candidat(s), rien n'est écrit sans un clic."]
        if rapport.identite_mb:
            lignes.insert(0, f"MusicBrainz : {rapport.identite_mb}")
        self.etat.setText("\n".join([*lignes, *rapport.diagnostics]))

    def _echec(self, message: str) -> None:
        self.bouton_chercher.setEnabled(True)
        self.etat.setText(f"Recherche impossible : {message}")
