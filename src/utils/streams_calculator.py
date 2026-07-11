"""
Calcul estimatif du nombre de streams total et des auditeurs mensuels.

Parts de marché streaming France 2025 :
  Spotify   ~40 %
  YT Music  ~25 %
  Ensemble  ~65 %

Optimisation : si une seule source est disponible, on extrapole via
sa part individuelle (plus précis que diviser par 65 %).
  - Spotify seul  → total = spotify / 0.40
  - YTM seul      → total = ytm    / 0.25
  - Les deux      → total = (spotify + ytm) / 0.65
"""

from typing import Optional

# ── Parts de marché ───────────────────────────────────────────────────────────
SPOTIFY_SHARE = 0.40
YTM_SHARE = 0.25
COMBINED_SHARE = SPOTIFY_SHARE + YTM_SHARE  # 0.65


def calculate_total_streams(
    spotify_streams: Optional[int],
    ytm_streams: Optional[int],
) -> Optional[int]:
    """Estime le nombre total de streams toutes plateformes.

    Returns:
        int estimé, ou None si aucune donnée disponible.
    """
    sp = spotify_streams if spotify_streams and spotify_streams > 0 else None
    yt = ytm_streams if ytm_streams and ytm_streams > 0 else None

    if sp and yt:
        return int((sp + yt) / COMBINED_SHARE)
    if sp:
        return int(sp / SPOTIFY_SHARE)
    if yt:
        return int(yt / YTM_SHARE)
    return None


def calculate_total_monthly_listeners(
    spotify_listeners: Optional[int],
    ytm_listeners: Optional[int],
) -> Optional[int]:
    """Estime le nombre total d'auditeurs mensuels toutes plateformes.

    Returns:
        int estimé, ou None si aucune donnée disponible.
    """
    sp = spotify_listeners if spotify_listeners and spotify_listeners > 0 else None
    yt = ytm_listeners if ytm_listeners and ytm_listeners > 0 else None

    if sp and yt:
        return int((sp + yt) / COMBINED_SHARE)
    if sp:
        return int(sp / SPOTIFY_SHARE)
    if yt:
        return int(yt / YTM_SHARE)
    return None


def streams_source_label(
    spotify_streams: Optional[int],
    ytm_streams: Optional[int],
) -> str:
    """Retourne un indicateur de complétude des données.

    Returns:
        ""       si les deux sources sont présentes (données complètes)
        " ~Sp"   si Spotify seul
        " ~YT"   si YouTube Music seul
        ""       si aucune donnée
    """
    sp = bool(spotify_streams and spotify_streams > 0)
    yt = bool(ytm_streams and ytm_streams > 0)
    if sp and yt:
        return ""
    if sp:
        return " ~Sp"
    if yt:
        return " ~YT"
    return ""


def format_streams(n: Optional[int], suffix: str = "") -> str:
    """Formate un nombre de streams pour affichage (séparateur milliers = espace).

    Args:
        n:      nombre à formater
        suffix: suffixe indicateur (" ~Sp", " ~YT", …)

    Returns:
        "15 734 892" ou "15 734 892 ~Sp" ou "" si None.
    """
    if n is None:
        return ""
    formatted = f"{n:,}".replace(",", " ")  # espace fine insécable
    return f"{formatted}{suffix}"
