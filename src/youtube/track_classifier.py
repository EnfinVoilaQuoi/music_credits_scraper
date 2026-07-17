"""Classification des types de morceaux pour stratégie YouTube"""

from enum import Enum

from src.utils.logger import get_logger

logger = get_logger(__name__)


class TrackType(Enum):
    """Types de morceaux pour stratégies de recherche différentes"""

    ALBUM = "album"  # Morceau d'album standard
    EXOTIC = "exotic"  # Morceau rare/exotique
    SINGLE = "single"  # Single officiel
    REMIX = "remix"  # Remix/rework
    LIVE = "live"  # Enregistrement live
    ACOUSTIC = "acoustic"  # Version acoustique


class TrackClassifier:
    """Classifie les morceaux pour déterminer la stratégie de recherche"""

    def __init__(self):
        self.exotic_indicators = [
            "unreleased",
            "leaked",
            "demo",
            "snippet",
            "instrumental",
            "acapella",
            "radio rip",
            "bootleg",
            "edit",
            "vip",
            "exclusive",
            "rare",
            "vault",
            "outtake",
            "alternate",
        ]

        self.remix_indicators = [
            "remix",
            "rmx",
            "rework",
            "edit",
            "bootleg",
            "vip",
            "flip",
            "refix",
            "version",
            "mix",
        ]

        self.live_indicators = [
            "live",
            "concert",
            "tour",
            "session",
            "unplugged",
            "acoustic",
            "stripped",
            "intimate",
        ]

        # Chantier « Media » : émissions / freestyles / cyphers (Grünt, COLORS,
        # Planète Rap, OKLM…). Détectés sur le TITRE OU l'ALBUM. Classés EXOTIC
        # (pas de nouvelle valeur d'enum → zéro ripple sur les seuils / auto-select).
        # Liste extensible.
        self.show_indicators = [
            "grünt",
            "grunt",
            "colors show",
            "a colors show",
            "planète rap",
            "planete rap",
            "oklm",
            "rentre dans le cercle",
            "red bull 64 bars",
            "on the radar",
            "from the block",
            "cypher",
            "freestyle",
            "radio session",
        ]

    def is_show_performance(self, title: str, album: str | None = None) -> bool:
        """True si le morceau est une prestation d'émission/freestyle/cypher.

        Cherche un `show_indicator` dans le titre OU l'album (chantier « Media »).
        Helper public réutilisé par la vignette YouTube ET la classification de
        la vidéo (`youtube_utils.classify_video_kind`).
        """
        haystack = f"{title or ''} {album or ''}".lower()
        return any(indicator in haystack for indicator in self.show_indicators)

    def classify_track(
        self, track_title: str, album: str = None, release_year: int = None, **context
    ) -> TrackType:
        """Classifie un morceau selon son type"""

        title_lower = track_title.lower()

        # Émissions/freestyles (Grünt, COLORS, Planète Rap…) → EXOTIC. Testé en
        # PREMIER : un « Freestyle Planète Rap » ne doit pas tomber en LIVE/REMIX.
        if self.is_show_performance(track_title, album):
            logger.debug(f"'{track_title}' classifié comme EXOTIC (show/freestyle)")
            return TrackType.EXOTIC

        # Détection des remixes
        if any(indicator in title_lower for indicator in self.remix_indicators):
            logger.debug(f"'{track_title}' classifié comme REMIX")
            return TrackType.REMIX

        # Détection des versions live/acoustiques
        if any(indicator in title_lower for indicator in self.live_indicators):
            if "acoustic" in title_lower or "unplugged" in title_lower:
                logger.debug(f"'{track_title}' classifié comme ACOUSTIC")
                return TrackType.ACOUSTIC
            else:
                logger.debug(f"'{track_title}' classifié comme LIVE")
                return TrackType.LIVE

        # Détection des morceaux exotiques
        if any(indicator in title_lower for indicator in self.exotic_indicators):
            logger.debug(f"'{track_title}' classifié comme EXOTIC")
            return TrackType.EXOTIC

        # Basé sur l'année de sortie
        if release_year and release_year < 1990:
            logger.debug(f"'{track_title}' classifié comme EXOTIC (année: {release_year})")
            return TrackType.EXOTIC

        # Basé sur l'absence d'album (probable single)
        if not album or album.lower() in ["single", "ep", "maxi"]:
            logger.debug(f"'{track_title}' classifié comme SINGLE")
            return TrackType.SINGLE

        # Par défaut : morceau d'album standard
        logger.debug(f"'{track_title}' classifié comme ALBUM (défaut)")
        return TrackType.ALBUM

    def should_auto_select(self, track_type: TrackType) -> bool:
        """Détermine si un type de morceau permet la sélection automatique"""

        auto_select_types = {
            TrackType.ALBUM: True,  # Auto pour morceaux d'album
            TrackType.SINGLE: True,  # Auto pour singles
            TrackType.REMIX: True,  # Auto pour remixes populaires
            TrackType.LIVE: False,  # Manuel pour lives (trop de variantes)
            TrackType.ACOUSTIC: True,  # Auto pour acoustiques
            TrackType.EXOTIC: False,  # Manuel pour exotiques
        }

        return auto_select_types.get(track_type, False)

    def get_confidence_threshold(self, track_type: TrackType) -> float:
        """Retourne le seuil de confiance selon le type"""

        thresholds = {
            TrackType.ALBUM: 0.85,  # Strict pour albums
            TrackType.SINGLE: 0.80,  # Assez strict pour singles
            TrackType.REMIX: 0.75,  # Moins strict pour remixes
            TrackType.LIVE: 0.70,  # Permissif pour lives
            TrackType.ACOUSTIC: 0.80,  # Assez strict pour acoustiques
            TrackType.EXOTIC: 0.60,  # Très permissif pour exotiques
        }

        return thresholds.get(track_type, 0.75)

    def get_search_strategy(self, track_type: TrackType) -> dict[str, str]:
        """Retourne la stratégie de recherche selon le type"""

        strategies = {
            TrackType.ALBUM: {
                "primary_query": '"{artist}" "{title}"',
                "fallback_query": "{artist} {title} official",
                "priority": "official_channels",
            },
            TrackType.SINGLE: {
                "primary_query": "{artist} {title} official",
                "fallback_query": '"{artist}" "{title}"',
                "priority": "official_channels",
            },
            TrackType.REMIX: {
                "primary_query": "{artist} {title}",
                "fallback_query": "{title} {artist}",
                "priority": "high_quality",
            },
            TrackType.LIVE: {
                "primary_query": "{artist} {title} live",
                "fallback_query": "{artist} {title} concert",
                "priority": "video_quality",
            },
            TrackType.ACOUSTIC: {
                "primary_query": "{artist} {title} acoustic",
                "fallback_query": "{artist} {title} unplugged",
                "priority": "official_channels",
            },
            TrackType.EXOTIC: {
                "primary_query": "{artist} {title}",
                "fallback_query": "{title}",
                "priority": "any_match",
            },
        }

        return strategies.get(track_type, strategies[TrackType.ALBUM])
