"""Provider Spotify ID — récupère le Track ID Spotify (destiné à ReccoBeats).

Une seule voie : ASYNC (scraper Playwright dans la boucle). La voie sync n'avait
plus d'appelant en production depuis que `DataEnricher` fournit toujours la
variante async : retirée. Spotify n'est utilisé QUE pour l'ID (jamais les
audio-features). L'unicité d'ID passe par le contexte (logique partagée avec
ReccoBeats/SongBPM).
"""

from datetime import datetime

from playwright.async_api import Error as PlaywrightError

from src.enrichment.base import Capability, LazyResource
from src.enrichment.context import EnrichmentContext
from src.models import Track
from src.observability.issues import SansReponse
from src.utils.logger import get_logger
from src.utils.spotify_identity import valider_identite_async

logger = get_logger(__name__)


class SpotifyIdProvider:
    """Récupération du Spotify Track ID via scraper (source `spotify_id`)."""

    name = "spotify_id"
    capabilities = {Capability.BPM}  # E7b structurel (non consommé)
    error_result = False

    def __init__(self, scraper=None, scraper_factory=None):
        # PROPRIÉTAIRE de son scraper ASYNC (créé lazy dans la boucle, fermé par aclose()).
        self._resource = LazyResource(scraper, scraper_factory, label="scraper Spotify ID")

    def is_available(self) -> bool:
        return self._resource.available()

    def close(self) -> None:
        """Rien à fermer ici : le scraper vit dans la boucle asyncio, fermé par
        `aclose()` (le browser meurt là où il est né)."""

    async def aclose(self) -> None:
        """Ferme le scraper s'il l'a créé (browsers de la boucle, F3b)."""
        await self._resource.aclose()

    def gate(self, track: Track, ctx: EnrichmentContext) -> str | None:
        """Skip si un ID valide existe déjà (hors force_update) ou si la voie
        ISRC a fourni les données audio (le scrape Spotify devient inutile)."""
        validate = ctx.validate_spotify_id_unique
        has_valid_id = track.spotify_id and (
            not ctx.artist_tracks
            or validate is None
            or validate(track.spotify_id, track, ctx.artist_tracks)
        )
        if (ctx.force_update or not has_valid_id) and not ctx.isrc_satisfied:
            logger.info(
                f"🎯 Appel du scraper Spotify ID pour '{track.title}' "
                f"(force_update={ctx.force_update}, has_valid_id={has_valid_id})"
            )
            return None
        # Deux motifs de skip distincts : soit l'ISRC a déjà satisfait ReccoBeats
        # en pré-étape (spotify_id souvent None sur cette voie), soit un ID valide
        # existe déjà. Ne pas afficher « ID déjà présent » quand c'est l'ISRC.
        if ctx.isrc_satisfied:
            logger.info(
                f"⏭️ Scraper Spotify ID non nécessaire (ISRC déjà satisfait) pour '{track.title}'"
            )
        else:
            logger.info(
                f"⏭️ Scraper Spotify ID non nécessaire (ID déjà présent et valide: {track.spotify_id})"
            )
        return "not_needed"

    @staticmethod
    def _sans_reponse(track: Track, e: SansReponse) -> None:
        """Spotify n'a pas répondu : AUCUN constat daté (`spotify_id_checked_at`
        reste tel quel) — « jamais cherché » reste vrai, la prochaine passe
        recherchera. Dater ici faisait d'un navigateur mort un « pas sur
        Spotify » permanent (2026-09-28)."""
        logger.warning(f"⚠️ Spotify sans réponse pour '{track.title}' ({e.kind}) — rien daté")
        return None

    @staticmethod
    def _dater_et_prevalider(
        track: Track, ctx: EnrichmentContext, spotify_id: str | None
    ) -> str | None:
        """Une fois le scrape rendu : dater le constat puis valider l'unicité.

        La colonne e17 (`spotify_id_checked_at`) sépare « absent de Spotify » de
        « jamais cherché » : sans cette date, `gui/helpers` affichait « jamais
        cherché » pour des morceaux bel et bien résolus.
        """
        # La recherche a été MENÉE À TERME : on date le constat, trouvé ou non.
        # Sans cette date, un morceau jamais résolu passerait pour absent, et
        # serait validé à tort sans streams.
        track.spotify_id_checked_at = datetime.now().isoformat(timespec="seconds")

        if not spotify_id:
            logger.info(f"∅ Aucun Spotify ID trouvé via scraper pour '{track.title}'")
            return None

        # Valider l'unicité de l'ID trouvé
        validate = ctx.validate_spotify_id_unique
        if validate and not validate(spotify_id, track, ctx.artist_tracks):
            # Le refus est déjà journalisé (et compté) par le validateur ; le
            # candidat appartient à une autre fiche, rien n'est écrit.
            logger.info(f"⏭ Candidat Spotify {spotify_id} écarté : déjà porté par une autre fiche")
            return None

        logger.info(f"✅ Spotify ID unique trouvé via scraper: {spotify_id}")
        return spotify_id

    @staticmethod
    async def _dater_et_valider_async(
        track: Track, ctx: EnrichmentContext, spotify_id: str | None, lire_identite
    ) -> str | None:
        """Unicité PUIS justesse — deux questions distinctes.

        L'unicité demande « cet ID est-il déjà pris ? » et ne dit RIEN de « est-ce
        le bon morceau ? ». Mesuré le 2026-09-08 sur un run réel : **52 IDs sur
        152 (34 %) désignaient le morceau de quelqu'un d'autre**, tous
        parfaitement uniques — le scraper ne trouve pas (freestyles, lives et
        inédits ne sont pas sur Spotify) et retient le résultat le plus proche.

        Refuser est un BON résultat : `spotify_id_checked_at` (e17) date le
        constat, donc la recherche ne recommencera pas en boucle.
        """
        valide = SpotifyIdProvider._dater_et_prevalider(track, ctx, spotify_id)
        if valide is None:
            return None
        return valide if await valider_identite_async(track, valide, lire_identite) else None

    async def enrich_async(self, track: Track, ctx: EnrichmentContext) -> bool:
        """Scraper Playwright ASYNC natif dans la boucle."""
        scraper = self._resource.get()
        if scraper is None:
            logger.warning("❌ Spotify ID scraper non disponible")
            return False

        spotify_id = await self._get_unique_spotify_id_async(track, ctx, scraper)
        if not spotify_id:
            logger.info("∅ Aucun Spotify ID retenu par le scraper")
            return False

        track.spotify_id = spotify_id
        track.add_spotify_id(spotify_id, source="scraper")
        logger.info(f"✅ Spotify ID attribué via scraper: {spotify_id}")

        # Récupérer le titre de la page Spotify pour vérification
        try:
            page_title = await scraper.get_spotify_page_title_async(spotify_id)
            if page_title:
                track.spotify_page_title = page_title
                logger.info(f"📄 Titre de page Spotify: {page_title[:50]}...")
        except PlaywrightError as e:
            logger.debug(f"Impossible de récupérer le titre de page: {e}")

        return True

    async def _get_unique_spotify_id_async(
        self, track: Track, ctx: EnrichmentContext, scraper
    ) -> str | None:
        """Scrape + suite commune (datation du constat, validation d'unicité) —
        l'existant a déjà été jugé par le gate."""
        artist_name = track.artist.name if hasattr(track.artist, "name") else str(track.artist)

        logger.info(f"🔍 Recherche Spotify ID via scraper pour: '{artist_name}' - '{track.title}'")
        try:
            spotify_id = await scraper.get_spotify_id_async(artist_name, track.title)
        except SansReponse as e:
            return self._sans_reponse(track, e)
        return await self._dater_et_valider_async(
            track, ctx, spotify_id, scraper.get_track_identity_async
        )
