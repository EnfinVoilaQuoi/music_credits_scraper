"""Fonctions pures de formatage et de statut partagées par les composants GUI"""

import unicodedata
from datetime import datetime

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


def format_lyrics_cell(track) -> str:
    """Cellule « Paroles » du tableau : ✓ = texte, ⏱ = timestamps (en plus ou
    seuls), 🎹 = instrumental CONSTATÉ sur Genius (scrape réussi, pas de paroles
    par nature — e27), vide = rien ou jamais cherché."""
    has_text = bool(track.lyrics.present)
    has_sync = bool(track.lyrics.synced)
    if has_text and has_sync:
        return "✓⏱"
    if has_sync:
        return "⏱"
    if has_text:
        return "✓"
    if track.lyrics.instrumental:
        return "🎹"
    return ""


def get_track_status_icon(track, disabled_ids, ctx=None) -> str:
    """Icône de statut — ADAPTATEUR MINCE sur `src/utils/track_validation.py`.

    Les règles ont quitté le GUI le 2026-09-22 : elles sont pures, testées, et
    comptent dans le cliquet de couverture (`src/gui/*` en est exclu). La
    signature ne bouge pas, les appelants non plus ; `ctx` (facultatif) porte
    la nature des disques, sans quoi les timestamps ne sont jamais exigés.

    ✅ complet · ⚠️ incomplet · 🔒 inédit (rien n'est exigé) · ❌ désactivé.
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


def format_date(release_date):
    """Formate une date pour l'affichage en format français DD/MM/YYYY"""
    if not release_date:
        return "N/A"

    try:
        # Si c'est déjà un objet datetime
        if hasattr(release_date, "strftime"):
            return release_date.strftime("%d/%m/%Y")

        # Si c'est une chaîne
        if isinstance(release_date, str):
            # Convertir de YYYY-MM-DD vers DD/MM/YYYY
            date_str = str(release_date)[:10]  # Prendre YYYY-MM-DD
            if len(date_str) == 10 and "-" in date_str:
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    return dt.strftime("%d/%m/%Y")
                except (ValueError, TypeError):
                    pass
            # Si format ISO avec T
            if "T" in str(release_date):
                try:
                    dt = datetime.fromisoformat(
                        str(release_date).replace("Z", "+00:00").split("T")[0]
                    )
                    return dt.strftime("%d/%m/%Y")
                except (ValueError, TypeError):
                    pass
            return date_str

        return str(release_date)[:10]

    except (ValueError, TypeError, AttributeError) as e:
        logger.debug(f"Erreur formatage date '{release_date}': {e}")
        return "N/A"


def format_datetime(date_value):
    """Formate une date avec heure en format français DD/MM/YYYY à HH:MM"""
    if not date_value:
        return "N/A"

    try:
        # Si c'est déjà un objet datetime
        if hasattr(date_value, "strftime"):
            return date_value.strftime("%d/%m/%Y à %H:%M")

        # Si c'est une chaîne
        if isinstance(date_value, str):
            # Format ISO avec T (ex: 2024-10-05T14:23:45)
            if "T" in date_value:
                try:
                    dt = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
                    return dt.strftime("%d/%m/%Y à %H:%M")
                except (ValueError, TypeError):
                    pass

            # Format YYYY-MM-DD HH:MM:SS
            if len(date_value) > 10 and " " in date_value:
                try:
                    dt = datetime.strptime(date_value[:19], "%Y-%m-%d %H:%M:%S")
                    return dt.strftime("%d/%m/%Y à %H:%M")
                except (ValueError, TypeError):
                    pass

            # Format court YYYY-MM-DD (sans heure)
            if len(date_value) == 10:
                try:
                    dt = datetime.strptime(date_value, "%Y-%m-%d")
                    return dt.strftime("%d/%m/%Y")
                except (ValueError, TypeError):
                    pass

            return date_value

        return str(date_value)

    except (ValueError, TypeError, AttributeError) as e:
        logger.debug(f"Erreur formatage datetime '{date_value}': {e}")
        return "N/A"


def normalize_text(text: str) -> str:
    """Normalise le texte pour le tri (sans accents, minuscules)"""
    if not text:
        return ""
    # Supprimer les accents
    text = unicodedata.normalize("NFD", str(text))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    # Convertir en minuscules
    return text.lower()


#: Émoji par source de crédit. Table UNIQUE : la fiche morceau en avait deux
#: copies (musique et vidéo), et celle des vidéos avait perdu `deezer`,
#: `spotify_web`, `kworb` et `heritage` — les crédits venus de là s'affichaient
#: tous avec le même 🔗 « source inconnue ».
EMOJI_SOURCE = {
    "genius": "🎤",
    "spotify": "🎧",
    "spotify_web": "🎧",
    "deezer": "🎵",
    "discogs": "💿",
    "lastfm": "📻",
    "kworb": "📈",
    "heritage": "↩",
    "youtube_topic": "▶️",
    "youtube_clip": "🎬",
}


def lignes_de_credits(credits) -> list[str]:
    """Une ligne d'affichage PAR PERSONNE, et non par ligne de base.

    Deux sources qui créditent la même personne au même rôle sont un ACCORD,
    pas deux crédits : « 🎤💿 Skread » dit la même chose que deux lignes
    empilées, en le disant mieux. Mesuré le 2026-09-22 : **136 accords** de ce
    genre dormaient déjà en base et s'affichaient en double.

    Ni la base ni les sources ne perdent quoi que ce soit — c'est de
    l'affichage : `credits` garde une ligne par source, chacune avec sa
    provenance, et le reclassement des `Other` (`scripts/reclass_credit_roles`)
    peut donc en ajouter sans « fabriquer un doublon ».

    Les personnes gardent l'ordre d'arrivée ; leurs émojis sont triés pour que
    deux morceaux affichent la même paire dans le même ordre.
    """
    from src.utils.credit_normalize import identity_key

    par_personne: dict[str, dict] = {}
    for credit in credits:
        cle = identity_key(credit.name) or (credit.name or "").lower()
        entree = par_personne.setdefault(cle, {"nom": credit.name, "emojis": [], "details": []})
        emoji = EMOJI_SOURCE.get(credit.source, "🔗")
        if emoji not in entree["emojis"]:
            entree["emojis"].append(emoji)
        detail = (credit.role_detail or "").strip()
        if detail and detail not in entree["details"]:
            entree["details"].append(detail)

    lignes = []
    for entree in par_personne.values():
        detail = f" ({', '.join(entree['details'])})" if entree["details"] else ""
        lignes.append(f"{''.join(sorted(entree['emojis']))} {entree['nom']}{detail}")
    return lignes
