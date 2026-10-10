"""Couleurs en TOKENS et feuille de style générée — un seul endroit.

Décision (ébauche, 2026-10-11) : les couleurs sont des tokens dès la V1, pour
que le mode clair et la couleur d'accent personnalisée (V2) ne soient qu'un
autre dictionnaire. Aucune couleur en dur ailleurs dans `src/gui_qt`.

Module sans Qt : la génération de la feuille de style est une fonction pure.
"""

from __future__ import annotations

from types import MappingProxyType

SOMBRE = MappingProxyType(
    {
        # Fonds
        "cadre": "#7450a9",  # barres de titre et pied (violet principal)
        "fond": "#2b2b2b",  # fond des fenêtres
        "carte": "#383838",  # cartes, champs, onglets
        "table": "#1d1d1d",  # fond de la table
        "table_alterne": "#222222",
        "grille": "#5a4383",
        "entete_table": "#3d2a5c",
        "survol": "#454545",
        "selection": "#4a3570",
        # Accent
        "accent": "#895ecc",
        "accent_fonce": "#442f66",
        "onglet_inactif": "#442d66",
        # Texte — contraste ≥ 4,5:1 sur « carte » (WCAG AA) pour `texte_discret`.
        "texte": "#e6e6e6",
        "texte_doux": "#cfcfcf",
        "texte_discret": "#a8a8a8",
        "texte_sur_accent": "#f2f2f2",
        # Catégories (liseré de la cellule de coche) — Prod : couleur À CHOISIR.
        "principal": "#744da9",
        "feat": "#b48ef0",
        "prod": "#4f9a94",
        "secondaire": "#bfbfbf",
        # Badges et états
        "inedit": "#c97a70",
        "instru": "#c9a227",
        "alerte": "#e0b341",
        "erreur": "#d9665b",
        "ok": "#6cc070",
        "desactive": "#6e6e6e",
        # Plateformes
        "spotify": "#1ed760",
        "youtube": "#ff3d3d",
        "genius": "#ffff64",
        # Jauge de run (ébauche) : fond accent, progression foncée.
        "jauge_fond": "#895ecc",
        "jauge_progression": "#442f66",
    }
)

#: Le thème courant. V2 : choisi dans les Paramètres (QSettings).
TOKENS = SOMBRE

POLICE = "Segoe UI"
POLICE_LEGERE = "Segoe UI Semilight"
POLICE_SEMIGRAS = "Segoe UI Semibold"


def feuille_de_style(t=TOKENS) -> str:
    """La feuille de style Qt de l'application, tirée des tokens."""
    return f"""
    QWidget {{
        background: {t['fond']};
        color: {t['texte']};
        font-family: "{POLICE}";
        font-size: 10pt;
    }}
    QToolTip {{
        background: {t['carte']};
        color: {t['texte']};
        border: 1px solid {t['accent']};
        padding: 4px;
    }}
    QMainWindow, QDialog {{ background: {t['fond']}; }}
    QFrame#carte, QWidget#carte {{
        background: {t['carte']};
        border-radius: 8px;
    }}
    QLabel {{ background: transparent; }}
    QLabel#discret {{ color: {t['texte_discret']}; }}
    QLabel#nomArtiste {{
        font-family: "{POLICE_SEMIGRAS}";
        font-size: 20pt;
        color: {t['accent']};
    }}
    QLabel#formations {{
        font-family: "{POLICE_LEGERE}";
        font-size: 16pt;
        color: {t['accent']};
    }}
    QLabel#recap {{
        font-family: "{POLICE_LEGERE}";
        font-size: 12pt;
        color: {t['texte_doux']};
    }}
    QLineEdit {{
        background: {t['carte']};
        border: none;
        border-radius: 6px;
        padding: 6px 10px;
        font-size: 12pt;
        selection-background-color: {t['accent']};
    }}
    QPushButton, QToolButton {{
        background: {t['carte']};
        color: {t['texte_doux']};
        border: none;
        border-radius: 6px;
        padding: 6px 12px;
        font-family: "{POLICE_SEMIGRAS}";
    }}
    QPushButton:hover, QToolButton:hover {{ background: {t['survol']}; }}
    QPushButton:disabled, QToolButton:disabled {{ color: {t['desactive']}; }}
    QPushButton#accent, QToolButton#accent {{
        background: {t['accent']};
        color: {t['texte_sur_accent']};
    }}
    QToolButton#bascule {{ background: {t['onglet_inactif']}; color: {t['texte_doux']}; }}
    QToolButton#bascule:checked {{ background: {t['accent']}; color: {t['texte_sur_accent']}; }}
    QTableView {{
        background: {t['table']};
        alternate-background-color: {t['table_alterne']};
        gridline-color: {t['grille']};
        border: none;
        selection-background-color: {t['selection']};
        selection-color: {t['texte']};
    }}
    QHeaderView::section {{
        background: {t['entete_table']};
        color: {t['texte_doux']};
        border: none;
        border-right: 1px solid {t['grille']};
        padding: 6px;
    }}
    QTabWidget::pane {{ border: none; }}
    QTabBar::tab {{
        background: {t['onglet_inactif']};
        color: {t['texte_doux']};
        padding: 8px 18px;
        margin-right: 2px;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        font-family: "{POLICE_SEMIGRAS}";
    }}
    QTabBar::tab:selected {{ background: {t['cadre']}; color: {t['texte_sur_accent']}; }}
    QLabel#bandeau {{
        background: {t['cadre']};
        color: {t['texte_sur_accent']};
        padding: 8px 12px;
        font-size: 12pt;
    }}
    QWidget#pied {{ background: {t['cadre']}; }}
    QWidget#pied QLabel, QWidget#pied QToolButton {{
        background: transparent;
        color: {t['texte_sur_accent']};
    }}
    QWidget#pied QToolButton:checked {{ background: {t['accent_fonce']}; }}
    QDockWidget {{ color: {t['texte']}; }}
    QDockWidget::title {{ background: {t['cadre']}; padding: 6px; }}
    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; }}
    QScrollBar::handle:vertical {{ background: {t['texte_discret']}; border-radius: 4px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QMenu {{ background: {t['carte']}; border: 1px solid {t['accent']}; }}
    QMenu::item:selected {{ background: {t['selection']}; }}
    QProgressBar {{
        background: {t['jauge_fond']};
        border: none;
        border-radius: 6px;
        color: {t['texte_sur_accent']};
        text-align: center;
    }}
    QProgressBar::chunk {{ background: {t['jauge_progression']}; border-radius: 6px; }}
    """


def couleur_categorie(participation: str, t=TOKENS) -> str:
    """Couleur du liseré d'une catégorie (`Participation`, valeur texte)."""
    return t.get(str(participation), t["principal"])
