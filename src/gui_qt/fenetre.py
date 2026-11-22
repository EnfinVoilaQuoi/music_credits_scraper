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
    QActionGroup,
    QDesktopServices,
    QKeySequence,
    QShortcut,
    QStandardItem,
    QStandardItemModel,
)
from PyQt6.QtWidgets import (
    QCompleter,
    QDockWidget,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from src.gui_qt import icones
from src.gui_qt.artistes import FenetreArtistes
from src.gui_qt.centre_taches import CentreTaches, EnAttente
from src.gui_qt.dialogues import DialogueAjout
from src.gui_qt.fenetres_windows import colorer_barre_titre
from src.gui_qt.fiche import FenetreFiche, VueFiche
from src.gui_qt.formations import FenetreFormations
from src.gui_qt.modele import ModeleMorceaux, ProxyMorceaux
from src.gui_qt.pont import Etat, Taches, en_fond
from src.gui_qt.theme import TOKENS
from src.gui_qt.vue_albums import VueAlbums
from src.gui_qt.vue_table import VueMorceaux, colonne_triee_par_defaut
from src.gui_qt.widgets import BoutonFlux, BoutonPastille, CartoucheArtiste, PiedTable
from src.presentation import albums as presentation_albums
from src.presentation import chargement as presentation_chargement
from src.presentation import formations as presentation_formations
from src.presentation import pied as presentation_pied
from src.presentation import runs
from src.presentation.artistes import ResumeArtiste, suggestions
from src.presentation.filtres import Filtre
from src.presentation.pied import Portee
from src.presentation.runs import PorteeFlux
from src.utils.logger import get_logger

logger = get_logger(__name__)

ROLE_NOM = Qt.ItemDataRole.UserRole + 10

#: (clé, libellé, icône). Ordre de la barre des flux (maquettes du 2026-11-21) :
#: Run Complet d'abord ; plus de bouton Certifications — le run certifs de
#: l'artiste passe par Run Complet, la fenêtre des certifs par la barre du haut.
CYCLE = ("cycle", "Run Complet", "player-track-next")
FLUX = (
    ("disco", "Discographie", "playlist-add"),
    ("identite", "Identité", "id"),
    ("credits", "Crédits & Paroles", "list-details"),
    ("enrich", "Données Tech.", "ruler-measure"),
    ("streams", "Streams", "chart-bar-popular"),
)

#: Cases du filtre : (attribut de `Filtre`, libellé, inversée). None = séparateur.
#: « Titres uniques » est l'INVERSE de `version_alt` : cochée, elle RETIRE les
#: versions dérivées (décision utilisateur 2026-11-21).
_CASES_FILTRE = (
    ("principal", "Titres principaux", False),
    ("feat", "Invités", False),
    ("secondaire", "Rôles secondaires", False),
    ("prod", "Productions", False),
    None,
    ("inedit", "Inédit", False),
    ("instrumental", "Instrumental", False),
    ("version_alt", "Titres uniques", True),
    ("desactives", "Désactivés", False),
)

#: Cases du menu d'un bouton de flux : (attribut de `PorteeFlux`, libellé,
#: infobulle). None = séparateur.
_CASES_PORTEE = (
    (
        "selection",
        "Titres sélectionnés",
        "Seulement les titres cochés dans la table (Identité, Crédits, Données Tech., Streams).",
    ),
    (
        "desactives",
        "Titres désactivés",
        "Inclure les titres désactivés — à venir : les services les écartent encore toujours.",
    ),
    None,
    ("manquants", "Manquants", "Les morceaux auxquels il manque la donnée de ce flux."),
    ("echecs", "Échecs", "Les morceaux qu'une source n'a pas servis au dernier passage."),
    ("tout", "Tout", "Tous les morceaux, sans forcer les valeurs déjà là."),
    None,
    ("forcer", "Forcer", "Redemander ce qui est déjà là (Identité, Crédits, Données Tech.)."),
)


def _cle_de_nom(nom: str) -> str:
    """Un nom d'artiste en clé QSettings : « / » et l'antislash y sont des séparateurs
    de groupe (« AC/DC »)."""
    return nom.replace("%", "%25").replace("/", "%2F").replace("\\", "%5C")


def _nom_de_cle(cle: str) -> str:
    return cle.replace("%5C", "\\").replace("%2F", "/").replace("%25", "%")


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
        #: Runs demandés pendant qu'un autre tournait : un seul écrivain à la
        #: fois, ils partent l'un après l'autre (liste de tâches).
        self.file_attente: list[EnAttente] = []
        self._fenetres_fiche: list[FenetreFiche] = []
        self.verification = None  # nouveautés de l'artiste chargé
        self.setWindowTitle("théRAPie")
        self.resize(1500, 900)

        self.modele = ModeleMorceaux(self)
        self.proxy = ProxyMorceaux(self)
        self.proxy.setSourceModel(self.modele)
        self.vue = VueMorceaux(self.modele, self.proxy)
        self.vue_albums = VueAlbums()
        #: Les projets de l'artiste chargé (`get_projets_for_artist`), lus à la
        #: première bascule en vue Album, relus après un run.
        self.projets: list[dict] | None = None
        self.pile = QStackedWidget()
        self.pile.addWidget(self.vue)
        self.pile.addWidget(self.vue_albums)
        self.cartouche = CartoucheArtiste()
        self.pied = PiedTable()
        self.fiche = VueFiche(contexte_versions=self._contexte_versions)

        central = QWidget()
        couche = QVBoxLayout(central)
        couche.setContentsMargins(6, 6, 6, 0)
        couche.setSpacing(6)
        couche.addLayout(self._barre_artiste())
        couche.addWidget(self.cartouche)
        couche.addLayout(self._barre_flux())
        couche.addWidget(self.pile, 1)
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
        # À droite de la recherche (maquettes du 2026-11-21). Une pastille « ! »
        # dit qu'un cas attend ; les fenêtres pas encore portées sont grisées.
        self.b_formations = BoutonPastille(
            "users-group", "Alias • Formations — la pastille dit qu'un lien attend une décision"
        )
        self.b_certifs = BoutonPastille(
            "award", "Certifications — fenêtre à venir (version CustomTkinter)"
        )
        self.b_media = BoutonPastille("photo", "Media — fenêtre à venir (version CustomTkinter)")
        self.b_decisions = BoutonPastille(
            "alert-triangle", "Décisions • Erreurs — panneau à venir (version CustomTkinter)"
        )
        self.b_sante = BoutonPastille(
            "activity", "Santé des sources — la pastille dit qu'une source est cassée"
        )
        self.b_file = BoutonPastille(
            "list-check", "Liste de tâches : runs en cours, planifiés, en attente"
        )
        self.b_menu = BoutonPastille("settings", "Menu")
        self.b_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.b_menu.setMenu(self._menu_general())
        for b in (self.b_certifs, self.b_media, self.b_decisions):
            b.setEnabled(False)
        for w in (self.b_ajout, self.b_base):
            h.addWidget(w)
        h.addWidget(self.recherche, 1)
        for w in (self.b_formations, self.b_certifs, self.b_media, self.b_decisions):
            h.addWidget(w)
        h.addSpacing(48)
        for w in (self.b_sante, self.b_file, self.b_menu):
            h.addWidget(w)
        return h

    def _menu_general(self) -> QMenu:
        """Le Menu (roue) : deux colonnes, comme la maquette. Rien n'y est encore
        branché ; « Mise à Jour » sera la mise à jour de l'APPLICATION."""
        menu = QMenu(self)
        grille = QWidget()
        g = QGridLayout(grille)
        g.setContentsMargins(8, 8, 8, 8)
        entrees = (
            ("Thème", "Thème — à venir"),
            ("Emplacement", "Dossier d'écriture de l'application installée — à venir"),
            ("Debug", "Montre ou cache des options de maintenance — à venir"),
            ("Sources", "Les sources de chaque run — à venir"),
            ("Mise à Jour", "Mise à jour de l'application — à venir"),
            ("Paramètres", "Délais de relecture, réglages — à venir"),
        )
        self.entrees_menu: dict[str, QToolButton] = {}
        for i, (libelle, infobulle) in enumerate(entrees):
            b = QToolButton()
            b.setText(libelle)
            b.setToolTip(infobulle)
            b.setEnabled(False)
            b.setAutoRaise(True)
            self.entrees_menu[libelle] = b
            g.addWidget(b, i // 2, i % 2)
        action = QWidgetAction(menu)
        action.setDefaultWidget(grille)
        menu.addAction(action)
        return menu

    def _barre_flux(self) -> QHBoxLayout:
        h = QHBoxLayout()
        self.boutons_flux: dict[str, BoutonFlux] = {}
        self.portees: dict[str, PorteeFlux] = {}
        self._actions_portee: dict[str, dict[str, QAction]] = {}
        cle, libelle, icone = CYCLE
        b = BoutonFlux(libelle, icone, sur_accent=True)
        b.avec_menu(self._menu_complet())
        b.clicked.connect(self._sur_cycle)
        self.boutons_flux[cle] = b
        h.addWidget(b)
        h.addSpacing(12)
        for cle, libelle, icone in FLUX:
            b = BoutonFlux(libelle, icone)
            self.portees[cle] = self._portee_memorisee(cle)
            b.avec_menu(self._menu_portee(cle))
            b.clicked.connect(lambda _=False, c=cle, l_=libelle: self._sur_flux(c, l_))
            self.boutons_flux[cle] = b
            self._infobulle_flux(cle)
            h.addWidget(b)
        self.boutons_flux["cycle"].setObjectName("accent")
        h.addSpacing(12)
        self.b_export = QPushButton(icones.icone("file-export", TOKENS["accent"], 22), "  Export")
        self.b_export.setMinimumHeight(44)
        self.b_export.setEnabled(False)
        self.b_export.setToolTip("Export — fenêtre à venir (version CustomTkinter)")
        h.addWidget(self.b_export)
        h.addStretch()
        self.recherche_table = QLineEdit()
        self.recherche_table.setPlaceholderText("Rechercher un titre (Ctrl+F)")
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
            b.setMinimumHeight(44)
            h.addWidget(b)
        self.b_titre.setChecked(True)
        self.b_titre.setToolTip("Vue Titre : un morceau par ligne")
        self.b_album.setToolTip("Vue Album : un projet par ligne, ses éditions dépliables")
        self.b_titre.clicked.connect(lambda: self._basculer_vue(album=False))
        self.b_album.clicked.connect(lambda: self._basculer_vue(album=True))
        return h

    # ── menus des runs ───────────────────────────────────────────────────────
    def _portee_memorisee(self, cle: str) -> PorteeFlux:
        valeurs = {
            k: self.reglages.value(f"runs/{cle}/{k}")
            for k in PorteeFlux().en_texte()
            if self.reglages.value(f"runs/{cle}/{k}") is not None
        }
        return PorteeFlux.depuis_texte(valeurs)

    def _memoriser_portee(self, cle: str) -> None:
        for k, v in self.portees[cle].en_texte().items():
            self.reglages.setValue(f"runs/{cle}/{k}", v)

    def _menu_portee(self, cle: str) -> QMenu:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        actions: dict[str, QAction] = {}
        if cle == "disco":
            # La discographie relit tout l'artiste : pas de portée par titre.
            info = menu.addAction("Toute la discographie de l'artiste")
            info.setEnabled(False)
            self.a_nouveautes = menu.addAction("Voir les nouveaux titres", self._montrer_nouveautes)
            self.a_nouveautes.setEnabled(False)
            self._actions_portee[cle] = actions
            return menu
        for case in _CASES_PORTEE:
            if case is None:
                menu.addSeparator()
                continue
            attribut, libelle, infobulle = case
            if attribut == "selection" and cle not in runs.ETAPES_PAR_FICHES:
                continue
            if attribut == "forcer" and cle not in runs.ETAPES_FORCABLES:
                continue
            if attribut in ("manquants", "echecs", "tout") and cle not in runs.ETAPES_RESTREINTES:
                continue
            a = QAction(libelle, menu)
            a.setCheckable(True)
            a.setToolTip(infobulle)
            a.setEnabled(attribut not in runs.OPTIONS_A_VENIR)
            a.toggled.connect(lambda oui, c=cle, k=attribut: self._sur_case_portee(c, k, oui))
            actions[attribut] = a
            menu.addAction(a)
        self._actions_portee[cle] = actions
        self._cocher_portee(cle)
        return menu

    def _cocher_portee(self, cle: str) -> None:
        valeurs = self.portees[cle].en_texte()
        for k, a in self._actions_portee[cle].items():
            a.blockSignals(True)
            a.setChecked(bool(valeurs[k]) and a.isEnabled())
            a.blockSignals(False)

    def _sur_case_portee(self, cle: str, attribut: str, oui: bool) -> None:
        self.portees[cle] = self.portees[cle].avec(**{attribut: oui})
        self._cocher_portee(cle)
        self._memoriser_portee(cle)
        self._infobulle_flux(cle)

    def _infobulle_flux(self, cle: str) -> None:
        libelle = next(lib for c, lib, _ in FLUX if c == cle)
        portee = runs.resume_portee(cle, self.portees[cle], len(self.modele.selection))
        self.boutons_flux[cle].setToolTip(
            f"Lancer « {libelle} » : {portee}.\nLa flèche choisit les titres concernés."
        )

    def _menu_complet(self) -> QMenu:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        groupe = QActionGroup(menu)
        groupe.setExclusive(True)
        memorise = self.reglages.value("runs/cycle/mode", runs.ModeComplet.MANQUANTS_ECHECS.value)
        try:
            self.mode_complet = runs.ModeComplet(memorise)
        except ValueError:
            self.mode_complet = runs.ModeComplet.MANQUANTS_ECHECS
        if self.mode_complet in runs.MODES_A_VENIR:
            self.mode_complet = runs.ModeComplet.MANQUANTS_ECHECS
        infobulles = {
            runs.ModeComplet.MANQUANTS_ECHECS: "Le défaut : ce qui manque et ce qui a échoué.",
            runs.ModeComplet.ECHECS: "Seulement ce qu'une source n'a pas servi.",
            runs.ModeComplet.IGNORER_DELAIS: (
                "Redemander les « pas sur Spotify / Deezer / Apple » et les absences "
                "récentes avant leur délai — à venir."
            ),
            runs.ModeComplet.TOUT: "Tous les morceaux, sans forcer les valeurs déjà là.",
        }
        self._actions_complet: dict[runs.ModeComplet, QAction] = {}
        for mode, libelle in runs.LIBELLES_COMPLET.items():
            a = QAction(libelle, menu)
            a.setCheckable(True)
            a.setToolTip(infobulles[mode])
            a.setEnabled(mode not in runs.MODES_A_VENIR)
            a.setChecked(mode is self.mode_complet)
            a.triggered.connect(lambda _=False, m=mode: self._sur_mode_complet(m))
            groupe.addAction(a)
            menu.addAction(a)
            self._actions_complet[mode] = a
        return menu

    def _sur_mode_complet(self, mode: runs.ModeComplet) -> None:
        self.mode_complet = mode
        self.reglages.setValue("runs/cycle/mode", mode.value)

    def _menu_filtre(self) -> QMenu:
        menu = QMenu(self)
        self._actions_filtre: dict[str, QAction] = {}
        self._cases_inversees: set[str] = set()
        for case in _CASES_FILTRE:
            if case is None:
                menu.addSeparator()
                continue
            attribut, libelle, inversee = case
            a = QAction(libelle, menu)
            a.setCheckable(True)
            a.setChecked(not inversee)
            a.toggled.connect(self._appliquer_filtre)
            self._actions_filtre[attribut] = a
            if inversee:
                self._cases_inversees.add(attribut)
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
        self.b_file.clicked.connect(self._ouvrir_centre)
        self.b_formations.clicked.connect(self._ouvrir_formations)
        self.b_sante.clicked.connect(self._ouvrir_sante)
        self.cartouche.formations_cliquees.connect(self._ouvrir_formations)
        self.recherche.textEdited.connect(self._completer)
        self.recherche.returnPressed.connect(lambda: self.charger(self.recherche.text()))
        self.completion.activated[str].connect(self.charger)
        self.recherche_table.textChanged.connect(lambda _: self._appliquer_filtre())
        QShortcut(QKeySequence.StandardKey.Find, self, activated=self._focus_recherche_table)
        self.modele.selection_changee.connect(self._rafraichir_pied)
        self.modele.selection_changee.connect(self._rafraichir_infobulles_flux)
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
        self.vue_albums.menu_album.connect(self._menu_album)

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
            self._verifier_nouveautes()
        self.projets = None
        self._compter_formations()
        if self.en_vue_album:
            self._charger_albums()
        self.cartouche.afficher(c.cartouche, getattr(c.artist, "image_path", None))
        self.setWindowTitle(f"théRAPie — {c.artist.name}")
        self.reglages.setValue("artiste", c.artist.name)
        global_ = presentation_pied.calculer(c.lignes, c.lignes, Portee.GLOBAL)
        self.reglages.setValue(
            f"pourcentages/{_cle_de_nom(c.artist.name)}", global_.texte_pourcentage
        )
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
        d = FenetreArtistes(
            self.runtime, self.artistes, self._pourcentages(), self._sauvegarder_base, self
        )
        d.ouvrir.connect(self.charger)
        d.ajouter.connect(lambda: QTimer.singleShot(0, self._ouvrir_ajout))
        d.supprime.connect(self._sur_artiste_supprime)
        d.exec()

    def _pourcentages(self) -> dict[str, str]:
        """Le dernier pourcentage connu de chaque artiste (noté au chargement)."""
        self.reglages.beginGroup("pourcentages")
        try:
            return {
                _nom_de_cle(cle): str(self.reglages.value(cle)) for cle in self.reglages.childKeys()
            }
        finally:
            self.reglages.endGroup()

    @staticmethod
    def _sauvegarder_base(nom: str):
        """Sauvegarde de la base AVANT une suppression (règle des données)."""
        from src.utils.database_backup import get_backup_manager

        return get_backup_manager().create_backup(f"suppression_artiste_{nom}")

    def _sur_artiste_supprime(self, nom: str) -> None:
        self.reglages.remove(f"pourcentages/{_cle_de_nom(nom)}")
        if self.chargement is not None and self.chargement.artist.name == nom:
            self.chargement = None
            self.modele.charger([])
            self.reglages.remove("artiste")
            self._afficher_vide()
        self._charger_liste_artistes()

    # ── filtre, pied ─────────────────────────────────────────────────────────
    def _focus_recherche_table(self) -> None:
        self.recherche_table.setFocus()
        self.recherche_table.selectAll()

    def _appliquer_filtre(self, *_) -> None:
        cases = {
            k: a.isChecked() != (k in self._cases_inversees)
            for k, a in self._actions_filtre.items()
        }
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
        self.pied.bouton_portee.setVisible(not self.en_vue_album)
        if self.en_vue_album:
            # Le pied de la vue Album suivra ses colonnes (à définir) ; pour
            # l'instant il dit ce qui est montré.
            self.pied.afficher(None, presentation_albums.resume(self.vue_albums.albums))
            return
        visibles = self.proxy.lignes_visibles()
        p = presentation_pied.calculer(self.modele.lignes, visibles, self.pied.portee)
        self.pied.afficher(p, presentation_pied.texte_selection(self.modele.selection, visibles))

    # ── formations ───────────────────────────────────────────────────────────
    def _compter_formations(self) -> None:
        c = self.chargement
        if c is None:
            self.b_formations.poser_pastille(None)
            return
        artist_id = c.artist.id

        def compter():
            from src.utils.formations import candidats_de_base

            relations = self.runtime.data_manager.get_artist_relations(artist_id, status=None)
            return presentation_formations.a_decider(candidats_de_base(relations))

        def poser(n) -> None:
            if self.chargement is not None and self.chargement.artist.id == artist_id:
                self.b_formations.poser_pastille("!" if n else None)

        en_fond(compter, poser, lambda m: logger.debug(f"Formations non comptées : {m}"))

    def _ouvrir_formations(self) -> None:
        c = self.chargement
        if c is None:
            return
        d = FenetreFormations(self.runtime, c.artist, self)
        nom = c.artist.name
        d.modifie.connect(lambda: self._recharger(nom))
        self.b_formations.setObjectName("actif")
        self.b_formations.style().polish(self.b_formations)
        d.exec()
        self.b_formations.setObjectName("")
        self.b_formations.style().polish(self.b_formations)

    def _recharger(self, nom: str) -> None:
        en_fond(
            lambda: presentation_chargement.charger(self.runtime, nom),
            lambda c: self._sur_chargement(c, nom, garder=True),
            nom="qt:recharger-artiste",
        )

    # ── santé des sources ────────────────────────────────────────────────────
    def _ouvrir_sante(self) -> None:
        from src.gui_qt.sante import FenetreSante
        from src.observability.repository import SourceUsageRepository
        from src.utils.source_health import SOURCES, check_all, load_health, save_health

        def sonder(niveau: str) -> dict:
            save_health(check_all(level=niveau))
            return load_health()

        engine = self.runtime.data_manager.engine
        d = FenetreSante(SourceUsageRepository(engine), SOURCES, load_health, sonder, self)
        d.sondee.connect(lambda n: self.b_sante.poser_pastille("!" if n else None))
        d.show()

    # ── vue Album ────────────────────────────────────────────────────────────
    @property
    def en_vue_album(self) -> bool:
        return self.pile.currentWidget() is self.vue_albums

    def _basculer_vue(self, *, album: bool) -> None:
        self.b_titre.setChecked(not album)
        self.b_album.setChecked(album)
        self.pile.setCurrentWidget(self.vue_albums if album else self.vue)
        self.reglages.setValue("vue", "album" if album else "titre")
        if album and self.chargement is not None:
            self._charger_albums()
        self._rafraichir_pied()

    def _charger_albums(self) -> None:
        c = self.chargement
        if c is None:
            return
        if self.projets is not None:
            self._afficher_albums()
            return
        artist_id = c.artist.id

        def lus(projets) -> None:
            if self.chargement is None or self.chargement.artist.id != artist_id:
                return
            self.projets = projets
            self._afficher_albums()

        en_fond(
            lambda: self.runtime.data_manager.get_projets_for_artist(artist_id),
            lus,
            lambda message: QMessageBox.warning(self, "Vue Album", message),
            nom="qt:projets",
        )

    def _afficher_albums(self) -> None:
        self.vue_albums.charger(
            presentation_albums.construire(self.projets or [], self.modele.lignes)
        )
        self._rafraichir_pied()

    def _menu_album(self, album, position) -> None:
        menu = QMenu(self)
        menu.addAction(
            "Voir ses titres",
            lambda: (
                self._basculer_vue(album=False),
                self.recherche_table.setText(
                    album.titre if album.rang == presentation_albums.RANG_PROJET else ""
                ),
            ),
        )
        menu.exec(position)

    # ── fiche ────────────────────────────────────────────────────────────────
    def _sur_ligne_courante(self, ligne) -> None:
        if self.dock.isVisible():
            self.fiche.afficher(ligne)

    def _montrer_fiche(self, ligne) -> None:
        self.dock.show()
        self.dock.raise_()
        self.fiche.afficher(ligne)

    def _fiche_detachee(self, ligne) -> None:
        f = FenetreFiche(ligne, self, self._contexte_versions)
        self._fenetres_fiche.append(f)
        f.finished.connect(lambda _: self._fenetres_fiche.remove(f))
        f.show()

    def _contexte_versions(self, track) -> tuple[list, list]:
        """Les éditions de la fiche (base) et les fiches chargées, pour
        l'onglet Versions. Une requête courte : lue sur le fil de l'interface."""
        editions = self.runtime.data_manager.get_track_editions(track.id) if track.id else []
        return editions, [ligne.track for ligne in self.modele.lignes]

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
        # Occupé, un bouton reste actif : son run va dans la file d'attente.
        for b in self.boutons_flux.values():
            b.setEnabled(charge or (occupe and b.en_cours))
        self.b_formations.setEnabled(charge)

    def _rafraichir_infobulles_flux(self, *_) -> None:
        for cle in self.portees:
            self._infobulle_flux(cle)

    def _confirmer(self, titre: str, texte: str) -> bool:
        """Jamais en silence : chaque run dit sa portée avant de partir."""
        r = QMessageBox.question(self, titre, texte)
        return r == QMessageBox.StandardButton.Yes

    def _sur_flux(self, cle: str, libelle: str) -> None:
        if self.boutons_flux[cle].en_cours:
            self._ouvrir_centre()
            return
        c = self.chargement
        if c is None:
            return
        portee = self.portees[cle]
        try:
            options = runs.options_flux(cle, portee, self.modele.selection)
        except runs.RunImpossible as e:
            QMessageBox.information(self, libelle, str(e))
            return
        resume = runs.resume_portee(cle, portee, len(self.modele.selection))
        self._demander(cle, libelle, options, f"« {libelle} » sur {c.artist.name}", resume)

    def _sur_cycle(self) -> None:
        cle, libelle, _ = CYCLE
        if self.boutons_flux[cle].en_cours:
            self._ouvrir_centre()
            return
        c = self.chargement
        if c is None:
            return
        try:
            options = runs.options_complet(self.mode_complet)
        except runs.RunImpossible as e:
            QMessageBox.information(self, libelle, str(e))
            return
        mode = runs.LIBELLES_COMPLET[self.mode_complet]
        self._demander(
            cle,
            libelle,
            options,
            f"toutes les étapes sur {c.artist.name}",
            "Discographie, identité, crédits, données tech., streams, certifications — "
            f"{mode.lower()}",
        )

    def _demander(self, cle: str, libelle: str, options, quoi: str, portee: str) -> None:
        """Lance le run, ou le met en ATTENTE si un autre tourne — toujours
        après une confirmation qui dit sa portée."""
        nom = self.chargement.artist.name
        if self.taches.occupe:
            if self._confirmer(
                libelle,
                f"Un run est en cours. Mettre en attente {quoi} ?\n\n{portee}.\n\n"
                "Il partira à la fin des runs en cours (liste de tâches).",
            ):
                self.file_attente.append(EnAttente(cle, libelle, nom, options))
                self._rafraichir_file()
            return
        if self._confirmer(libelle, f"Lancer {quoi} ?\n\n{portee}."):
            self._lancer(cle, libelle, options, nom)

    def _rafraichir_file(self) -> None:
        self.b_file.poser_pastille(len(self.taches.en_cours()) + len(self.file_attente) or None)
        if self._centre is not None:
            self._centre._rafraichir()

    def _retirer_de_la_file(self, i: int) -> None:
        if 0 <= i < len(self.file_attente):
            del self.file_attente[i]
        self._rafraichir_file()

    def _lancer(self, cle: str, libelle: str, options, nom: str) -> None:

        def corps(hooks):
            from src.services import cycle

            return cycle.run(self.runtime, nom, options, hooks)

        if self.taches.lancer(cle, f"{libelle} — {nom}", corps):
            self.boutons_flux[cle].progression(0.0, "0 %")
            self.boutons_flux[cle].setToolTip(f"{libelle} — démarrage…")
            self._etat_boutons()
            self._rafraichir_file()

    def _sur_tache(self, cle: str) -> None:
        t = self.taches.tache(cle)
        if t is None:
            return
        b = self.boutons_flux.get(cle)
        if t.active and b is not None:
            pct = t.pourcentage
            b.progression((pct or 0) / 100, f"{pct} %" if pct is not None else "…")
            b.setToolTip(" — ".join(x for x in (t.titre, t.etape, t.libelle) if x))
        if self._centre is not None:
            self._centre._rafraichir()

    def _sur_fin_tache(self, cle: str) -> None:
        t = self.taches.tache(cle)
        b = self.boutons_flux.get(cle)
        if b is not None:
            b.au_repos()
            if cle in self.portees:
                self._infobulle_flux(cle)
            else:
                b.setToolTip("Lancer toutes les étapes. La flèche choisit ce qui est relu.")
        self._etat_boutons()
        if self.file_attente and not self.taches.occupe:
            e = self.file_attente.pop(0)
            self._lancer(e.cle, e.libelle, e.options, e.artiste)
        self._rafraichir_file()
        # Les écritures du run sont relues : la table se rafraîchit SANS perdre
        # tri, filtre, sélection ni défilement.
        if self.chargement is not None:
            nom = self.chargement.artist.name
            en_fond(
                lambda: presentation_chargement.charger(self.runtime, nom),
                lambda c: self._sur_chargement(c, nom, garder=True),
                nom="qt:recharger-artiste",
            )
            if cle in ("disco", "cycle"):
                self._verifier_nouveautes(force=True)
        icone = QMessageBox.Icon.Information if t.etat is Etat.COMPLET else QMessageBox.Icon.Warning
        boite = QMessageBox(
            icone, f"{t.titre} — {t.etat.value}", t.message or t.etat.value, parent=self
        )
        boite.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        boite.show()

    # ── nouveautés (pastille de Discographie) ────────────────────────────────
    def _verifier_nouveautes(self, *, force: bool = False) -> None:
        """Les titres Genius absents de la base (`services.nouveautes`, cache
        d'un jour). La pastille de Discographie en porte le nombre."""
        c = self.chargement
        self.verification = None
        self.boutons_flux["disco"].poser_pastille(None)
        self.a_nouveautes.setEnabled(False)
        if c is None:
            return
        artist = c.artist

        def travail():
            from src.services import nouveautes

            return nouveautes.verifier(self.runtime, artist, force=force)

        en_fond(
            travail,
            lambda v: self._sur_nouveautes(artist, v),
            lambda message: logger.debug(f"Nouveautés non vérifiées : {message}"),
            nom="qt:nouveautes",
        )

    def _sur_nouveautes(self, artist, verification) -> None:
        if self.chargement is None or self.chargement.artist.id != artist.id:
            return
        self.verification = verification
        n = len(verification.nouveautes)
        self.boutons_flux["disco"].poser_pastille(n or None)
        self.a_nouveautes.setEnabled(bool(n))

    def _montrer_nouveautes(self) -> None:
        from src.services import nouveautes

        v = self.verification
        if v is None or self.chargement is None:
            return
        QMessageBox.information(
            self, "Nouveaux titres", nouveautes.resume(v, self.chargement.artist.name)
        )

    def _ouvrir_centre(self) -> None:
        if self._centre is None:
            self._centre = CentreTaches(self.taches, self, self.file_attente)
            self._centre.retirer.connect(self._retirer_de_la_file)
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
        self.vue_albums.restaurer(self.reglages)
        if self.reglages.value("vue") == "album":
            self._basculer_vue(album=True)
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
        self.vue_albums.sauver(self.reglages)
        self.taches.fermer()
        event.accept()
