"""Fonctions pures de formatage et de statut partagées par les composants GUI"""
import unicodedata
from datetime import datetime

from src.utils.logger import get_logger

logger = get_logger(__name__)


def normalize_album_title(s: str) -> str:
    # Normaliseur UNIFIÉ (title_matching) : l'ancien normaliseur local
    # (apostrophes/casse) ratait "Vol.3" (Kworb) vs "Vol. 3" (Genius)
    # → stats Kworb invisibles pour La vie augmente Vol 1/2/3.
    from src.utils.title_matching import normalize_title
    return normalize_title(s) or (s or "").strip().lower()


def build_genius_slug(name: str) -> str:
    """Construit le slug Genius depuis un nom d'artiste.

    Règles :
    - Tout en minuscules
    - Supprime '.' et "'"
    - Remplace les espaces par '-'
    - Première lettre en majuscule

    Ex: 'Sofiane Pamart' → 'Sofiane-pamart'
        "L'Or du Commun" → 'Lor-du-commun'
        'NWA'            → 'Nwa'
    """
    slug = name.lower()
    for ch in (".", "'", "’"):  # point, apostrophe droite, apostrophe typographique
        slug = slug.replace(ch, "")
    slug = slug.replace(" ", "-")
    if slug:
        slug = slug[0].upper() + slug[1:]
    return slug


def format_lyrics_for_display(lyrics: str) -> str:
    """Formate les paroles pour l'affichage dans l'interface - VERSION CORRIGÉE"""
    if not lyrics:
        return "Aucunes paroles disponibles"

    lines = lyrics.split('\n')
    formatted_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            formatted_lines.append('')
            continue

        # ✅ CORRECTION: TOUTES les sections entre crochets ont le même formatage
        if line.startswith('[') and line.endswith(']'):
            # Extraire le contenu entre crochets
            section_content = line[1:-1]  # Enlever les [ ]

            # Créer la ligne décorée
            decorated_line = f"───────────────────────── [{section_content}] ─────────────────────────"

            formatted_lines.append('')
            formatted_lines.append(decorated_line)
            formatted_lines.append('')

        # Mentions d'artistes ou indentations spéciales
        elif '*' in line:
            formatted_lines.append(f"        {line}")

        # Paroles normales
        else:
            formatted_lines.append(line)

    return '\n'.join(formatted_lines)


def get_track_status_icon(track, disabled_ids) -> str:
    """Retourne l'icône de statut selon le niveau de complétude des données

    Infos nécessaires pour validation complète:
    - Date de sortie ✓
    - Crédits obtenus ✓
    - Paroles obtenues ✓
    - BPM ✓
    - Key et Mode ✓
    - Durée ✓
    - Certifications ✓ (ou validation si base à jour)

    Note: Album n'est PAS obligatoire (singles, featurings hors projet)

    Retourne:
    - ❌ : Morceau désactivé
    - ⚠️ : Données incomplètes
    - ✅ : Toutes les infos présentes
    """
    try:
        # Si le morceau est désactivé, retourner ❌
        if track.id is not None and track.id in disabled_ids:
            return "❌"

        # Liste des champs requis avec leur validation
        missing = []

        # 1. Date de sortie
        if not hasattr(track, 'release_date') or not track.release_date:
            missing.append("Date")

        # 3. Crédits obtenus
        try:
            music_credits = track.get_music_credits()
            if not music_credits or len(music_credits) == 0:
                missing.append("Crédits")
        except:
            missing.append("Crédits")

        # 4. Paroles obtenues
        if not hasattr(track, 'lyrics') or not track.lyrics or not track.lyrics.strip():
            missing.append("Paroles")

        # 5. BPM
        if not hasattr(track, 'bpm') or not track.bpm or track.bpm == 0:
            missing.append("BPM")

        # 6. Key et Mode
        has_key = hasattr(track, 'key') and track.key
        has_mode = hasattr(track, 'mode') and track.mode
        has_musical_key = hasattr(track, 'musical_key') and track.musical_key

        if not (has_musical_key or (has_key and has_mode)):
            missing.append("Key/Mode")

        # 7. Durée
        if not hasattr(track, 'duration') or not track.duration:
            missing.append("Durée")

        # 8. Certifications (validé si base à jour même sans certif)
        # On considère que si le champ 'certifications' existe (même vide), c'est que la recherche a été faite
        if not hasattr(track, 'certifications'):
            missing.append("Certifications")

        # Retourner le statut selon les données manquantes
        if len(missing) == 0:
            return "✅"  # Toutes les infos présentes
        else:
            return "⚠️"  # Données incomplètes

    except Exception as e:
        logger.error(f"Erreur dans get_track_status_icon pour {getattr(track, 'title', 'unknown')}: {e}")
        return "⚠️"  # Erreur = incomplet


