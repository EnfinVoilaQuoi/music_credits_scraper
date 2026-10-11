"""La fenêtre principale PyQt6 (V1) : barre d'artiste, cartouche, flux, table,
pied, et la fiche dans un panneau latéral qui suit la sélection.

Ce qui n'a pas encore sa fenêtre d'options (maquettes en cours de retravail)
lance l'ÉTAPE correspondante du cycle (`services.cycle`, mêmes défauts que la
CLI) après une confirmation qui dit sa portée — jamais en silence.
"""

from __future__ import annotations

from PyQt6.QtCore import QSettings, Qt, QTimer, QUrl
from PyQt6.QtGui import (
    QAction,
    QDesktopServices,
    QKeySequence,
    QShortcut,
    QStandardItem,
    QStandardItemModel,
)
from PyQt6.QtWidgets import (
    QCheckBox,
    QCompleter,
    QDockWidget,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.gui_qt import icones
from src.gui_qt.centre_taches import CentreTaches
from src.gui_qt.dialogues import DialogueAjout, DialogueArtistes
from src.gui_qt.fenetres_windows import colorer_barre_titre
from src.gui_qt.fiche import FenetreFiche, VueFiche
from src.gui_qt.modele import ModeleMorceaux, ProxyMorceaux
from src.gui_qt.pont import Etat, Taches, en_fond
from src.gui_qt.theme import TOKENS
from src.gui_qt.vue_table import VueMorceaux, colonne_triee_par_defaut
from src.gui_qt.widgets import BoutonFlux, CartoucheArtiste, PiedTable
from src.presentation import chargement as presentation_chargement
from src.presentation import pied as presentation_pied
from src.presentation.artistes import ResumeArtiste, suggestions
from src.presentation.filtres import Filtre
from src.utils.logger import get_logger

logger = get_logger(__name__)

ROLE_NOM = Qt.ItemDataRole.UserRole + 10

#: (clé, libellé, icône, étapes du cycle). Ordre de la barre des flux.
FLUX = (
    ("disco", "Discographie", "playlist-add", ("disco",)),
    ("identite", "Identité", "user-search", ("identite",)),
    ("credits", "Crédits & Paroles", "list-details", ("credits",)),
    ("enrich", "Données Tech.", "ruler-measure", ("enrich",)),
    ("certifs", "Certifications", "award", ("certifs",)),
    ("streams", "Streams", "chart-bar-popular", ("streams",)),
)
CYCLE = ("cycle", "Tout mettre à jour", "reload", ())

#: Cases du filtre : (attribut de `Filtre`, libellé). None = séparateur.
_CASES_FILTRE = (
    ("principal", "Titres principaux"),
    ("feat", "Invités (featurings)"),
    ("prod", "Productions"),
    ("secondaire", "Rôles secondaires"),
    None,
    ("inedit", "Inédits"),
    ("instrumental", "Instrumentaux"),
    ("version_alt", "Versions alternatives"),
    ("desactives", "Désactivés"),
)


def _bouton_icone(nom: str, infobulle: str, *, actif: bool = True) -> QToolButton:
    b = QToolButton()
    b.setIcon(icones.icone(nom, TOKENS["accent"], 24))
    b.setIconSize(b.iconSize().expandedTo(b.iconSize()))
    b.setToolTip(infobulle)
    b.setAccessibleName(infobulle)
    b.setMinimumSize(44, 44)
    b.setEnabled(actif)
    return b


class FenetrePrincipale(QMainWindow):
    def __init__(self, runtime, reglages: QSettings | None = None) -> None:
        super().__init__()
        self.runtime = runtime
        self.chargement: presentation_chargement.Chargement | None = None
        self.artistes: list[ResumeArtiste] = []
        # Préférences d'INTERFACE (géométrie, colonnes, dernier artiste), à part
        # des secrets et réglages métier (`config.Settings`). Injectables : les
        # tests ne doivent jamais écrire dans le registre réel.
        self.reglages = reglages or QSettings("theRAPie", "GUI PyQt6")
        self.taches = Taches(self)
        self.taches.changee.connect(self._sur_tache)
        self.taches.terminee.connect(self._sur_fin_tache)
        self._centre: CentreTaches | None = None
        self._fenetres_fiche: list[FenetreFiche] = []
        self.setWindowTitle("théRAPie")
        self.resize(1500, 900)

        self.modele = ModeleMorceaux(self)
        self.proxy = ProxyMorceaux(self)
        self.proxy.setSourceModel(self.modele)
        self.vue = VueMorceaux(self.modele, self.proxy)
        self.cartouche = CartoucheArtiste()
        self.pied = PiedTable()
        self.fiche = VueFiche()

        central = QWidget()
        couche = QVBoxLayout(central)
        couche.setContentsMargins(6, 6, 6, 0)
        couche.setSpacing(6)
        couche.addLayout(self._barre_artiste())
        couche.addWidget(self.cartouche)
        couche.addLayout(self._barre_flux())
        couche.addWidget(self.vue, 1)
        couche.addWidget(self.pied)
        self.setCentralWidget(central)

        self.dock = QDockWidget("Fiche", self)
        self.dock.setObjectName("fiche")
        self.dock.setWidget(self.fiche)
        self.dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)

        self._relier()
        self._restaurer()
        self._afficher_vide()
        QTimer.singleShot(0, self._charger_liste_artistes)

    # ── construction ─────────────────────────────────────────────────────────
    def _barre_artiste(self) -> QHBoxLayout:
        h = QHBoxLayout()
        self.b_ajout = _bouton_icone("plus", "Ajouter un artiste")
        self.b_base = _bouton_icone("list-details", "Base de données • Artistes")
        self.recherche = QLineEdit()
        self.recherche.setPlaceholderText("Artiste")
        self.recherche.setClearButtonEnabled(True)
        self.recherche.addAction(
            icones.icone("search", TOKENS["texte_discret"]),
            QLineEdit.ActionPosition.TrailingPosition,
        )
        self._modele_completion = QStandardItemModel(self)
        self.completion = QCompleter(self._modele_completion, self)
        self.completion.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        # Le NOM est inséré, le détail seulement affiché : `QStandardItem` confond
        # EditRole et DisplayRole, d'où un rôle à part pour la complétion.
        self.completion.setCompletionRole(ROLE_NOM)
        self.recherche.setCompleter(self.completion)
        self.b_groupes = _bouton_icone(
            "users-group",
            "Groupes & formations — fenêtre à venir (version CustomTkinter)",
            actif=False,
        )
        self.b_sources = _bouton_icone(
            "activity", "État des sources — fenêtre à venir (version CustomTkinter)", actif=False
        )
        self.b_file = _bouton_icone(
            "list-check", "File d'attente et tâches planifiées (V2)", actif=False
        )
        self.b_taches = QPushButton()
        self.b_taches.setObjectName("accent")
        self.b_taches.setToolTip("Tâches de la session")
        self.b_taches.setVisible(False)
        self.b_reglages = _bouton_icone("settings", "Paramètres (V2)", actif=False)
        for w in (self.b_ajout, self.b_base):
            h.addWidget(w)
        h.addWidget(self.recherche, 1)
        h.addWidget(self.b_groupes)
        h.addStretch()
        for w in (self.b_taches, self.b_sources, self.b_file, self.b_reglages):
            h.addWidget(w)
        return h

    def _barre_flux(self) -> QHBoxLayout:
        h = QHBoxLayout()
        self.boutons_flux: dict[str, BoutonFlux] = {}
        for cle, libelle, icone, etapes in (*FLUX, CYCLE):
            b = BoutonFlux(libelle, icone)
            b.setToolTip(
                f"Lancer « {libelle} » sur l'artiste chargé (réglages par défaut — la "
                "fenêtre d'options arrive avec les nouvelles maquettes)"
            )
            b.clicked.connect(lambda _=False, c=cle, l_=libelle, e=etapes: self._sur_flux(c, l_, e))
            self.boutons_flux[cle] = b
            h.addWidget(b)
        self.boutons_flux["cycle"].setObjectName("accent")
        self.b_export = QPushButton(icones.icone("file-export", TOKENS["accent"], 22), "  Export")
        self.b_export.setEnabled(False)
        self.b_export.setToolTip("Export — fenêtre à venir (version CustomTkinter)")
        h.addWidget(self.b_export)
        h.addSpacing(16)
        self.recherche_table = QLineEdit()
        self.recherche_table.setPlaceholderText("Rechercher dans la table (Ctrl+F)")
        self.recherche_table.setClearButtonEnabled(True)
        self.recherche_table.setMinimumWidth(180)
        self.recherche_table.setMaximumWidth(300)
        h.addWidget(self.recherche_table)
        self.b_filtre = _bouton_icone("filter", "Filtres • Affichage")
        self.b_filtre.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.b_filtre.setMenu(self._menu_filtre())
        h.addWidget(self.b_filtre)
        self.b_titre = QToolButton()
        self.b_titre.setText("Titre")
        self.b_album = QToolButton()
        self.b_album.setText("Album")
        for b in (self.b_titre, self.b_album):
            b.setObjectName("bascule")
            b.setCheckable(True)
            h.addWidget(b)
        self.b_titre.setChecked(True)
        self.b_album.setEnabled(False)
        self.b_album.setToolTip("Vue Albums — à venir (version CustomTkinter)")
        return h

    def _menu_filtre(self) -> QMenu:
        menu = QMenu(self)
        self._actions_filtre: dict[str, QAction] = {}
        for case in _CASES_FILTRE:
            if case is None:
                menu.addSeparator()
                continue
            attribut, libelle = case
            a = QAction(libelle, menu)
            a.setCheckable(True)
            a.setChecked(True)
            a.toggled.connect(self._appliquer_filtre)
            self._actions_filtre[attribut] = a
            menu.addAction(a)
        menu.addSeparator()
        self.a_couleurs = QAction("Afficher les lignes colorées", menu)
        self.a_couleurs.setCheckable(True)
        self.a_couleurs.toggled.connect(self._sur_affichage)
        self.a_badges = QAction("Afficher les badges", menu)
        self.a_badges.setCheckable(True)
        self.a_badges.setChecked(True)
        self.a_badges.toggled.connect(self._sur_affichage)
        menu.addAction(self.a_couleurs)
        menu.addAction(self.a_badges)
        return menu

    def _relier(self) -> None:
        self.b_ajout.clicked.connect(self._ouvrir_ajout)
        self.b_base.clicked.connect(self._ouvrir_base)
        self.b_taches.clicked.connect(self._ouvrir_centre)
        self.recherche.textEdited.connect(self._completer)
        self.recherche.returnPressed.connect(lambda: self.charger(self.recherche.text()))
        self.completion.activated[str].connect(self.charger)
        self.recherche_table.textChanged.connect(lambda _: self._appliquer_filtre())
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self._focus_recherche_table)
        self.modele.selection_changee.connect(self._rafraichir_pied)
        self.proxy.filtre_change.connect(self._rafraichir_pied)
        self.proxy.rowsInserted.connect(self._rafraichir_pied)
        self.proxy.modelReset.connect(self._rafraichir_pied)
        self.pied.portee_changee.connect(lambda _: self._rafraichir_pied())
        self.pied.manque_demande.connect(lambda _: self._appliquer_filtre())
        self.vue.courante.connect(self._sur_ligne_courante)
        self.vue.ouvrir.connect(self._montrer_fiche)
        self.vue.ouvrir_fenetre.connect(self._fiche_detachee)
        self.vue.desactiver.connect(self._desactiver)
        self.vue.menu_ligne.connect(self._menu_ligne)
        self.vue.entete.colonnes_changees.connect(lambda: self.vue.sauver(self.reglages))

    # ── artistes ─────────────────────────────────────────────────────────────
    def _charger_liste_artistes(self) -> None:
        en_fond(
            self.runtime.data_manager.resume_des_artistes,
            lambda lignes: self._sur_liste_artistes(lignes),
            nom="qt:artistes",
        )

    def _sur_liste_artistes(self, lignes) -> None:
        self.artistes = [ResumeArtiste.depuis_resume(d) for d in lignes]
        self._completer(self.recherche.text())

    def _completer(self, texte: str) -> None:
        self._modele_completion.clear()
        for a in suggestions(texte, self.artistes, limite=15):
            item = QStandardItem(f"{a.nom}    —  {a.detail}")
            item.setData(a.nom, ROLE_NOM)
            self._modele_completion.appendRow(item)

    def charger(self, nom: str) -> None:
        nom = (nom or "").strip()
        if not nom:
            return
        self.recherche.setText(nom)
        self.recherche.setEnabled(False)
        self.statusBar().showMessage(f"Chargement de {nom}…")
        en_fond(
            lambda: presentation_chargement.charger(self.runtime, nom),
            lambda c: self._sur_chargement(c, nom),
            self._sur_echec_chargement,
            nom="qt:charger-artiste",
        )

    def _sur_echec_chargement(self, message: str) -> None:
        self.recherche.setEnabled(True)
        self.statusBar().clearMessage()
        QMessageBox.warning(self, "Chargement impossible", message)

    def _sur_chargement(self, c, nom: str, *, garder: bool = False) -> None:
        self.recherche.setEnabled(True)
        self.statusBar().clearMessage()
        if c is None:
            if (
                QMessageBox.question(
                    self, "Artiste absent", f"« {nom} » n'est pas en base. L'ajouter ?"
                )
                == QMessageBox.StandardButton.Yes
            ):
                self._ouvrir_ajout(nom)
            return
        meme = self.chargement is not None and self.chargement.artist.id == c.artist.id
        self.chargement = c
        if garder and meme:
            anciens = {ligne.id for ligne in self.modele.lignes}
            nouveaux = {ligne.id for ligne in c.lignes}
            if anciens <= nouveaux:
                # Rafraîchissement après un run : tri, filtre, sélection et
                # défilement restent ; seules les lignes changent.
                self.modele.remplacer(c.lignes)
            else:
                self.modele.charger(c.lignes, garder_selection=True)
        else:
            self.modele.charger(c.lignes)
            colonne_triee_par_defaut(self.vue)
            self.fiche.afficher(None)
        self.cartouche.afficher(c.cartouche, getattr(c.artist, "image_path", None))
        self.setWindowTitle(f"théRAPie — {c.artist.name}")
        self.reglages.setValue("artiste", c.artist.name)
        self._etat_boutons()
        self._rafraichir_pied()

    def _afficher_vide(self) -> None:
        self.cartouche.afficher(None)
        self._etat_boutons()
        self._rafraichir_pied()

    def _ouvrir_ajout(self, nom: str = "") -> None:
        d = DialogueAjout(self.runtime, self.artistes, self)
        if nom:
            d.saisie.setText(nom)
            d._suggerer(nom)
        d.ouvrir.connect(self.charger)
        d.ajoute.connect(lambda n: (self._charger_liste_artistes(), self.charger(n)))
        d.exec()

    def _ouvrir_base(self) -> None:
        d = DialogueArtistes(self.artistes, self)
        d.ouvrir.connect(self.charger)
        d.exec()

    # ── filtre, pied ─────────────────────────────────────────────────────────
    def _focus_recherche_table(self) -> None:
        self.recherche_table.setFocus()
        self.recherche_table.selectAll()

    def _appliquer_filtre(self, *_) -> None:
        cases = {k: a.isChecked() for k, a in self._actions_filtre.items()}
        coche = next((b for c, b in self.pied.compteurs.items() if b.isChecked()), None)
        manque = next((c for c, b in self.pied.compteurs.items() if b is coche), None)
        filtre = Filtre(**cases, texte=self.recherche_table.text(), manque=manque)
        self.proxy.definir_filtre(filtre)
        self.b_filtre.setIcon(
            icones.icone(
                "filter", TOKENS["alerte"] if filtre.categories_reduites else TOKENS["accent"], 24
            )
        )

    def _sur_affichage(self, *_) -> None:
        self.vue.affichage.lignes_colorees = self.a_couleurs.isChecked()
        self.vue.affichage.badges = self.a_badges.isChecked()
        self.vue.viewport().update()

    def _rafraichir_pied(self, *_) -> None:
        if self.chargement is None:
            self.pied.afficher(None, "Aucun artiste chargé")
            return
        visibles = self.proxy.lignes_visibles()
        p = presentation_pied.calculer(self.modele.lignes, visibles, self.pied.portee)
        self.pied.afficher(p, presentation_pied.texte_selection(self.modele.selection, visibles))

    # ── fiche ────────────────────────────────────────────────────────────────
    def _sur_ligne_courante(self, ligne) -> None:
        if self.dock.isVisible():
            self.fiche.afficher(ligne)

    def _montrer_fiche(self, ligne) -> None:
        self.dock.show()
        self.dock.raise_()
        self.fiche.afficher(ligne)

    def _fiche_detachee(self, ligne) -> None:
        f = FenetreFiche(ligne, self)
        self._fenetres_fiche.append(f)
        f.finished.connect(lambda _: self._fenetres_fiche.remove(f))
        f.show()

    def _menu_ligne(self, ligne, position) -> None:
        menu = QMenu(self)
        menu.addAction("Voir la fiche", lambda: self._montrer_fiche(ligne))
        menu.addAction("Ouvrir dans une fenêtre", lambda: self._fiche_detachee(ligne))
        if ligne.id is not None:
            texte = "Réactiver ce morceau" if ligne.desactive else "Désactiver ce morceau"
            menu.addAction(texte, lambda: self._desactiver([ligne.id], not ligne.desactive))
        t = ligne.track
        liens = [
            ("Ouvrir sur Genius", t.genius_url),
            (
                "Ouvrir sur Spotify",
                t.spotify_url
                or (f"https://open.spotify.com/track/{t.spotify_id}" if t.spotify_id else None),
            ),
            ("Ouvrir sur YouTube", t.youtube_url),
        ]
        if any(url for _, url in liens):
            menu.addSeparator()
            for texte, url in liens:
                if url:
                    menu.addAction(texte, lambda u=url: QDesktopServices.openUrl(QUrl(u)))
        menu.exec(position)

    # ── désactivation ────────────────────────────────────────────────────────
    def _desactiver(self, ids: list, desactiver: bool) -> None:
        """Même mémoire que la GUI CustomTkinter (`DisabledTracksManager`, par
        artiste). Rappel du projet : désactiver APRÈS les runs, jamais avant."""
        c = self.chargement
        if c is None:
            return
        actuels = set(c.ctx.desactives)
        actuels = actuels | set(ids) if desactiver else actuels - set(ids)
        self.runtime.disabled.save_disabled_tracks(c.artist.name, actuels)
        self.modele.remplacer(c.avec_desactives(actuels))
        self.cartouche.afficher(c.cartouche, getattr(c.artist, "image_path", None))
        self._rafraichir_pied()

    # ── runs ─────────────────────────────────────────────────────────────────
    def _etat_boutons(self) -> None:
        charge = self.chargement is not None
        occupe = self.taches.occupe
        for b in self.boutons_flux.values():
            b.setEnabled(charge and (not occupe or b.en_cours))

    def _sur_flux(self, cle: str, libelle: str, etapes: tuple) -> None:
        if self.boutons_flux[cle].en_cours:
            self._ouvrir_centre()
            return
        c = self.chargement
        if c is None or self.taches.occupe:
            return
        boite = QMessageBox(self)
        boite.setWindowTitle(libelle)
        boite.setIcon(QMessageBox.Icon.Question)
        quoi = "toutes les étapes du cycle" if not etapes else f"l'étape « {libelle} »"
        boite.setText(
            f"Lancer {quoi} sur {c.artist.name} ?\n\n"
            f"Portée : toute la discographie ({len(c.lignes)} titres, désactivés exclus).\n"
            "Réglages : ceux par défaut (les mêmes que la CLI)."
        )
        manquants = QCheckBox("Seulement les morceaux à donnée manquante ou en échec")
        manquants.setChecked(True)
        manquants.setToolTip(
            "Ne traiter que les morceaux auxquels il manque la donnée de CE flux, "
            "et ceux qu'une source n'a pas servis. Décoché : tous les morceaux."
        )
        boite.setCheckBox(manquants)
        boite.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if boite.exec() != QMessageBox.StandardButton.Ok:
            return
        nom = c.artist.name
        seulement = manquants.isChecked()

        def corps(hooks):
            from src.services import cycle

            options = cycle.OptionsCycle(only=etapes, tout=not seulement)
            return cycle.run(self.runtime, nom, options, hooks)

        if self.taches.lancer(cle, f"{libelle} — {nom}", corps):
            self.boutons_flux[cle].progression(0.0, f"{libelle} — démarrage…")
            self._etat_boutons()

    def _sur_tache(self, cle: str) -> None:
        t = self.taches.tache(cle)
        if t is None:
            return
        b = self.boutons_flux.get(cle)
        if t.active:
            pct = t.pourcentage
            etiquette = (
                f"{pct} % — {t.etape or t.titre}" if pct is not None else (t.etape or t.titre)
            )
            if b is not None:
                b.progression((pct or 0) / 100, etiquette)
            self.b_taches.setText(f"⏳ {t.titre}" + (f" — {pct} %" if pct is not None else ""))
            self.b_taches.setVisible(True)
        if self._centre is not None:
            self._centre._rafraichir()

    def _sur_fin_tache(self, cle: str) -> None:
        t = self.taches.tache(cle)
        b = self.boutons_flux.get(cle)
        if b is not None:
            b.au_repos()
        self.b_taches.setText(
            f"{'✔' if t.etat is Etat.COMPLET else '⚠'} {t.titre} — {t.etat.value}"
        )
        self._etat_boutons()
        # Les écritures du run sont relues : la table se rafraîchit SANS perdre
        # tri, filtre, sélection ni défilement.
        if self.chargement is not None:
            nom = self.chargement.artist.name
            en_fond(
                lambda: presentation_chargement.charger(self.runtime, nom),
                lambda c: self._sur_chargement(c, nom, garder=True),
                nom="qt:recharger-artiste",
            )
        icone = QMessageBox.Icon.Information if t.etat is Etat.COMPLET else QMessageBox.Icon.Warning
        boite = QMessageBox(
            icone, f"{t.titre} — {t.etat.value}", t.message or t.etat.value, parent=self
        )
        boite.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        boite.show()

    def _ouvrir_centre(self) -> None:
        if self._centre is None:
            self._centre = CentreTaches(self.taches, self)
        self._centre.show()
        self._centre.raise_()

    # ── préférences, fermeture ───────────────────────────────────────────────
    def _restaurer(self) -> None:
        geometrie = self.reglages.value("fenetre/geometrie")
        if geometrie is not None:
            self.restoreGeometry(geometrie)
        etat = self.reglages.value("fenetre/etat")
        if etat is not None:
            self.restoreState(etat)
        self.vue.restaurer(self.reglages)
        artiste = self.reglages.value("artiste")
        if artiste:
            QTimer.singleShot(0, lambda: self.charger(str(artiste)))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        colorer_barre_titre(self, TOKENS["cadre"], TOKENS["texte_sur_accent"])

    def closeEvent(self, event) -> None:
        if self.taches.occupe:
            r = QMessageBox.question(
                self,
                "Run en cours",
                "Un run est en cours. Fermer l'arrête à la fin du morceau en cours "
                "(jusqu'à 8 s d'attente). Fermer quand même ?",
            )
            if r != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.reglages.setValue("fenetre/geometrie", self.saveGeometry())
        self.reglages.setValue("fenetre/etat", self.saveState())
        self.vue.sauver(self.reglages)
        self.taches.fermer()
        event.accept()
