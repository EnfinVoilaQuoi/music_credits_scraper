"""
Client API Musixmatch (endpoint « desktop » non officiel) — paroles synchronisées.

Rôle dans le projet : **SOURCE 3** des timestamps, en **dernier recours gated**, uniquement
quand LRCLIB (source 1) ET YouTube Music (source 2) échouent tous les deux. Ne JAMAIS
l'utiliser en source primaire de batch (cf. gestion du rate limit ci-dessous).

Mécanisme (reverse-engineering du client web-desktop, identique à syncedlyrics /
YTubic / navidrome-musixmatch-plugin) :
  - L'API officielle exige un contrat payant. Le lecteur desktop, lui, tape
    `https://apic-desktop.musixmatch.com/ws/1.1` avec un `usertoken` de session
    obtenu gratuitement via `token.get`.
  - Un seul appel `macro.subtitles.get` renvoie d'un coup : le morceau apparié
    (`matcher.track.get`), le LRC ligne-à-ligne (`track.subtitles.get`), le richsync
    mot-à-mot (`track.richsync.get`) et le texte brut (`track.lyrics.get`). On
    privilégie l'appel unique pour minimiser le nombre de requêtes (donc le risque
    de flag IP / CAPTCHA).

Quatre gardes-fous — la partie non triviale, apprise des projets de référence :
  1. **TTL courte** : le token n'est valide que ~10 min côté serveur. On le met en cache
     (mémoire + fichier) avec une TTL prudente sous ce seuil, on ne le refetch pas à
     chaque appel.
  2. **Token factice** : quand l'IP est flaggée ou le CAPTCHA-gate actif, Musixmatch
     renvoie un token de forme valide mais inutilisable — soit contenant « UpgradeOnly »,
     soit un seul caractère répété (56 zéros observés le 2026-09-05). `_is_degenerate_token`
     les rejette AVANT usage ET avant mise en cache : accepté, un leurre empoisonne le
     cache fichier pour toute la durée du TTL et rend le run silencieusement stérile.
  3. **Retry 401** : un token périmé est rejeté au niveau HTTP (401/403), dans l'enveloppe
     JSON racine (`message.header.status_code`) **ou dans un SOUS-APPEL macro** — ce
     dernier cas ressortait en « absent » (cf. `_macro_auth_failed`). On invalide le cache
     et on réessaie une fois avec un token frais.
  4. **Dégradation propre** : toute erreur (réseau, parsing, blocage) renvoie None sans
     jamais lever — c'est une source facultative, elle ne doit jamais casser le pipeline.
  5. **Fenêtre de repos** (2026-09-06) : rejeter le leurre ne suffisait pas. Quand
     `token.get` ne rend PLUS de jeton utilisable, chaque morceau suivant rejouait
     deux requêtes sur une IP déjà bridée, et un WARNING par morceau — la « rafale »
     décrite au WIP. On cesse d'interroger la source pendant
     `settings.musixmatch_token_cooldown_s`, puis on reprend seul. Ce n'est PAS un
     disjoncteur : la source doit pouvoir revenir dans le même run. Pendant la
     fenêtre, l'appel est déclaré `skipped` — hors dénominateur : ne pas appeler
     n'est pas un échec de la source, et compter 0/0 comme un échec mentirait.

Vérifications de correspondance :
  - Match serveur par durée (`f_subtitle_length` ± `f_subtitle_length_max_deviation`)
    quand la durée réelle est connue (Deezer canonique / YTM secours), en cohérence
    avec le départage par durée du reste du projet.
  - Contrôle local titre/artiste du morceau apparié (Musixmatch peut renvoyer un match
    approximatif) : rejet si en dessous des seuils → évite d'attacher les mauvaises paroles.

Sortie : dict homogène avec `lrclib_api` (drop-in comme peer source), plus un helper
`get_synced_as_source3_async()` qui renvoie directement la forme de
`lyrics_sync.compare_synced` (`{'lrc','source','confidence','note'}`, confidence=1 =
source unique/dernier recours).

Voie SYNC retirée le 2026-09-05 (A3) : elle n'avait plus d'appelant depuis le câblage
des jumeaux async (le provider injecte `_MusixmatchBridge`, qui expose l'interface sync
et route vers l'async). Ce n'était donc pas un filet — rien n'y basculait — mais deux
implémentations à corriger en parallèle : les deux défauts trouvés le jour même ont dû
être appliqués deux fois, et le premier ne l'avait été QUE sur la voie morte.

Coupe-circuit : `MUSIXMATCH_ENABLED=false` (env) désactive la source sans toucher au code
— utile car cette API privée peut cesser de fonctionner sans préavis.
Token épinglé optionnel : `MUSIXMATCH_USER_TOKEN` (env) amorce le cache ; en cas d'échec
d'auth, on retombe automatiquement sur `token.get`.
"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from src.api.async_http import AsyncHttpSession

try:
    from src.config import (
        DATA_DIR,
        DELAY_BETWEEN_REQUESTS,
        MAX_RETRIES,
        MUSIXMATCH_TOKEN_COOLDOWN_S,
    )
except ImportError:  # exécution hors package (tests standalone)
    DELAY_BETWEEN_REQUESTS, MAX_RETRIES = 1, 3
    MUSIXMATCH_TOKEN_COOLDOWN_S = 600
    DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

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
from src.observability.issues import IssueKind

logger = logging.getLogger(__name__)

#: Clé de `source_health.SOURCES` sous laquelle cet usage est compté.
_SOURCE = "musixmatch"

# ── Constantes du client desktop ────────────────────────────────────────────────
_API_BASE = "https://apic-desktop.musixmatch.com/ws/1.1"
_APP_ID = "web-desktop-app-v1.0"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
# En-têtes par requête pour la voie async : l'AsyncHttpSession est PARTAGÉE (UA
# httpx par défaut) → on repasse par requête les en-têtes de la session sync
# (UA navigateur + Accept + Cookie AWSELB, indispensables pour ne pas être flaggé).
_ASYNC_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/json",
    "Accept-Language": "en",
    "Cookie": "AWSELB=0; AWSELBCORS=0",
}
# Le token serveur s'invalide vers ~10 min ; on rafraîchit un peu avant.
_TOKEN_TTL = 9 * 60  # secondes
_TOKEN_FILE = Path(DATA_DIR) / ".musixmatch_token.json"

_STATUS_OK = 200
_STATUS_AUTH = 401  # blocage token / CAPTCHA-gate

# Seuils de validation du morceau apparié (mêmes ordres de grandeur que lrclib_api).
_TITLE_MATCH_MIN = 0.72
_ARTIST_MATCH_MIN = 0.55

# Sentinelle interne : échec d'authentification → déclenche l'invalidation + retry.
_AUTH_FAILURE = object()


def _is_degenerate_token(token: str) -> bool:
    """Token de FORME valide mais inutilisable (leurre servi par Musixmatch).

    Quand l'IP est bridée ou que la requête sent le robot, `token.get` répond
    HTTP 200 avec un jeton factice au lieu d'une erreur. Deux formes observées :
    la mention `UpgradeOnly`, et une suite d'un seul caractère répété — mesuré
    le 2026-09-05, 56 zéros mis en cache puis utilisés pendant tout un run.

    Le tester ici plutôt qu'au site d'appel : c'est la SEULE façon de ne pas
    empoisonner le cache fichier pour la durée du TTL.
    """
    t = (token or "").strip()
    if not t or "UpgradeOnly" in t:
        return True
    # Un vrai usertoken est une empreinte variée ; un seul caractère répété
    # (zéros, 'x'…) sur toute la longueur ne peut pas en être un.
    return len(set(t)) == 1


# ── Normalisation / matching (copies locales : module autonome, comme lrclib_api) ─
def _looks_synced(lrc: str | None) -> bool:
    """Un LRC exploitable contient au moins une balise `[mm:ss...]`."""
    return bool(lrc) and re.search(r"\[\d+:\d+", lrc) is not None


class MusixmatchAPI:
    """Client lecture seule pour l'endpoint desktop Musixmatch (paroles synchronisées)."""

    def __init__(
        self, timeout: int = 12, token_file: Path | None = None, enabled: bool | None = None
    ):
        self.timeout = timeout
        self.token_file = Path(token_file) if token_file else _TOKEN_FILE
        # Coupe-circuit global (env prioritaire, argument explicite sinon).
        if enabled is None:
            enabled = os.getenv("MUSIXMATCH_ENABLED", "true").strip().lower() != "false"
        self.enabled = enabled

        # Cache token en mémoire : (token, obtained_at_epoch).
        self._token: str | None = None
        self._token_ts: float = 0.0

        # Fenêtre de repos : instant (epoch) avant lequel on n'interroge plus.
        self._repos_jusqua: float = 0.0

        # Token épinglé optionnel : amorce le cache, expiry gérée normalement.
        pinned = (os.getenv("MUSIXMATCH_USER_TOKEN") or "").strip()
        if pinned and not _is_degenerate_token(pinned):
            self._token, self._token_ts = pinned, time.time()

    # ── Fenêtre de repos ────────────────────────────────────────────────────────
    def _au_repos(self) -> bool:
        """La source est-elle en repos ? Journalise la reprise, une seule fois."""
        if not self._repos_jusqua:
            return False
        if time.time() < self._repos_jusqua:
            return True
        self._repos_jusqua = 0.0
        logger.info("Musixmatch : fin de la fenêtre de repos, on retente.")
        return False

    def _mettre_au_repos(self, raison: str) -> None:
        """Arme la fenêtre. Un SEUL warning par fenêtre, pas un par morceau."""
        duree = MUSIXMATCH_TOKEN_COOLDOWN_S
        if duree <= 0:
            return
        deja_armee = self._repos_jusqua > time.time()
        self._repos_jusqua = time.time() + duree
        if not deja_armee:
            logger.warning(
                f"Musixmatch : jeton inutilisable ({raison}) — source mise au repos "
                f"{duree} s (insister sur une IP bridée ne fait que l'aggraver)."
            )

    # ── Gestion du token ────────────────────────────────────────────────────────
    def _load_cached_token(self) -> str | None:
        """Token encore valide (mémoire puis fichier), sinon None."""
        now = time.time()
        if self._token and (now - self._token_ts) < _TOKEN_TTL:
            return self._token
        try:
            if self.token_file.exists():
                data = json.loads(self.token_file.read_text(encoding="utf-8"))
                tok, ts = data.get("token"), float(data.get("obtained_at", 0))
                if tok and not _is_degenerate_token(tok) and (now - ts) < _TOKEN_TTL:
                    self._token, self._token_ts = tok, ts
                    return tok
        except (OSError, ValueError, TypeError) as e:  # cache corrompu → on l'ignore
            logger.debug(f"Musixmatch: cache token illisible ({e})")
        return None

    def _save_token(self, token: str) -> None:
        self._token, self._token_ts = token, time.time()
        try:
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.write_text(
                json.dumps({"token": token, "obtained_at": self._token_ts}),
                encoding="utf-8",
            )
        except OSError as e:  # échec disque non bloquant (copie mémoire conservée)
            logger.debug(f"Musixmatch: écriture cache token impossible ({e})")

    def _invalidate_token(self) -> None:
        self._token, self._token_ts = None, 0.0
        try:
            if self.token_file.exists():
                self.token_file.unlink()
        except OSError:
            pass

    # ── HTTP ──────────────────────────────────────────────────────────────────────
    # ── Extraction depuis la réponse macro ────────────────────────────────────────
    @staticmethod
    def _macro_calls(env: dict) -> dict[str, dict]:
        body = (env.get("message") or {}).get("body") or {}
        calls = body.get("macro_calls")
        return calls if isinstance(calls, dict) else {}

    @staticmethod
    def _macro_auth_failed(calls: dict[str, dict]) -> bool:
        """Un sous-appel macro porte-t-il un 401 ?

        `macro.subtitles.get` peut répondre 200 à la racine tout en refusant
        l'authentification DANS ses sous-appels. `_call_body` collapsait tout
        non-200 en « pas de données » : un token refusé ressortait alors en
        `absent`, verdict qui — par construction — n'est JAMAIS compté comme un
        échec. L'auth cassée devenait donc invisible dans le panneau de santé,
        exactement le trou de capteur que le modèle d'observabilité proscrit.
        """
        for call in calls.values():
            if not isinstance(call, dict):
                continue
            header = ((call.get("message") or {}).get("header")) or {}
            if header.get("status_code") == _STATUS_AUTH:
                return True
        return False

    @staticmethod
    def _call_body(calls: dict[str, dict], key: str) -> dict | None:
        """Corps d'un sous-appel macro si son propre header est 200 et non vide."""
        call = calls.get(key)
        if not isinstance(call, dict):
            return None
        msg = call.get("message") or {}
        if (msg.get("header") or {}).get("status_code") != _STATUS_OK:
            return None
        body = msg.get("body")
        return body if isinstance(body, dict) and body else None

    def _matched_track(self, calls: dict[str, dict]) -> dict | None:
        body = self._call_body(calls, "matcher.track.get")
        track = (body or {}).get("track") if body else None
        return track if isinstance(track, dict) else None

    def _subtitle_lrc(self, calls: dict[str, dict]) -> str | None:
        body = self._call_body(calls, "track.subtitles.get")
        if not body:
            return None
        subs = body.get("subtitle_list") or []
        if subs and isinstance(subs, list):
            sub = (subs[0] or {}).get("subtitle") or {}
            lrc = sub.get("subtitle_body")
            if _looks_synced(lrc):
                return lrc
        return None

    def _richsync_as_lrc(self, calls: dict[str, dict]) -> str | None:
        """Richsync (mot-à-mot) → LRC ligne-à-ligne (secours si pas de subtitle)."""
        body = self._call_body(calls, "track.richsync.get")
        raw = (body or {}).get("richsync_body") if body else None
        if not raw:
            return None
        try:
            lines = json.loads(raw)
        except (ValueError, TypeError):
            return None
        out = []
        for ln in lines:
            ts = ln.get("ts")
            text = (ln.get("x") or "").strip()
            if ts is None or not text:
                continue
            out.append(f"{_sec_to_lrc(float(ts))}{text}")
        return "\n".join(out) if out else None

    def _plain_lyrics(self, calls: dict[str, dict]) -> tuple[str | None, bool]:
        """(texte brut, instrumental?)."""
        body = self._call_body(calls, "track.lyrics.get")
        lyr = (body or {}).get("lyrics") if body else None
        if not isinstance(lyr, dict):
            return None, False
        instrumental = bool(lyr.get("instrumental"))
        text = lyr.get("lyrics_body") or None
        return text, instrumental

    def _verify_match(self, track: dict | None, q_track: str, q_artist: str) -> bool:
        """Contrôle titre/artiste du morceau apparié (rejette les faux positifs)."""
        if not track:
            return True  # pas de métadonnées de match → on ne bloque pas
        t_ok = _title_match(q_track, track.get("track_name", "")) >= _TITLE_MATCH_MIN
        a_ok = (not q_artist) or _artist_match(
            q_artist, track.get("artist_name", "")
        ) >= _ARTIST_MATCH_MIN
        if not (t_ok and a_ok):
            logger.debug(
                "Musixmatch: match rejeté '%s - %s' vs '%s - %s'",
                track.get("artist_name"),
                track.get("track_name"),
                q_artist,
                q_track,
            )
        return t_ok and a_ok

    # ── Point d'entrée ────────────────────────────────────────────────────────────
    # ── Jumeaux ASYNC (F5) : même logique + gardes-fous, sur l'AsyncHttpSession ──
    # Le cache token (mémoire + fichier) et les helpers d'extraction/vérification
    # (purs) sont RÉUTILISÉS tels quels : seuls les appels HTTP passent en async.
    # Les I/O fichier du cache token (_load_cached_token/_save_token) restent sync
    # dans la coroutine (~1 ms, hors boucle chaude) — assumé.
    async def _api_get_async(
        self, http: "AsyncHttpSession", action: str, params: dict, with_token: bool = True
    ) -> tuple[int | None, dict | None]:
        """Jumeau async de `_api_get` (mêmes retries/gardes-fous, sleep non bloquant)."""
        q = dict(params)
        q["app_id"] = _APP_ID
        q["format"] = "json"
        q["t"] = str(int(time.time() * 1000))
        if with_token:
            tok = self._load_cached_token() or await self._fetch_new_token_async(http)
            if not tok:
                return _STATUS_AUTH, None
            q["usertoken"] = tok

        url = f"{_API_BASE}/{action}"
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                r = await http.get(url, params=q, headers=_ASYNC_HEADERS, timeout=self.timeout)
                if r.status_code == _STATUS_OK:
                    try:
                        return _STATUS_OK, r.json()
                    except ValueError as e:
                        last_err = f"JSON invalide ({e})"
                        return _STATUS_OK, None  # 200 corps illisible : ne pas réessayer
                if r.status_code in (401, 403):
                    return _STATUS_AUTH, None  # auth : ne pas réessayer aveuglément
                last_err = f"HTTP {r.status_code}"  # 5xx/429 → retry
            except httpx.HTTPError as e:
                last_err = str(e)
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(max(DELAY_BETWEEN_REQUESTS, 0.5) * (attempt + 1))
        logger.debug(f"Musixmatch {action} échec ({last_err})")
        return None, None

    async def _fetch_new_token_async(self, http: "AsyncHttpSession") -> str | None:
        """Jumeau async de `_fetch_new_token` (mêmes gardes-fous #2, mise en cache)."""
        status, env = await self._api_get_async(
            http, "token.get", {"user_language": "en"}, with_token=False
        )
        if env is None:
            return None
        if status == _STATUS_AUTH or _envelope_status(env) == _STATUS_AUTH:
            logger.debug("Musixmatch: 401 sur token.get (IP flaggée / CAPTCHA-gate ?)")
            self._mettre_au_repos("401 sur token.get")
            return None
        token = ((env.get("message") or {}).get("body") or {}).get("user_token") or ""
        # Garde-fou #2 : token de forme valide mais inutilisable.
        if _is_degenerate_token(token):
            logger.debug(f"Musixmatch: token factice rejeté ({token[:12]!r}…, IP restreinte)")
            self._mettre_au_repos(f"jeton factice {token[:12]!r}…")
            return None
        self._save_token(token)
        logger.debug("Musixmatch: nouveau usertoken obtenu (async)")
        return token

    async def get_synced_async(
        self,
        http: "AsyncHttpSession",
        track_name: str,
        artist_name: str,
        duration: float | None = None,
        album_name: str | None = None,
    ) -> dict | None:
        """Jumeau async de `get_synced` (passe 1 + retry unique après refresh token)."""
        if not self.enabled or not track_name or not artist_name:
            return None
        # Le retry après refresh de token est une SECONDE tentative du même appel
        # logique : une observation les couvre toutes deux, un seul verdict.
        with source_usage.observe(_SOURCE, label=f"{artist_name} — {track_name}") as obs:
            if self._au_repos():
                obs.skipped("fenêtre de repos (jeton refusé)")
                return None
            result = await self._try_fetch_async(http, track_name, artist_name, duration, False)
            if result is _AUTH_FAILURE:
                logger.debug("Musixmatch: auth échouée → refresh token + retry (async)")
                result = await self._try_fetch_async(http, track_name, artist_name, duration, True)
            if result is _AUTH_FAILURE:
                obs.fail(IssueKind.AUTH, "token refusé même après refresh")
                return None
            if not result:
                obs.absent("aucune parole synchronisée")
            return result

    async def _try_fetch_async(
        self,
        http: "AsyncHttpSession",
        track_name: str,
        artist_name: str,
        duration: float | None,
        force_token: bool,
    ):
        """Jumeau async de `_try_fetch` (même enchaînement, sentinelle _AUTH_FAILURE)."""
        if force_token:
            self._invalidate_token()
            if await self._fetch_new_token_async(http) is None:
                return _AUTH_FAILURE

        params = {
            "q_track": track_name,
            "q_artist": artist_name,
            "namespace": "lyrics_richsynced",
            "optional_calls": "track.richsync",
            "subtitle_format": "lrc",
        }
        if duration and duration > 0:
            params["f_subtitle_length"] = str(int(round(duration)))
            params["f_subtitle_length_max_deviation"] = "3"

        status, env = await self._api_get_async(
            http, "macro.subtitles.get", params, with_token=True
        )
        if status == _STATUS_AUTH:
            self._invalidate_token()
            return _AUTH_FAILURE
        if env is None:
            return None
        if _envelope_status(env) == _STATUS_AUTH:
            self._invalidate_token()
            return _AUTH_FAILURE

        calls = self._macro_calls(env)
        if not calls:
            return None
        if self._macro_auth_failed(calls):
            self._invalidate_token()
            return _AUTH_FAILURE

        track = self._matched_track(calls)
        if not self._verify_match(track, track_name, artist_name):
            return None  # mauvais morceau → on n'attache rien

        synced = self._subtitle_lrc(calls) or self._richsync_as_lrc(calls)
        plain, instrumental = self._plain_lyrics(calls)
        if not synced and not plain and not instrumental:
            return None

        track = track or {}
        tid = track.get("track_id")
        tdur = track.get("track_length") or (int(round(duration)) if duration else None)
        if synced:
            logger.info("🎵 Musixmatch: '%s - %s' (synchro, id=%s)", artist_name, track_name, tid)
        else:
            logger.debug("Musixmatch: seulement texte brut pour '%s - %s'", artist_name, track_name)
        return {
            "lyrics_synced": synced,
            "lyrics": plain,
            "source": "Musixmatch",
            "musixmatch_track_id": tid,
            "duration": tdur,
            "instrumental": instrumental,
        }

    async def get_synced_as_source3_async(
        self,
        http: "AsyncHttpSession",
        track_name: str,
        artist_name: str,
        duration: float | None = None,
    ) -> dict | None:
        """Jumeau async de `get_synced_as_source3` (forme `compare_synced`, conf=1)."""
        hit = await self.get_synced_async(http, track_name, artist_name, duration=duration)
        if not hit or not hit.get("lyrics_synced"):
            return None
        return {
            "lrc": hit["lyrics_synced"],
            "source": "Musixmatch",
            "confidence": 1,
            "note": "source unique (Musixmatch, dernier recours)",
        }


# ── Utilitaires temps ────────────────────────────────────────────────────────────
def _sec_to_lrc(total: float) -> str:
    """Secondes → balise LRC `[mm:ss.xx]`."""
    if total < 0:
        total = 0.0
    m = int(total) // 60
    s = int(total) % 60
    cs = int(round((total - int(total)) * 100))
    if cs == 100:  # arrondi qui déborde
        s += 1
        cs = 0
        if s == 60:
            m += 1
            s = 0
    return f"[{m:02d}:{s:02d}.{cs:02d}]"


def _envelope_status(env: dict | None) -> int | None:
    """status_code de l'enveloppe racine Musixmatch."""
    if not isinstance(env, dict):
        return None
    return ((env.get("message") or {}).get("header") or {}).get("status_code")
