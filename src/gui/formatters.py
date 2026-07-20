"""Présentation des certifications pour la GUI.

Le modèle `Track` ne doit pas savoir que « Diamant » = 💎 : toute la mise en
forme lisible des certifications (emoji, texte, durées) vit ici.
"""

_EMOJI_MAP = {
    "Or": "🥇",
    "Double Or": "🥇🥇",
    "Triple Or": "🥇🥇🥇",
    "Platine": "💿",
    "Double Platine": "💿💿",
    "Triple Platine": "💿💿💿",
    "Diamant": "💎",
    "Double Diamant": "💎💎",
    "Triple Diamant": "💎💎💎",
    "Quadruple Diamant": "💎💎💎💎",
}


def certification_emoji(level: str | None) -> str:
    """Retourne un emoji correspondant au niveau de certification."""
    if not level:
        return ""
    return _EMOJI_MAP.get(level, "🏆")
