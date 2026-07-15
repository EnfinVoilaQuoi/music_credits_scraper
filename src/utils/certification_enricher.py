"""Enrichissement des données avec les certifications.

Cœur : `apply_certifications(artist, tracks, matcher)` (E7g) — rematche chaque
morceau/album contre les CSV clean (matcher en mémoire, offline, rapide) et pose
`track.certifications`/`album_certifications`. La MATÉRIALISATION passe par des
objets typés `Certification` (`from_match`) puis se re-sérialise au format
colonne (`to_column_dict`), byte-compatible avec `cert_matcher._format` (contrat
mapper/GUI inchangé). Ne PERSISTE pas : l'appelant (worker retrieval, E7h) save.

`CertificationEnricher` (classe historique) subsiste pour les scripts one-off et
délègue son `enrich_tracks` à `apply_certifications`.
"""

from datetime import datetime
from typing import Any

from src.models import Artist, Track
from src.models.certification import Certification
from src.utils.cert_matcher import get_cert_matcher
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Substitutions Unicode courantes des titres avant le matching (apostrophes
# courbes, ligatures œ) — alignées sur l'historique de enrich_tracks.
_TITLE_SUBS = {"’": "'", "‘": "'", "œ": "œ", "Œ": "Œ"}


def _normalize_title(title: str) -> str:
    for bad, good in _TITLE_SUBS.items():
        title = title.replace(bad, good)
    return title


def _extra_artists(track: Track, artist_name: str) -> list[str]:
    """Artistes candidats supplémentaires : si NOTRE artiste est secondaire/feat,
    la certif peut être déposée sous l'artiste PRINCIPAL → on le passe pour la
    rattacher quand même."""
    extra: list[str] = []
    pan = getattr(track, "primary_artist_name", None)
    if pan and pan != artist_name:
        extra.append(pan)
    fa = getattr(track, "featured_artists", None)
    if isinstance(fa, str) and fa:
        extra.append(fa)
    elif isinstance(fa, (list, tuple)):
        extra.extend(str(x) for x in fa if x)
    return extra


def apply_certifications(artist: Artist, tracks: list[Track], matcher) -> int:
    """Pose `track.certifications`/`album_certifications` depuis le matcher unifié.

    Matérialise chaque correspondance en `Certification` (frontière typée) puis la
    re-sérialise au format colonne. Renvoie le nombre de morceaux portant au moins
    une certification. Offline (matcher en mémoire), NE PERSISTE PAS.
    """
    if not tracks or not artist:
        return 0

    enriched = 0
    album_cache: dict[str, list[dict]] = {}  # évite de re-chercher le même album

    for track in tracks:
        try:
            title = _normalize_title(track.title)
            extra = _extra_artists(track, artist.name)

            matches = matcher.get_track_certifications(artist.name, title, extra_artists=extra)
            track.certifications = [Certification.from_match(m).to_column_dict() for m in matches]

            if track.certifications:
                highest = track.certifications[0]  # déjà trié par priorité
                track.has_certification = True
                track.certification_level = highest.get("certification", "")
                track.certification_date = highest.get("certification_date", "")
                track.certification_category = highest.get("category", "")
                track.certification_publisher = highest.get("publisher", "")
                track.certification_details = highest
                enriched += 1
            else:
                track.has_certification = False
                track.certification_level = None
                track.certification_date = None
                track.certification_category = None
                track.certification_publisher = None
                track.certification_details = None

            if track.album:
                if track.album not in album_cache:
                    album_cache[track.album] = matcher.get_album_certifications(
                        artist.name, track.album
                    )
                track.album_certifications = [
                    Certification.from_match(m).to_column_dict() for m in album_cache[track.album]
                ]
            else:
                track.album_certifications = []
        except Exception as e:
            logger.error(f"Erreur enrichissement {track.title}: {e}")
            track.certifications = []
            track.album_certifications = []
            track.has_certification = False

    if enriched:
        logger.info(f"🏆 {enriched}/{len(tracks)} morceaux enrichis avec certifications")
    albums_with_certs = sum(1 for t in tracks if t.album_certifications)
    if albums_with_certs:
        logger.info(f"💿 {albums_with_certs}/{len(tracks)} morceaux ont des certifs d'album")
    return enriched


class CertificationEnricher:
    """Enrichit les données d'artistes et morceaux avec les certifications.

    Utilise le matcher UNIFIÉ (SNEP 🇫🇷 + BRMA 🇧🇪 + RIAA à venir) : le
    raccordement morceau/album ↔ certif est mutualisé pour toutes les sources.
    """

    def __init__(self):
        """Initialise l'enrichisseur de certifications"""
        self.matcher = get_cert_matcher()  # raccordement multi-pays
        logger.info("✅ CertificationEnricher initialisé (matcher unifié)")

    def enrich_artist(self, artist: Artist) -> Artist:
        """Enrichit un artiste avec ses certifications"""
        if not artist or not artist.name:
            return artist

        try:
            # Récupérer toutes les certifications de l'artiste
            certifications = self.matcher.get_artist_certifications(artist.name)

            if certifications:
                # Ajouter les certifications à l'artiste
                if not hasattr(artist, "certifications"):
                    artist.certifications = []

                artist.certifications = certifications

                # Ajouter des statistiques
                artist.certification_stats = self._calculate_artist_stats(certifications)

                logger.info(f"✅ {len(certifications)} certifications trouvées pour {artist.name}")
            else:
                logger.info(f"ℹ️ Aucune certification trouvée pour {artist.name}")
                artist.certifications = []
                artist.certification_stats = {}

        except Exception as e:
            logger.error(f"❌ Erreur lors de l'enrichissement de {artist.name}: {e}")
            artist.certifications = []
            artist.certification_stats = {}

        return artist

    def enrich_tracks(self, artist: Artist, tracks: list[Track]) -> list[Track]:
        """Enrichit une liste de morceaux avec leurs certifications.

        Délègue à `apply_certifications` (E7g) — logique mutualisée avec le
        câblage runtime. Rafraîchit d'abord le matcher (prend en compte un
        `reset_cert_matcher()` post-MàJ, sinon on garderait l'ancien snapshot)."""
        self.matcher = get_cert_matcher()
        apply_certifications(artist, tracks, self.matcher)
        return tracks

    def _calculate_artist_stats(self, certifications: list[dict[str, Any]]) -> dict[str, Any]:
        """Calcule les statistiques de certification d'un artiste"""
        stats = {
            "total": len(certifications),
            "by_level": {},
            "by_category": {},
            "by_year": {},
            "highest_certification": None,
            "most_recent": None,
            "singles_count": 0,
            "albums_count": 0,
        }

        if not certifications:
            return stats

        # Ordre de priorité des certifications
        cert_order = [
            "Quadruple Diamant",
            "Triple Diamant",
            "Double Diamant",
            "Diamant",
            "Triple Platine",
            "Double Platine",
            "Platine",
            "Triple Or",
            "Double Or",
            "Or",
        ]

        for cert in certifications:
            # Par niveau
            level = cert.get("certification", "")
            stats["by_level"][level] = stats["by_level"].get(level, 0) + 1

            # Par catégorie
            category = cert.get("category", "")
            stats["by_category"][category] = stats["by_category"].get(category, 0) + 1

            # Compter singles et albums
            if category == "Singles":
                stats["singles_count"] += 1
            elif category == "Albums":
                stats["albums_count"] += 1

            # Par année
            cert_date = cert.get("certification_date")
            if cert_date:
                if isinstance(cert_date, str):
                    try:
                        cert_date = datetime.fromisoformat(cert_date)
                    except Exception:
                        continue
                year = cert_date.year
                stats["by_year"][year] = stats["by_year"].get(year, 0) + 1

        # Plus haute certification
        for cert_level in cert_order:
            if cert_level in stats["by_level"]:
                stats["highest_certification"] = cert_level
                break

        # Certification la plus récente
        sorted_certs = sorted(
            certifications, key=lambda x: x.get("certification_date", ""), reverse=True
        )
        if sorted_certs:
            most_recent = sorted_certs[0]
            stats["most_recent"] = {
                "title": most_recent.get("title"),
                "level": most_recent.get("certification"),
                "date": most_recent.get("certification_date"),
            }

        return stats

    def get_certification_summary(self, artist_name: str) -> str:
        """Génère un résumé textuel des certifications d'un artiste"""
        certifications = self.matcher.get_artist_certifications(artist_name)

        if not certifications:
            return f"Aucune certification trouvée pour {artist_name}"

        stats = self._calculate_artist_stats(certifications)

        summary = f"📊 Certifications de {artist_name}:\n"
        summary += f"• Total: {stats['total']} certifications\n"
        summary += f"• Singles: {stats['singles_count']} | Albums: {stats['albums_count']}\n"

        if stats["highest_certification"]:
            summary += f"• Plus haute: {stats['highest_certification']}\n"

        if stats["most_recent"]:
            summary += f"• Plus récente: {stats['most_recent']['title']} "
            summary += f"({stats['most_recent']['level']}) - {stats['most_recent']['date']}\n"

        # Détail par niveau
        if stats["by_level"]:
            summary += "\n📈 Par niveau:\n"
            for level, count in sorted(stats["by_level"].items(), key=lambda x: x[1], reverse=True):
                summary += f"  • {level}: {count}\n"

        return summary

    def calculate_certification_duration(self, track: Track) -> int | None:
        """Calcule la durée en jours pour obtenir une certification"""
        if not track.certification or not track.release_date:
            return None

        try:
            cert_date = track.certification.get("certification_date")
            if isinstance(cert_date, str):
                cert_date = datetime.fromisoformat(cert_date)

            if isinstance(track.release_date, str):
                release_date = datetime.fromisoformat(track.release_date)
            else:
                release_date = track.release_date

            duration = (cert_date - release_date).days
            return duration if duration >= 0 else None

        except Exception as e:
            logger.error(f"Erreur calcul durée certification: {e}")
            return None
