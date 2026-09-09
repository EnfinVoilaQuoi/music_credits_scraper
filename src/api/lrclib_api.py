"""
Client API LRCLIB (lrclib.net) — paroles synchronisées (.lrc) libres, sans clé.

Doc de référence : docs/api/lrclib-api.md.

Rôle dans le projet : **SOURCE 1** des timestamps (paroles synchronisées), devant YTM.
Renvoie du LRC (`[mm:ss.xx] ligne`) + le texte brut. **Aucun crédit** (paroles seules) :
à combiner avec Genius/YTM pour les crédits.

Matching :
- `/get` exige track + artist + album + duration, avec **tolérance ±2 s** sur la durée
  (excellent désambiguïsateur). Utilisé quand la durée ET l'album sont connus.
- Fallback `/search` (titre + artiste) : jusqu'à 20 candidats, on exige un **match de
  titre fort** puis on départage par la durée quand elle est connue.

Politesse : User-Agent identifiable (recommandé par LRCLIB), retries réseau via config.
Pas de rate limit annoncé, on respecte quand même DELAY_BETWEEN_REQUESTS.
"""

import asyncio
import time
from typing import TYPE_CHECKING

import httpx
import requests

# Comparateurs titre/artiste : définis UNE fois dans `_text_match` (ils étaient
# dupliqués à l'octet près entre ce module et son jumeau). Ré-exportés sous
# leurs noms d'origine — les appelants et les tests ne changent pas.
from src.api._text_match import (  # noqa: F401 — ré-export
    _FEAT_RE,
    _PAREN_RE,
    _artist_match,
    _norm,
    _strip_accents,
    _title_core,
    _title_match,
)
from src.observability import source_usage
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.api.async_http import AsyncHttpSession

try:
    from src.config import DELAY_BETWEEN_REQUESTS, MAX_RETRIES
except ImportError:  # exécution hors package (tests standalone)
    DELAY_BETWEEN_REQUESTS, MAX_RETRIES = 1, 3

logger = get_logger(__name__)

#: Clé de `source_health.SOURCES` sous laquelle cet usage est compté.
_SOURCE = "lrclib"

# User-Agent identifiable, recommandé par LRCLIB (nom + version + lien projet).
_USER_AGENT = "MusicCreditsScraper/1.0 (+https://github.com/g78rem/music_credits_scraper)"

# En-têtes par requête pour la voie async : l'AsyncHttpSession est PARTAGÉE (UA
# httpx par défaut) — on repasse l'UA identifiable par requête (comme la session
# sync le pose sur son propre requests.Session), recommandé par LRCLIB.
_ASYNC_HEADERS = {"User-Agent": _USER_AGENT}

# Seuil de similarité de titre pour accepter un candidat /search (« titre fort exigé »).
_TITLE_MATCH_MIN = 0.72


def _best_search_hit(
    results: list,
    track_name: str,
    artist_name: str | None,
    duration: int | None,
    require_synced: bool,
) -> dict | None:
    """Meilleur candidat `/search` : titre fort exigé, départage/bonus par la durée.

    Pur (sans I/O) → partagé VERBATIM par les voies sync (`search`) et async
    (`search_async`). Renvoie le candidat brut LRCLIB (non `_pack`é) ou None.
    """
    best, best_score = None, -1.0
    for cand in results:
        if not isinstance(cand, dict):
            continue
        if require_synced and not cand.get("syncedLyrics"):
            continue
        tscore = _title_match(track_name, cand.get("trackName", ""))
        if tscore < _TITLE_MATCH_MIN:
            continue  # titre pas assez proche → écarté
        ascore = (
            _artist_match(artist_name or "", cand.get("artistName", "")) if artist_name else 0.5
        )
        score = tscore + 0.5 * ascore
        # Départage / bonus par la durée réelle
        if duration and cand.get("duration"):
            diff = abs(int(cand["duration"]) - int(duration))
            if diff <= 2:
                score += 1.0  # match durée quasi exact = forte confiance
            elif diff <= 5:
                score += 0.3
            else:
                score -= min(diff / 60.0, 1.0)  # pénalité croissante
        if score > best_score:
            best, best_score = cand, score
    return best


