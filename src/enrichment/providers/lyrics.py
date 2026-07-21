"""Provider paroles synchronisées (LRCLIB / YTM / Musixmatch) — capability LYRICS.

Packagé comme provider (E7b `Capability.LYRICS`) mais **PAS ENCORE consommé par
l'orchestrateur audio** (`enrich_track`) : utilisé par le worker GUI « Crédits &
Paroles » (`src/gui/workers/scraping.py`) — MÊME déclencheur, comportement
inchangé. Il POSSÈDE les clients (créés lazy selon les sources cochées) et
délègue la résolution PAR MORCEAU au `synced_lyrics_resolver` (pur/testé), puis
APPLIQUE le résultat au morceau (observations + colonnes `lyrics_synced*` +
fallback texte). Le worker garde la boucle batch, les compteurs, la progression
et les saves.

Interface volontairement propre au domaine paroles (pas l'`EnrichmentContext`
audio) : le jour où l'orchestrateur consommera la capability LYRICS, un adaptateur
fera le pont — sans réécrire ce cœur.
"""

from datetime import datetime

from src.enrichment.base import Capability
from src.utils.logger import get_logger
from src.utils.synced_lyrics_resolver import SyncedLyricsOutcome, resolve_track_synced_lyrics

logger = get_logger(__name__)


class LyricsProvider:
    """Résout + applique la synchro/texte d'un morceau via les sources cochées."""

    name = "lyrics"
    capabilities = {Capability.LYRICS}

    def __init__(
        self,
        *,
        sync_lrclib: bool = True,
        sync_ytm: bool = True,
        sync_musixmatch: bool = False,
        lyrics_ytm: bool = True,
        lrclib=None,
        ytm=None,
        mxm=None,
    ):
        # Sources activées (dérivées des cases du dialogue GUI).
        self._sync_lrclib = sync_lrclib
        self._sync_ytm = sync_ytm
        self._sync_musixmatch = sync_musixmatch
        self._lyrics_ytm = lyrics_ytm
        # Clients : injectés (tests) ou créés LAZY au 1er usage selon les sources
        # (comme le worker historique : une seule instance par batch).
        self._lrclib = lrclib
        self._ytm = ytm
        self._mxm = mxm

    # ── Clients (lazy, selon les sources demandées) ─────────────────────────

    def _lrclib_client(self):
        if self._lrclib is None and self._sync_lrclib:
            from src.api.lrclib_api import LRCLIBAPI

            self._lrclib = LRCLIBAPI()
        return self._lrclib

    def _ytm_client(self):
        # YTM sert de source 2 (sync) ET/OU de fallback TEXTE (lyrics_ytm).
        if self._ytm is None and (self._sync_ytm or self._lyrics_ytm):
            from src.api.ytmusic_api import YTMusicAPI

            self._ytm = YTMusicAPI()
        return self._ytm

    def _mxm_client(self):
        if self._mxm is None and self._sync_musixmatch:
            from src.api.musixmatch_api import MusixmatchAPI

            self._mxm = MusixmatchAPI()  # token en cache réutilisé sur tout le batch
        return self._mxm

    # ── Enrichissement paroles d'UN morceau ─────────────────────────────────

    def enrich(
        self, track, artist_name: str, *, need_sync: bool, need_text: bool
    ) -> SyncedLyricsOutcome:
        """Résout la synchro/texte du morceau (resolver pur) PUIS applique au track
        (observations + colonnes + fallback texte). Renvoie l'`outcome` : le worker
        agrège les compteurs (LRCLIB/YTM/Musixmatch, croisés/à vérifier, textes)."""
        outcome = resolve_track_synced_lyrics(
            track,
            artist_name,
            lrclib=self._lrclib_client(),
            ytm=self._ytm_client(),
            mxm=self._mxm_client(),
            need_sync=need_sync,
            need_text=need_text,
            sync_ytm=self._sync_ytm,
        )
        track.observations.extend(outcome.observations)
        if outcome.lyrics_synced is not None:
            track.lyrics.synced = outcome.lyrics_synced
            track.lyrics.synced_source = outcome.lyrics_synced_source
            track.lyrics.synced_confidence = outcome.lyrics_synced_confidence
        if outcome.text is not None:
            track.lyrics.text = outcome.text
            track.lyrics.present = True
            track.lyrics.scraped_at = datetime.now()
            track.lyrics.source = outcome.text_source
        return outcome

    def close(self) -> None:
        """Ferme les clients qui l'exposent (défensif — LRCLIB/YTM/Musixmatch
        n'ont pas de ressource persistante ; no-op sinon)."""
        for client in (self._lrclib, self._ytm, self._mxm):
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as e:
                    logger.debug(f"Fermeture client paroles: {e}")
