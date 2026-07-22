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

Voie async (F5) : LRCLIB et Musixmatch tapent désormais l'`AsyncHttpSession`
partagée (httpx + rate-limit par domaine) via leurs jumeaux `*_async`. Le provider
possède la session et fait le pont vers le resolver (qui reste sync) au moyen de
`run_sync` — même primitive que ce worker utilise déjà pour les crawls Genius (F4).
Deux petits adaptateurs (`_LrclibBridge`/`_MusixmatchBridge`) exposent au resolver
l'interface sync qu'il attend (`get_synced` / `get_synced_as_source3`). YTM reste
sync (aucune API async côté `ytmusicapi`).
"""

from datetime import datetime

from src.concurrency.async_loop import run_sync
from src.enrichment.base import Capability
from src.utils.logger import get_logger
from src.utils.synced_lyrics_resolver import SyncedLyricsOutcome, resolve_track_synced_lyrics

logger = get_logger(__name__)


class _LrclibBridge:
    """Expose l'interface sync `get_synced` du resolver, exécutée en async partagé."""

    def __init__(self, api, http, runner):
        self._api, self._http, self._runner = api, http, runner

    def get_synced(self, track_name, artist_name, album_name=None, duration=None):
        return self._runner(
            self._api.get_synced_async(
                self._http, track_name, artist_name, album_name=album_name, duration=duration
            )
        )


class _MusixmatchBridge:
    """Expose l'interface sync `get_synced_as_source3`, exécutée en async partagé."""

    def __init__(self, api, http, runner):
        self._api, self._http, self._runner = api, http, runner

    def get_synced_as_source3(self, track_name, artist_name, duration=None):
        return self._runner(
            self._api.get_synced_as_source3_async(
                self._http, track_name, artist_name, duration=duration
            )
        )


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
        http=None,
        runner=run_sync,
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
        # Voie async : session httpx partagée (lazy, créée dans la boucle au 1er
        # usage) + runner (run_sync par défaut, injectable pour tests offline).
        self._http = http
        self._runner = runner

    # ── Session HTTP async (lazy, partagée par les ponts LRCLIB/Musixmatch) ──

    def _http_session(self):
        if self._http is None:
            from src.api.async_http import AsyncHttpSession

            self._http = AsyncHttpSession()
        return self._http

    # ── Clients (lazy, selon les sources demandées) ─────────────────────────

    def _lrclib_client(self):
        # Client injecté (tests) : utilisé tel quel. Sinon pont async LRCLIB.
        if self._lrclib is None and self._sync_lrclib:
            from src.api.lrclib_api import LRCLIBAPI

            self._lrclib = _LrclibBridge(LRCLIBAPI(), self._http_session(), self._runner)
        return self._lrclib

    def _ytm_client(self):
        # YTM sert de source 2 (sync) ET/OU de fallback TEXTE (lyrics_ytm).
        if self._ytm is None and (self._sync_ytm or self._lyrics_ytm):
            from src.api.ytmusic_api import YTMusicAPI

            self._ytm = YTMusicAPI()
        return self._ytm

    def _mxm_client(self):
        # Client injecté (tests) : utilisé tel quel. Sinon pont async Musixmatch.
        if self._mxm is None and self._sync_musixmatch:
            from src.api.musixmatch_api import MusixmatchAPI

            # token en cache réutilisé sur tout le batch (dans l'instance API).
            self._mxm = _MusixmatchBridge(MusixmatchAPI(), self._http_session(), self._runner)
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
        """Ferme les clients qui l'exposent (défensif) PUIS la session async
        partagée (« qui crée ferme » ; idempotente, rouverte au batch suivant)."""
        for client in (self._lrclib, self._ytm, self._mxm):
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as e:  # noqa: BLE001 — fermeture best-effort (client arbitraire)
                    logger.debug(f"Fermeture client paroles: {e}")
        if self._http is not None:
            try:
                self._runner(self._http.aclose())
            except Exception as e:  # noqa: BLE001 — fermeture best-effort (session async)
                logger.debug(f"Fermeture session async paroles: {e}")
            self._http = None
