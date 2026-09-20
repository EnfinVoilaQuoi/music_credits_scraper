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


def _streams_complets(track) -> bool:
    """Le morceau a-t-il les streams qu'on peut légitimement lui réclamer ?

    La règle est ASYMÉTRIQUE, parce que les deux absences ne se valent pas.

    **YouTube : toujours exigé.** Un lien manquant ne prouve rien — le catalogue
    de YouTube est plus large que celui des plateformes de streaming (rips de
    titres supprimés, versions physiques, inédits, lyrics vidéos de projets
    jamais sortis). L'absence n'y est pas observable, elle ne peut donc jamais
    servir d'excuse.

    **Spotify : exigé seulement si l'absence est CONSTATÉE.** Un `spotify_id`
    vide ne suffit pas — il dit aussi bien « pas sur Spotify » que « jamais
    cherché ». Deux signaux tranchent :

      · `spotify_id_checked_at` (e17) : une résolution a été menée à terme. Sans
        cette date, on ne valide pas — affirmer une absence qu'on n'a pas
        constatée serait pire que de laisser un triangle.
      · l'ISRC : un enregistrement distribué en a forcément un, donc un ISRC
        sans identifiant Spotify trahit une résolution ratée, pas une absence.
        L'inverse ne vaut PAS — 292 morceaux ont un ID Spotify sans ISRC en base
        (le nôtre vient de Deezer), donc son absence ne prouve rien.
    """
    if not track.streams.ytm_streams:
        return False
    if track.spotify_id:
        return bool(track.streams.spotify_streams)
    if track.isrc:
        # Un ISRC signe un enregistrement DISTRIBUÉ : il est donc presque
        # sûrement sur Spotify, et ne pas avoir son identifiant est un échec de
        # RÉSOLUTION, pas une absence. Mesuré le 2026-09-05 : 67 morceaux dans
        # ce cas. (L'inverse ne vaut pas : 292 morceaux ont un ID Spotify SANS
        # ISRC en base — le nôtre vient de Deezer, son absence ne prouve rien.)
        return False
    # Ni identifiant ni ISRC : le morceau est-il absent, ou jamais cherché ?
    return bool(track.spotify_id_checked_at)


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


def get_track_status_icon(track, disabled_ids) -> str:
    """Retourne l'icône de statut selon le niveau de complétude des données

    Infos nécessaires pour validation complète:
    - Date de sortie ✓
    - Crédits obtenus ✓
    - Paroles obtenues ✓
    - BPM ✓
    - Key et Mode ✓
    - Durée ✓
    - Streams ✓ : YouTube toujours, Spotify seulement si le morceau y est
      (cf. `_streams_complets`) — un titre publié seulement sur YouTube est donc
      complet avec ses seuls streams YTM
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
        if not track.release_date:
            missing.append("Date")

        # 3. Crédits obtenus
        try:
            music_credits = track.get_music_credits()
            if not music_credits or len(music_credits) == 0:
                missing.append("Crédits")
        except (AttributeError, TypeError, KeyError):
            missing.append("Crédits")

        # 4. Paroles obtenues — ou instrumental constaté sur Genius (e27) :
        # pas de paroles PAR NATURE, il n'y a rien à réclamer.
        if track.lyrics.a_chercher():
            missing.append("Paroles")

        # 5. BPM
        if not track.audio.bpm or track.audio.bpm == 0:
            missing.append("BPM")

        # 6. Key et Mode. Le commentaire d'origine parlait d'« attributs
        # dynamiques du mapper → hasattr requis » : ce n'est plus vrai depuis la
        # Phase 5 (`key`/`mode` sont de VRAIS champs de `TrackAudio`), et le code
        # ne fait déjà plus de hasattr.
        has_key = track.audio.key
        has_mode = track.audio.mode
        has_musical_key = track.audio.musical_key

        if not (has_musical_key or (has_key and has_mode)):
            missing.append("Key/Mode")

        # 7. Durée
        if not track.duration:
            missing.append("Durée")

        # 9. Streams — depuis que le scrape Spotify est en place (2026-09-05),
        # l'absence de compteur est un vrai trou et non plus une fatalité.
        # Exigés PLATEFORME PAR PLATEFORME : un morceau qui n'existe pas sur
        # Spotify n'y aura jamais de streams, et le réclamer le marquerait
        # incomplet à perpétuité. Un morceau publié seulement sur YouTube est
        # donc COMPLET avec ses seuls streams YTM.
        if not _streams_complets(track):
            missing.append("Streams")

        # 8. Certifications : le champ existe toujours (dataclass), donc jamais
        # « manquant » — la recherche est réputée faite. (Ancien hasattr mort.)

        # Retourner le statut selon les données manquantes
        if len(missing) == 0:
            return "✅"  # Toutes les infos présentes
        else:
            return "⚠️"  # Données incomplètes

    except (AttributeError, TypeError, KeyError, ValueError) as e:
        logger.error(f"Erreur dans get_track_status_icon pour {track.title}: {e}")
        return "⚠️"  # Erreur = incomplet


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