def get_release_year_safely(track):
    """Récupère l'année de sortie de manière sécurisée"""
    if not track.release_date:
        return None

    # Si c'est déjà un objet datetime
    if hasattr(track.release_date, 'year'):
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
            for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%d/%m/%Y', '%m/%d/%Y', '%Y']:
                try:
                    date_obj = datetime.strptime(track.release_date, fmt)
                    return date_obj.year
                except ValueError:
                    continue

        except Exception as e:
            logger.debug(f"Erreur parsing date '{track.release_date}': {e}")

    return None


def format_date(release_date):
    """Formate une date pour l'affichage en format français DD/MM/YYYY"""
    if not release_date:
        return "N/A"

    try:
        # Si c'est déjà un objet datetime
        if hasattr(release_date, 'strftime'):
            return release_date.strftime('%d/%m/%Y')

        # Si c'est une chaîne
        if isinstance(release_date, str):
            # Convertir de YYYY-MM-DD vers DD/MM/YYYY
            date_str = str(release_date)[:10]  # Prendre YYYY-MM-DD
            if len(date_str) == 10 and '-' in date_str:
                try:
                    dt = datetime.strptime(date_str, '%Y-%m-%d')
                    return dt.strftime('%d/%m/%Y')
                except:
                    pass
            # Si format ISO avec T
            if 'T' in str(release_date):
                try:
                    dt = datetime.fromisoformat(str(release_date).replace('Z', '+00:00').split('T')[0])
                    return dt.strftime('%d/%m/%Y')
                except:
                    pass
            return date_str

        return str(release_date)[:10]

    except Exception as e:
        logger.debug(f"Erreur formatage date '{release_date}': {e}")
        return "N/A"


def format_datetime(date_value):
    """Formate une date avec heure en format français DD/MM/YYYY à HH:MM"""
    if not date_value:
        return "N/A"

    try:
        # Si c'est déjà un objet datetime
        if hasattr(date_value, 'strftime'):
            return date_value.strftime('%d/%m/%Y à %H:%M')

        # Si c'est une chaîne
        if isinstance(date_value, str):
            # Format ISO avec T (ex: 2024-10-05T14:23:45)
            if 'T' in date_value:
                try:
                    dt = datetime.fromisoformat(date_value.replace('Z', '+00:00'))
                    return dt.strftime('%d/%m/%Y à %H:%M')
                except:
                    pass

            # Format YYYY-MM-DD HH:MM:SS
            if len(date_value) > 10 and ' ' in date_value:
                try:
                    dt = datetime.strptime(date_value[:19], '%Y-%m-%d %H:%M:%S')
                    return dt.strftime('%d/%m/%Y à %H:%M')
                except:
                    pass

            # Format court YYYY-MM-DD (sans heure)
            if len(date_value) == 10:
                try:
                    dt = datetime.strptime(date_value, '%Y-%m-%d')
                    return dt.strftime('%d/%m/%Y')
                except:
                    pass

            return date_value

        return str(date_value)

    except Exception as e:
        logger.debug(f"Erreur formatage datetime '{date_value}': {e}")
        return "N/A"


def normalize_text(text: str) -> str:
    """Normalise le texte pour le tri (sans accents, minuscules)"""
    if not text:
        return ""
    # Supprimer les accents
    text = unicodedata.normalize('NFD', str(text))
    text = ''.join(char for char in text if unicodedata.category(char) != 'Mn')
    # Convertir en minuscules
    return text.lower()
