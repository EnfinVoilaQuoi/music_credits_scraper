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
import re
import time
import logging
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, List, Optional

import requests

try:
    from src.config import DELAY_BETWEEN_REQUESTS, MAX_RETRIES
except Exception:  # exécution hors package (tests standalone)
    DELAY_BETWEEN_REQUESTS, MAX_RETRIES = 1, 3

logger = logging.getLogger(__name__)

# User-Agent identifiable, recommandé par LRCLIB (nom + version + lien projet).
_USER_AGENT = "MusicCreditsScraper/1.0 (+https://github.com/g78rem/music_credits_scraper)"

# Seuil de similarité de titre pour accepter un candidat /search (« titre fort exigé »).
_TITLE_MATCH_MIN = 0.72


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _norm(s: str) -> str:
    """Normalisation robuste : minuscules, sans accents, alphanumérique + espaces."""
    s = _strip_accents((s or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# Parenthèses de version/remaster/feat qui parasitent le matching de titre.
_PAREN_RE = re.compile(r"[\(\[\{].*?[\)\]\}]")
_FEAT_RE = re.compile(r"\b(feat|ft|featuring|with|avec)\b.*$")


def _title_core(title: str) -> str:
    """Titre nu pour comparaison : retire (feat…), [remaster], suffixes feat."""
    t = _strip_accents((title or "").lower())
    t = _PAREN_RE.sub(" ", t)
    t = _FEAT_RE.sub(" ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _title_match(a: str, b: str) -> float:
    """Score de correspondance de titre 0..1, tolérant aux variantes de version/feat."""
    ca, cb = _title_core(a), _title_core(b)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0
    ratio = SequenceMatcher(None, ca, cb).ratio()
    # Bonus si l'un est strictement contenu dans l'autre (ex. "song" ⊂ "song pt ii")
    if ca in cb or cb in ca:
        ratio = max(ratio, 0.9)
    return ratio


def _artist_match(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb or na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


class LRCLIBAPI:
    """Client lecture seule pour lrclib.net (paroles synchronisées libres)."""

    BASE_URL = "https://lrclib.net/api"

    def __init__(self, timeout: int = 12):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _USER_AGENT})
        self.timeout = timeout

    # ── HTTP ──────────────────────────────────────────────────────────────────
    def _request(self, path: str, params: Dict) -> Optional[object]:
        """
        GET avec retries réseau/5xx. Renvoie le JSON parsé (dict ou list),
        None sur 404 (TrackNotFound) ou échec définitif.
        """
        url = f"{self.BASE_URL}{path}"
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 404:
                    return None  # non trouvé : inutile de réessayer
                # 5xx / 429 : on réessaie
                last_err = f"HTTP {r.status_code}"
            except requests.RequestException as e:
                last_err = str(e)
            if attempt < MAX_RETRIES - 1:
                time.sleep(max(DELAY_BETWEEN_REQUESTS, 0.5) * (attempt + 1))
        logger.debug(f"LRCLIB {path} échec ({last_err}) params={params}")
        return None

    # ── API publique ──────────────────────────────────────────────────────────
    def _pack(self, obj: Dict) -> Optional[Dict]:
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

    def get_exact(self, track_name: str, artist_name: str,
                  album_name: str, duration: int) -> Optional[Dict]:
        """`/get` : match sur les 4 champs (durée ±2 s). Renvoie un dict interne ou None."""
        obj = self._request("/get", {
            "track_name": track_name,
            "artist_name": artist_name,
            "album_name": album_name or "",
            "duration": int(duration),
        })
        return self._pack(obj) if isinstance(obj, dict) else None

    def search(self, track_name: str, artist_name: Optional[str] = None,
               duration: Optional[int] = None,
               require_synced: bool = True) -> Optional[Dict]:
        """
        `/search` (titre + artiste) puis sélection du meilleur candidat :
        titre fort exigé, départage par la durée (±2 s privilégié) si connue.
        """
        params = {"track_name": track_name}
        if artist_name:
            params["artist_name"] = artist_name
        results = self._request("/search", params)
        if not isinstance(results, list) or not results:
            return None

        best, best_score = None, -1.0
        for cand in results:
            if not isinstance(cand, dict):
                continue
            if require_synced and not cand.get("syncedLyrics"):
                continue
            tscore = _title_match(track_name, cand.get("trackName", ""))
            if tscore < _TITLE_MATCH_MIN:
                continue  # titre pas assez proche → écarté
            ascore = _artist_match(artist_name or "", cand.get("artistName", "")) if artist_name else 0.5
            score = tscore + 0.5 * ascore
            # Départage / bonus par la durée réelle
            if duration and cand.get("duration"):
                diff = abs(int(cand["duration"]) - int(duration))
                if diff <= 2:
                    score += 1.0          # match durée quasi exact = forte confiance
                elif diff <= 5:
                    score += 0.3
                else:
                    score -= min(diff / 60.0, 1.0)  # pénalité croissante
            if score > best_score:
                best, best_score = cand, score

        return self._pack(best) if best else None

    def get_synced(self, track_name: str, artist_name: str,
                   album_name: Optional[str] = None,
                   duration: Optional[int] = None) -> Optional[Dict]:
        """
        Point d'entrée principal (SOURCE 1 des timestamps).

        Stratégie : `/get` exact quand durée + album connus (match ±2 s), sinon/à défaut
        fallback `/search` (titre fort + départage durée). Renvoie un dict interne
        {'lyrics_synced', 'lyrics', 'source':'LRCLIB', 'lrclib_id', 'duration',
        'instrumental'} ou None.
        """
        if not track_name or not artist_name:
            return None

        # 1) Match exact si on a la durée ET l'album (les 4 champs requis par /get)
        if duration and album_name:
            hit = self.get_exact(track_name, artist_name, album_name, duration)
            if hit and hit.get("lyrics_synced"):
                logger.info(f"🎵 LRCLIB /get: '{artist_name} - {track_name}' (synchro, id={hit.get('lrclib_id')})")
                return hit

        # 2) Fallback recherche (titre fort + départage durée)
        hit = self.search(track_name, artist_name, duration=duration, require_synced=True)
        if hit and hit.get("lyrics_synced"):
            logger.info(f"🎵 LRCLIB /search: '{artist_name} - {track_name}' (synchro, id={hit.get('lrclib_id')})")
            return hit

        # 3) Dernier recours : texte brut (pas de synchro) via /search
        hit = self.search(track_name, artist_name, duration=duration, require_synced=False)
        if hit:
            logger.debug(f"LRCLIB: seulement texte brut pour '{artist_name} - {track_name}'")
            return hit

        return None
