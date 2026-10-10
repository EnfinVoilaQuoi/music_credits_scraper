"""Présentation des certifications pour la GUI.

Le modèle `Track` ne doit pas savoir que « Diamant » = 💎 : toute la mise en
forme lisible des certifications (emoji, texte, durées) vit dans
`src/presentation/format.py` depuis le 2026-10-11 (partagée avec la GUI PyQt6) ;
ré-exportée ici, même nom, même comportement.
"""

from src.presentation.format import certification_emoji  # noqa: F401 — ré-export
