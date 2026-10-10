"""Fonctions pures de formatage et de statut partagées par les composants GUI

Les cellules, la table des émojis de source et les dates vivent dans
`src/presentation/format.py` depuis le 2026-10-11 (partagées avec la GUI PyQt6) :
elles sont ré-exportées ici, mêmes noms, même comportement.
"""

from datetime import datetime

from src.presentation.format import (  # noqa: F401 — ré-exports
    EMOJI_SOURCE,
    format_credits_cell,
    format_date,
    format_datetime,
    format_lyrics_cell,
    lignes_de_credits,
    normalize_text,
)
from src.utils.logger import get_logger
from src.utils.track_validation import Contexte, evaluer

logger = get_logger(__name__)


def normalize_album_title(s: str) -> str:
    # Normaliseur UNIFIÉ (title_matching) : l'ancien normaliseur local
    # (apostrophes/casse) ratait "Vol.3" (Kworb) vs "Vol. 3" (Genius)
    # → stats Kworb invisibles pour La vie augmente Vol 1/2/3.
    from src.utils.title_matching import normalize_title

    return normalize_title(s) or (s or "").strip().lower()


def build_genius_slug(name: str) -> str:
    """Slug Genius d'un nom d'artiste — vit dans `src/services/artiste.py`
    depuis 2026-09-14 (la CLI en a besoin). Import paresseux : le service tire
    `Runtime`, donc le pipeline, et ce module doit rester léger."""
    from src.services.artiste import build_genius_slug as _slug

    return _slug(name)


def format_lyrics_for_display(lyrics: str) -> str:
    """Formate les paroles pour l'affichage dans l'interface - VERSION CORRIGÉE"""
    if not lyrics:
        return "Aucunes paroles disponibles"

    lines = lyrics.split("\n")
    formatted_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            formatted_lines.append("")
            continue

        # ✅ CORRECTION: TOUTES les sections entre crochets ont le même formatage
        if line.startswith("[") and line.endswith("]"):
            # Extraire le contenu entre crochets
            section_content = line[1:-1]  # Enlever les [ ]

            # Créer la ligne décorée
            decorated_line = (
                f"───────────────────────── [{section_content}] ─────────────────────────"
            )

            formatted_lines.append("")
            formatted_lines.append(decorated_line)
            formatted_lines.append("")

        # Mentions d'artistes ou indentations spéciales
        elif "*" in line:
            formatted_lines.append(f"        {line}")

        # Paroles normales
        else:
            formatted_lines.append(line)

    return "\n".join(formatted_lines)


def get_track_status_icon(track, disabled_ids, ctx=None) -> str:
    """Icône de statut — ADAPTATEUR MINCE sur `src/utils/track_validation.py`.

    Les règles ont quitté le GUI le 2026-09-22 : elles sont pures, testées, et
    comptent dans le cliquet de couverture (`src/gui/*` en est exclu). La
    signature ne bouge pas, les appelants non plus ; `ctx` (facultatif) porte
    la nature des disques, sans quoi les timestamps ne sont jamais exigés.

    ✅ complet · ⚠️ incomplet · 🔒 inédit (rien n'est exigé) · 📅 à venir
    (sortie datée dans le futur) · 🕳️ page Genius sans aucune info · ❌ désactivé.
    """
    contexte = ctx or Contexte(desactives=frozenset(disabled_ids or ()))
    return evaluer(track, contexte).icone


def get_release_year_safely(track):
    """Récupère l'année de sortie de manière sécurisée"""
    if not track.release_date:
        return None

    # Si c'est déjà un objet datetime
    if hasattr(track.release_date, "year"):
        return track.release_date.year

    # Si c'est une chaîne, essayer de l'analyser
    if isinstance(track.release_date, str):
        try:
            # Format YYYY-MM-DD
            if len(track.release_date) >= 4:
                year_str = track.release_date[:4]
                if year_str.isdigit():
                    return int(year_str)

            # Essayer de parser comme datetime
            for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%Y"]:
                try:
                    date_obj = datetime.strptime(track.release_date, fmt)
                    return date_obj.year
                except ValueError:
                    continue

        except (ValueError, TypeError, AttributeError) as e:
            logger.debug(f"Erreur parsing date '{track.release_date}': {e}")

    return None


def hauteur_texte(texte: str | None, *, mini: int = 60, maxi: int = 240) -> int:
    """Hauteur (px) d'une zone de texte proportionnelle à son contenu : une ligne
    par paragraphe et par ~130 caractères, plafonnée — au-delà, la zone défile.
    L'anecdote de l'API Genius garde ses paragraphes (2026-09-24)."""
    texte = texte or ""
    lignes = texte.count(chr(10)) + 1 + len(texte) // 130
    return min(maxi, max(mini, 18 * lignes))
