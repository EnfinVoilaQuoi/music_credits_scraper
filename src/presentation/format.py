"""Mise en forme des valeurs affichées — une seule fois pour les deux GUI.

Les fonctions de cellule (crédits, paroles), la table des émojis de source et
la mise en forme des dates vivaient dans `src/gui/helpers.py` ; elles sont ici
depuis le 2026-10-11, `helpers` les ré-exporte (même comportement, mêmes noms).
"""

from __future__ import annotations

import unicodedata
from datetime import datetime

from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── Texte ────────────────────────────────────────────────────────────────────


def normalize_text(text: str) -> str:
    """Normalise le texte pour le tri (sans accents, minuscules)"""
    if not text:
        return ""
    # Supprimer les accents
    text = unicodedata.normalize("NFD", str(text))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    # Convertir en minuscules
    return text.lower()


# ── Nombres ──────────────────────────────────────────────────────────────────


def nombre(n: int | float | None) -> str:
    """« 1 037 457 » — séparateur de milliers = espace ; vide si absent."""
    if n is None:
        return ""
    return f"{int(n):,}".replace(",", " ")


def _decimales(valeur: float) -> str:
    """Trois chiffres significatifs, virgule décimale : 1,07 · 56,1 · 561."""
    if valeur >= 100:
        texte = f"{valeur:.0f}"
    elif valeur >= 10:
        texte = f"{valeur:.1f}"
    else:
        texte = f"{valeur:.2f}"
    if "." in texte:
        texte = texte.rstrip("0").rstrip(".")
    return texte.replace(".", ",")


def compact(n: int | float | None) -> str:
    """« 1,07 M », « 561 M », « 2,3 Md », « 300 k » — pour un bandeau."""
    if n is None:
        return ""
    n = float(n)
    for seuil, unite in ((1e9, "Md"), (1e6, "M"), (1e3, "k")):
        if abs(n) >= seuil:
            return f"{_decimales(n / seuil)} {unite}"
    return f"{n:.0f}"


# ── Durées et dates ──────────────────────────────────────────────────────────


def duree(secondes: int | None) -> str:
    """« 3:47 », « 1:02:05 » ; vide si absente ou nulle."""
    if not secondes:
        return ""
    secondes = int(secondes)
    heures, reste = divmod(secondes, 3600)
    minutes, sec = divmod(reste, 60)
    if heures:
        return f"{heures}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def format_date(release_date, vide: str = "N/A"):
    """Formate une date pour l'affichage en format français DD/MM/YYYY"""
    if not release_date:
        return vide

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
        return vide


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


# ── Cellules de morceau ──────────────────────────────────────────────────────


def format_credits_cell(track) -> str:
    """Cellule « Crédits » du tableau : le nombre, suivi de ⏳ quand ce ne
    sont que des crédits PROVISOIRES de l'API Genius (le scrape n'est pas
    passé : il manque au moins les contributeurs sans page Genius)."""
    from src.utils.credits_genius_api import sont_provisoires

    n = len(track.credits)
    return f"{n} ⏳" if sont_provisoires(track.credits) else str(n)


def format_lyrics_cell(track) -> str:
    """Cellule « Paroles » du tableau : ✓ = texte, ⏱ = timestamps (en plus ou
    seuls), 🎹 = instrumental CONSTATÉ sur Genius (scrape réussi, pas de paroles
    par nature — e27), 📭 = page Genius lue sans paroles « pas encore
    transcrites » (relue au bout de 30 j), vide = rien ou jamais cherché."""
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
    if track.lyrics.non_transcrites():
        return "📭"
    return ""


def tonalite(track) -> str:
    """Notation française de la tonalité (« Do mineur »), ou vide.

    `musical_key` d'abord ; sinon dérivée de key/mode SANS l'écrire sur l'objet
    (la table CustomTkinter la mémorisait au passage : un affichage n'écrit pas).
    """
    if track.audio.musical_key:
        return track.audio.musical_key
    if track.audio.key is not None and track.audio.mode is not None:
        from src.utils.music_theory import key_mode_to_french_from_string

        return key_mode_to_french_from_string(track.audio.key, track.audio.mode) or ""
    return ""


def bpm_cellule(track) -> str:
    """« 142 (Do mineur) », « 142 », ou vide."""
    if not track.audio.bpm:
        return ""
    ton = tonalite(track)
    return f"{track.audio.bpm} ({ton})" if ton else str(track.audio.bpm)


# ── Crédits ──────────────────────────────────────────────────────────────────

#: Émoji par source de crédit. Table UNIQUE : la fiche morceau en avait deux
#: copies (musique et vidéo), et celle des vidéos avait perdu `deezer`,
#: `spotify_web`, `kworb` et `heritage` — les crédits venus de là s'affichaient
#: tous avec le même 🔗 « source inconnue ».
EMOJI_SOURCE = {
    "genius": "🎤",
    "genius_api": "🎫",
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


# ── Certifications ───────────────────────────────────────────────────────────

_EMOJI_CERTIF = {
    # BPI (UK) : palier SOUS l'or, propre à ce corps.
    "Silver": "🥈",
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
    return _EMOJI_CERTIF.get(level, "🏆")


def rang_certif(level: str | None) -> int:
    """Rang du palier (1 = le plus haut), par le référentiel UNIQUE des
    certifs (`cert_normalize.RANG_PALIERS`) ; 99 si inconnu ou absent."""
    if not level:
        return 99
    from src.utils.cert_normalize import RANG_PALIERS

    return RANG_PALIERS.get(level.strip().lower(), 99)
