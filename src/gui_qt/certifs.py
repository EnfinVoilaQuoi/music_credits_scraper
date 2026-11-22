"""La fenêtre Certifications (icône 🏅, pastille « ! » quand une base attend
sa mise à jour).

Ce qui ÉCRIT (MàJ d'une base, de toutes, recherche par artiste, application à
l'artiste chargé) passe par la LISTE DE TÂCHES (`Taches`) : un seul écrivain à
la fois, progression, récit et arrêt au même endroit que les runs. Ce qui ne
fait que lire (état des bases, périodes manquantes) part en fond.

Les outils de maintenance de la fenêtre CustomTkinter (valider le brut,
nettoyer, audit SNEP, corriger les titres corrompus) n'y sont pas encore : ils
iront derrière le mode Debug.

Lectures INJECTÉES (`etats`, `trous`) : un test ne lit jamais `data/` réel.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6 import sip
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.gui_qt.pont import en_fond
from src.gui_qt.theme import TOKENS
from src.presentation import certifs as presentation
from src.services import certifs as service

_COLONNES = ("Base", "État", "Dernière MàJ globale", "Récup. artiste", "Certifs", "Trous", "")


class FenetreCertifs(QDialog):
    """`a_jour(nb)` : le nombre de bases à mettre à jour (pastille)."""

    a_jour = pyqtSignal(int)

    def __init__(
        self,
        runtime,
        taches,
        artist=None,
        etats: Callable[[], list[tuple[str, dict]]] | None = None,
        trous: Callable[[str], dict] | None = None,
        lancer: Callable[[str, str, Callable], bool] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.taches = taches
        #: (clé, titre, corps) -> lancé ou mis en file ? La fenêtre principale
        #: passe sa FILE D'ATTENTE ; seule, la fenêtre lance ou refuse.
        self.lancer = lancer or self._lancer_seule
        taches.terminee.connect(self._apres)
        self.artist = artist
        #: () -> [(source, freshness)] — `all_certification_sources()` en vrai.
        self.etats = etats or _etats_reels
        #: source -> `periodes_manquantes` de son brut.
        self.lire_trous = trous or _trous_reels
        self.trous: dict[str, dict] = {}
        self.setWindowTitle("Certifications")
        self.resize(1080, 640)
        couche = QVBoxLayout(self)
        titre = QLabel("Bases de certifications")
        titre.setObjectName("titreCarte")
        couche.addWidget(titre)
        self.arbre = QTreeWidget()
        self.arbre.setColumnCount(len(_COLONNES))
        self.arbre.setHeaderLabels([c.upper() for c in _COLONNES])
        self.arbre.setRootIsDecorated(False)
        for i, largeur in enumerate((180, 150, 160, 150, 90, 150, 130)):
            self.arbre.setColumnWidth(i, largeur)
        couche.addWidget(self.arbre, 1)
        globales = QHBoxLayout()
        self.b_tout = QPushButton("Tout mettre à jour")
        self.b_tout.setObjectName("accent")
        self.b_tout.setToolTip("Les quatre bases, en série (liste de tâches).")
        self.b_tout.clicked.connect(self.tout_mettre_a_jour)
        self.b_trous = QPushButton("Vérifier les périodes manquantes")
        self.b_trous.clicked.connect(self.verifier_trous)
        self.b_etat = QPushButton("Actualiser l'état")
        self.b_etat.clicked.connect(self.relire)
        for b in (self.b_tout, self.b_trous, self.b_etat):
            globales.addWidget(b)
        globales.addStretch()
        couche.addLayout(globales)
        couche.addWidget(self._carte_artiste())
        self.etat = QLabel()
        self.etat.setObjectName("discret")
        self.etat.setWordWrap(True)
        couche.addWidget(self.etat)
        self.relire()

    def _carte_artiste(self) -> QWidget:
        carte = QWidget()
        carte.setObjectName("carte")
        carte.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        h = QHBoxLayout(carte)
        h.setContentsMargins(14, 10, 14, 10)
        h.addWidget(QLabel("Certifs par artiste"))
        self.noms = QLineEdit()
        self.noms.setPlaceholderText("Artiste (« ; » entre plusieurs noms : membre ; groupe)")
        if self.artist is not None:
            try:
                noms = service.noms_de_recherche_pour(self.runtime, self.artist)
            except (AttributeError, LookupError, ValueError):
                noms = [self.artist.name]
            self.noms.setText(" ; ".join(noms))
        h.addWidget(self.noms, 1)
        self.b_recuperer = QPushButton("Récupérer")
        self.b_recuperer.setToolTip(
            "SNEP, RIAA et BPI interrogés par artiste ; BRMA lu dans son corpus complet."
        )
        self.b_recuperer.clicked.connect(self.recuperer_artiste)
        h.addWidget(self.b_recuperer)
        self.b_appliquer = QPushButton("Appliquer à l'artiste chargé")
        self.b_appliquer.setEnabled(self.artist is not None)
        self.b_appliquer.clicked.connect(self.appliquer)
        h.addWidget(self.b_appliquer)
        return carte

    # ── état ──
    def relire(self) -> None:
        en_fond(self.etats, self._afficher, self._echec, nom="qt:certifs-etat")

    def _afficher(self, etats: list[tuple[str, dict]]) -> None:
        self.arbre.clear()
        a_faire = 0
        for source, frais in etats:
            lg = presentation.ligne(source, frais, self.trous.get(source))
            a_faire += lg.a_mettre_a_jour
            item = QTreeWidgetItem(
                [
                    lg.libelle,
                    lg.etat,
                    lg.derniere_maj,
                    lg.recup_artiste,
                    lg.certifications,
                    lg.trous,
                    "",
                ]
            )
            couleur = TOKENS["alerte"] if lg.a_mettre_a_jour else TOKENS["ok"]
            item.setForeground(1, QBrush(QColor(couleur)))
            if lg.detail:
                item.setToolTip(1, lg.detail)
            if source in self.trous:
                item.setToolTip(5, presentation.detail_trous(source, self.trous[source]))
            self.arbre.addTopLevelItem(item)
            b = QPushButton("Mettre à jour")
            b.clicked.connect(lambda _=False, s=source: self.mettre_a_jour(s))
            self.arbre.setItemWidget(item, 6, b)
        self.a_jour.emit(a_faire)

    def _echec(self, message: str) -> None:
        self.etat.setText(f"Lecture impossible : {message}")

    def verifier_trous(self) -> None:
        self.b_trous.setEnabled(False)
        self.etat.setText("Recherche des périodes manquantes…")

        def lire():
            return {source: self.lire_trous(source) for source in presentation.PAYS}

        def fini(trous: dict) -> None:
            self.b_trous.setEnabled(True)
            self.trous = trous
            self.etat.setText("Périodes manquantes vérifiées (détail en infobulle).")
            self.relire()

        def echec(message: str) -> None:
            self.b_trous.setEnabled(True)
            self._echec(message)

        en_fond(lire, fini, echec, nom="qt:certifs-trous")

    # ── écritures : par la liste de tâches ──
    def _lancer_seule(self, cle: str, titre: str, corps) -> bool:
        if self.taches.occupe:
            QMessageBox.information(
                self,
                "Certifications",
                "Un run est en cours : relancez quand il aura fini (liste de tâches).",
            )
            return False
        return self.taches.lancer(cle, titre, corps)

    def _lancer(self, cle: str, titre: str, corps) -> None:
        occupe = self.taches.occupe
        if self.lancer(cle, titre, corps):
            quoi = "mis en attente" if occupe else "lancé"
            self.etat.setText(f"« {titre} » {quoi} — suivi dans la liste de tâches.")

    def _apres(self, cle: str) -> None:
        if not cle.startswith("certifs:") or sip.isdeleted(self):
            return
        t = self.taches.tache(cle)
        if t is not None:
            self.etat.setText(f"{t.titre} — {t.etat.value}")
            r = t.resultat
            rapport = ""
            if isinstance(r, service.BilanCertifs):
                rapport = r.rapport or "\n".join(r.lignes)
            elif isinstance(r, _Bilan):
                rapport = r.resume()
            if rapport:
                self._rapport(t.titre, rapport)
        self.relire()

    def _rapport(self, titre: str, texte: str) -> None:
        d = QDialog(self)
        d.setWindowTitle(titre)
        d.resize(820, 560)
        zone = QPlainTextEdit(texte)
        zone.setReadOnly(True)
        QVBoxLayout(d).addWidget(zone)
        d.show()

    def mettre_a_jour(self, source: str) -> None:
        def corps(hooks):
            code, sortie = service.executer_maj(
                source, progres=lambda m: hooks.progress(0, 0, m, source)
            )
            return _Bilan([service.ligne_bilan(source, code, sortie)], code == 0)

        self._lancer(f"certifs:{source}", f"Mise à jour {source}", corps)

    def tout_mettre_a_jour(self) -> None:
        def corps(hooks):
            return service.mettre_a_jour(
                should_stop=hooks.should_stop,
                progres=lambda m: hooks.progress(0, 0, m, "Certifications"),
            )

        self._lancer("certifs:toutes", "Mise à jour des quatre bases", corps)

    def recuperer_artiste(self) -> None:
        noms = [n.strip() for n in self.noms.text().split(";") if n.strip()]
        if not noms:
            QMessageBox.information(self, "Certifications", "Saisissez un nom d'artiste.")
            return

        def corps(hooks):
            return service.rechercher_artiste(
                noms, progres=lambda m: hooks.progress(0, 0, m, "Certifs par artiste")
            )

        self._lancer("certifs:artiste", f"Certifs par artiste — {' + '.join(noms)}", corps)

    def appliquer(self) -> None:
        artist = self.artist
        if artist is None:
            return

        def corps(hooks):
            return service.appliquer(self.runtime, artist)

        self._lancer("certifs:appliquer", f"Certifs appliquées — {artist.name}", corps)


class _Bilan:
    """Le bilan d'UNE base, pour la liste de tâches (`complete`, `resume`)."""

    def __init__(self, lignes: list[str], complete: bool) -> None:
        self.lignes = lignes
        self.complete = complete
        self.motif = "" if complete else lignes[0]

    def resume(self) -> str:
        return "\n".join(self.lignes)


def _etats_reels() -> list[tuple[str, dict]]:
    from src.enrichment.cert_source import all_certification_sources

    return [(s.name, s.freshness()) for s in all_certification_sources()]


def _trous_reels(source: str) -> dict:
    from pathlib import Path

    from src.config import DATA_PATH
    from src.utils.cert_trous import BRUTS_PAR_SOURCE, periodes_manquantes

    dossier, fichier = BRUTS_PAR_SOURCE[source]
    chemin = Path(DATA_PATH) / "certifications" / dossier / fichier
    if not chemin.exists():
        return {"erreur": "fichier brut introuvable"}
    return periodes_manquantes(chemin, source)
