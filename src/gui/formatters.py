"""Présentation des certifications pour la GUI.

Le modèle `Track` ne doit pas savoir que « Diamant » = 💎 : toute la mise en
forme lisible des certifications (emoji, texte, durées) vit ici.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from src.utils.dates import parse_flexible

if TYPE_CHECKING:
    from src.models.track import Track

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


def certification_info(track: "Track") -> str:
    """Retourne une description textuelle de la certification la plus haute."""
    if not track.has_certification:
        return "Pas de certification"

    info = f"{certification_emoji(track.certification_level)} {track.certification_level}"

    if track.certification_date:
        date_str = (
            track.certification_date.strftime("%d/%m/%Y")
            if isinstance(track.certification_date, datetime)
            else str(track.certification_date)
        )
        info += f" (obtenue le {date_str}"

        if track.certification_duration_days is not None:
            years = track.certification_duration_days // 365
            months = (track.certification_duration_days % 365) // 30
            days = track.certification_duration_days % 30

            duration_str = ""
            if years > 0:
                duration_str += f"{years} an{'s' if years > 1 else ''}"
            if months > 0:
                if duration_str:
                    duration_str += ", "
                duration_str += f"{months} mois"
            if days > 0 and years == 0:  # On n'affiche les jours que si moins d'un an
                if duration_str:
                    duration_str += ", "
                duration_str += f"{days} jour{'s' if days > 1 else ''}"

            if duration_str:
                info += f" - durée: {duration_str}"

        info += ")"

    return info


def _format_cert_line(cert: dict) -> str:
    """Formate une ligne « emoji niveau (date) » pour une certification."""
    level = cert.get("certification", "")
    date = cert.get("certification_date", "")
    parsed = parse_flexible(date)
    if parsed is not None:
        date_str = parsed.strftime("%d/%m/%Y")
    elif isinstance(date, str):
        date_str = date  # chaîne non-ISO : affichée telle quelle
    else:
        date_str = ""

    line = f"  {certification_emoji(level)} {level}"
    if date_str:
        line += f" ({date_str})"
    return line


def all_certifications_info(track: "Track") -> str:
    """Retourne une description de TOUTES les certifications (morceau + album)."""
    lines: list[str] = []

    if track.certifications:
        lines.append("🎵 Certifications du morceau:")
        lines.extend(_format_cert_line(cert) for cert in track.certifications)

    if track.album_certifications:
        if lines:
            lines.append("")  # Ligne vide de séparation
        lines.append(f"💿 Certifications de l'album '{track.album}':")
        lines.extend(_format_cert_line(cert) for cert in track.album_certifications)

    if not lines:
        return "Pas de certification"

    return "\n".join(lines)
