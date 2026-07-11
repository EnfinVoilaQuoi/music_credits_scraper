"""Enrichissement des données avec les certifications"""

from typing import List, Dict, Any, Optional
from datetime import datetime

from src.models import Artist, Track
from src.api.snep_certifications import get_snep_manager
from src.utils.cert_matcher import get_cert_matcher
from src.utils.logger import get_logger

logger = get_logger(__name__)


class CertificationEnricher:
    """Enrichit les données d'artistes et morceaux avec les certifications.

    Utilise le matcher UNIFIÉ (SNEP 🇫🇷 + BRMA 🇧🇪 + RIAA à venir) : le
    raccordement morceau/album ↔ certif est mutualisé pour toutes les sources.
    """

    def __init__(self):
        """Initialise l'enrichisseur de certifications"""
        self.snep_manager = get_snep_manager()  # conservé (compat éventuelle)
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

    def enrich_tracks(self, artist: Artist, tracks: List[Track]) -> List[Track]:
        """Enrichit une liste de morceaux avec leurs certifications - VERSION AMÉLIORÉE"""
        if not tracks or not artist:
            return tracks

        # Référence fraîche au matcher : prend en compte un reset_cert_matcher()
        # après une récup de certifs (sinon on garderait l'ancien snapshot).
        self.matcher = get_cert_matcher()

        enriched_count = 0
        album_cache = {}  # Cache pour éviter de chercher plusieurs fois le même album

        for track in tracks:
            try:
                # Normaliser le titre pour la recherche
                # Remplacer les apostrophes Unicode courbes par des apostrophes standard
                track_title = track.title
                track_title = track_title.replace("\u2019", "'")  # ' (RIGHT SINGLE QUOTATION MARK)
                track_title = track_title.replace("\u2018", "'")  # ' (LEFT SINGLE QUOTATION MARK)
                track_title = track_title.replace("\u0153", "œ")  # Œ (OE LIGATURE)
                track_title = track_title.replace("\u0152", "Œ")  # Œ (OE LIGATURE majuscule)

                # Candidats artistes : si notre artiste est secondaire/feat sur ce
                # morceau, la certif peut être déposée sous l'artiste PRINCIPAL →
                # on les passe pour la rattacher quand même.
                extra_artists = []
                pan = getattr(track, "primary_artist_name", None)
                if pan and pan != artist.name:
                    extra_artists.append(pan)
                fa = getattr(track, "featured_artists", None)
                if isinstance(fa, str) and fa:
                    extra_artists.append(fa)
                elif isinstance(fa, (list, tuple)):
                    extra_artists.extend(str(x) for x in fa if x)

                # 1. Rechercher TOUTES les certifications du morceau (tous pays)
                track_certs = self.matcher.get_track_certifications(
                    artist.name, track_title, extra_artists=extra_artists
                )

                # Stocker toutes les certifications
                track.certifications = track_certs if track_certs else []

                # Pour rétrocompatibilité, garder la plus haute certification dans les anciens champs
                if track_certs:
                    highest_cert = track_certs[0]  # Déjà triée par priorité
                    track.has_certification = True
                    track.certification_level = highest_cert.get("certification", "")
                    track.certification_date = highest_cert.get("certification_date", "")
                    track.certification_category = highest_cert.get("category", "")
                    track.certification_publisher = highest_cert.get("publisher", "")
                    track.certification_details = highest_cert

                    enriched_count += 1
                    logger.debug(
                        f"✅ {len(track_certs)} certification(s) trouvée(s): {track.title} - {track.certification_level}"
                    )
                else:
                    track.has_certification = False
                    track.certification_level = None
                    track.certification_date = None
                    track.certification_category = None
                    track.certification_publisher = None
                    track.certification_details = None

                # 2. Rechercher les certifications de l'album associé
                if track.album:
                    # Utiliser le cache si disponible
                    if track.album not in album_cache:
                        album_certs = self.matcher.get_album_certifications(
                            artist.name, track.album
                        )
                        album_cache[track.album] = album_certs
                    else:
                        album_certs = album_cache[track.album]

                    track.album_certifications = album_certs if album_certs else []

                    if album_certs:
                        logger.debug(
                            f"✅ {len(album_certs)} certification(s) d'album trouvée(s) pour '{track.album}'"
                        )
                else:
                    track.album_certifications = []

            except Exception as e:
                logger.error(f"Erreur enrichissement {track.title}: {e}")
                track.certifications = []
                track.album_certifications = []
                track.has_certification = False

        if enriched_count > 0:
            logger.info(f"🏆 {enriched_count}/{len(tracks)} morceaux enrichis avec certifications")

        # Afficher statistiques sur les albums
        albums_with_certs = sum(1 for t in tracks if t.album_certifications)
        if albums_with_certs > 0:
            logger.info(
                f"💿 {albums_with_certs}/{len(tracks)} morceaux ont des certifications d'album"
            )

        return tracks

    def _calculate_artist_stats(self, certifications: List[Dict[str, Any]]) -> Dict[str, Any]:
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
                    except:
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

    def calculate_certification_duration(self, track: Track) -> Optional[int]:
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