class LRCLIBAPI:
    """Client lecture seule pour lrclib.net (paroles synchronisées libres)."""

    BASE_URL = "https://lrclib.net/api"

    def __init__(self, timeout: int = 12):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _USER_AGENT})
        self.timeout = timeout

    # ── HTTP ──────────────────────────────────────────────────────────────────
    def _request(self, path: str, params: dict) -> object | None:
        """
        GET avec retries réseau/5xx. Renvoie le JSON parsé (dict ou list),
        None sur 404 (TrackNotFound) ou échec définitif.
        """
        url = f"{self.BASE_URL}{path}"
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                # Une TENTATIVE par essai ; le verdict, lui, reste unique (il
                # est arrêté par l'observation ouverte plus haut).
                source_usage.record_response(url, r.status_code, headers=r.headers)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 404:
                    return None  # non trouvé : inutile de réessayer
                # 5xx / 429 : on réessaie
                last_err = f"HTTP {r.status_code}"
            except requests.RequestException as e:
                source_usage.note_failure(_SOURCE, e)
                last_err = str(e)
            if attempt < MAX_RETRIES - 1:
                time.sleep(max(DELAY_BETWEEN_REQUESTS, 0.5) * (attempt + 1))
        logger.debug(f"LRCLIB {path} échec ({last_err}) params={params}")
        return None

    # ── API publique ──────────────────────────────────────────────────────────
    def _pack(self, obj: dict) -> dict | None:
        """Objet Lyrics LRCLIB → dict interne homogène (ou None si vide/instrumental)."""
        if not isinstance(obj, dict):
            return None
        synced = obj.get("syncedLyrics") or None
        plain = obj.get("plainLyrics") or None
        if not synced and not plain:
            return None
        return {
            "lyrics_synced": synced,
            "lyrics": plain,
            "source": "LRCLIB",
            "lrclib_id": obj.get("id"),
            "duration": obj.get("duration"),
            "instrumental": bool(obj.get("instrumental")),
        }

    def get_exact(
        self, track_name: str, artist_name: str, album_name: str, duration: int
    ) -> dict | None:
        """`/get` : match sur les 4 champs (durée ±2 s). Renvoie un dict interne ou None."""
        obj = self._request(
            "/get",
            {
                "track_name": track_name,
                "artist_name": artist_name,
                "album_name": album_name or "",
                "duration": int(duration),
            },
        )
        return self._pack(obj) if isinstance(obj, dict) else None

    def _select_hit(
        self,
        results: list,
        track_name: str,
        artist_name: str | None,
        duration: int | None,
        require_synced: bool,
    ) -> dict | None:
        """Meilleur candidat d'une liste `/search` déjà obtenue (pur, sans réseau)."""
        if not results:
            return None
        best = _best_search_hit(results, track_name, artist_name, duration, require_synced)
        return self._pack(best) if best else None

    # ── Jumeaux ASYNC (F5) : même logique, sur l'AsyncHttpSession partagée ───────
    async def _request_async(self, http: "AsyncHttpSession", path: str, params: dict):
        """Jumeau async de `_request` (mêmes retries 200/404/5xx, sleep non bloquant)."""
        url = f"{self.BASE_URL}{path}"
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                r = await http.get(url, params=params, headers=_ASYNC_HEADERS, timeout=self.timeout)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 404:
                    return None  # non trouvé : inutile de réessayer
                last_err = f"HTTP {r.status_code}"  # 5xx / 429 : on réessaie
            except httpx.HTTPError as e:
                last_err = str(e)
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(max(DELAY_BETWEEN_REQUESTS, 0.5) * (attempt + 1))
        logger.debug(f"LRCLIB {path} échec ({last_err}) params={params}")
        return None

    async def get_exact_async(
        self, http: "AsyncHttpSession", track_name, artist_name, album_name, duration
    ) -> dict | None:
        """Jumeau async de `get_exact` (`/get`, match sur les 4 champs, durée ±2 s)."""
        obj = await self._request_async(
            http,
            "/get",
            {
                "track_name": track_name,
                "artist_name": artist_name,
                "album_name": album_name or "",
                "duration": int(duration),
            },
        )
        return self._pack(obj) if isinstance(obj, dict) else None

    async def search_async(
        self,
        http: "AsyncHttpSession",
        track_name: str,
        artist_name: str | None = None,
        duration: int | None = None,
        require_synced: bool = True,
    ) -> dict | None:
        """Jumeau async de `search` (sélection via `_best_search_hit`, partagée)."""
        results = await self._search_raw_async(http, track_name, artist_name)
        return self._select_hit(results, track_name, artist_name, duration, require_synced)

    async def _search_raw_async(
        self, http: "AsyncHttpSession", track_name: str, artist_name: str | None
    ) -> list:
        """Jumeau async de `_search_raw` (résultats bruts, sélection séparée)."""
        params = {"track_name": track_name}
        if artist_name:
            params["artist_name"] = artist_name
        results = await self._request_async(http, "/search", params)
        return results if isinstance(results, list) else []

    async def get_synced_async(
        self,
        http: "AsyncHttpSession",
        track_name: str,
        artist_name: str,
        album_name: str | None = None,
        duration: int | None = None,
    ) -> dict | None:
        """Jumeau async de `get_synced` : `/get` exact puis fallback `/search`."""
        if not track_name or not artist_name:
            return None

        with source_usage.observe(_SOURCE, label=f"{artist_name} — {track_name}") as obs:
            return await self._get_synced_async_body(
                obs, http, track_name, artist_name, album_name, duration
            )

    async def _get_synced_async_body(
        self, obs, http, track_name, artist_name, album_name, duration
    ) -> dict | None:
        """Corps de `get_synced_async`, sous l'observation ouverte par elle."""
        if duration and album_name:
            hit = await self.get_exact_async(http, track_name, artist_name, album_name, duration)
            if hit and hit.get("lyrics_synced"):
                logger.info(
                    f"🎵 LRCLIB /get: '{artist_name} - {track_name}' "
                    f"(synchro, id={hit.get('lrclib_id')})"
                )
                obs.ok()
                return hit

        # Une SEULE requête `/search` pour les deux passes : `require_synced`
        # est un filtre local (cf. `_search_raw`).
        results = await self._search_raw_async(http, track_name, artist_name)

        hit = self._select_hit(results, track_name, artist_name, duration, True)
        if hit and hit.get("lyrics_synced"):
            logger.info(
                f"🎵 LRCLIB /search: '{artist_name} - {track_name}' "
                f"(synchro, id={hit.get('lrclib_id')})"
            )
            obs.ok()
            return hit

        hit = self._select_hit(results, track_name, artist_name, duration, False)
        if hit:
            logger.debug(f"LRCLIB: seulement texte brut pour '{artist_name} - {track_name}'")
            obs.ok()
            return hit

        obs.absent("aucune parole pour ce morceau")
        return None
