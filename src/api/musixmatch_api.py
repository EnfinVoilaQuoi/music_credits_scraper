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
  2. **Token « UpgradeOnly »** : quand l'IP est flaggée ou le CAPTCHA-gate actif,
     Musixmatch renvoie un token de forme valide mais inutilisable (contient
     « UpgradeOnly »). On le rejette AVANT de l'utiliser.
  3. **Retry 401** : un token périmé est rejeté soit au niveau HTTP (401/403), soit dans
     l'enveloppe JSON (`message.header.status_code == 401`). On invalide le cache et on
     réessaie une fois avec un token frais.
  4. **Dégradation propre** : toute erreur (réseau, parsing, blocage) renvoie None sans
     jamais lever — c'est une source facultative, elle ne doit jamais casser le pipeline.

Vérifications de correspondance :
  - Match serveur par durée (`f_subtitle_length` ± `f_subtitle_length_max_deviation`)
    quand la durée réelle est connue (Deezer canonique / YTM secours), en cohérence
    avec le départage par durée du reste du projet.
  - Contrôle local titre/artiste du morceau apparié (Musixmatch peut renvoyer un match
    approximatif) : rejet si en dessous des seuils → évite d'attacher les mauvaises paroles.

Sortie : dict homogène avec `lrclib_api` (drop-in comme peer source), plus un helper
`get_synced_as_source3()` qui renvoie directement la forme de `lyrics_sync.compare_synced`
(`{'lrc','source','confidence','note'}`, confidence=1 = source unique/dernier recours).

Coupe-circuit : `MUSIXMATCH_ENABLED=false` (env) désactive la source sans toucher au code
— utile car cette API privée peut cesser de fonctionner sans préavis.
Token épinglé optionnel : `MUSIXMATCH_USER_TOKEN` (env) amorce le cache ; en cas d'échec
d'auth, on retombe automatiquement sur `token.get`.
"""

import json
import logging
import os
import re
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import requests

try:
    from src.config import DATA_DIR, DELAY_BETWEEN_REQUESTS, MAX_RETRIES
except Exception:  # exécution hors package (tests standalone)
    DELAY_BETWEEN_REQUESTS, MAX_RETRIES = 1, 3
    DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

logger = logging.getLogger(__name__)

# ── Constantes du client desktop ────────────────────────────────────────────────
_API_BASE = "https://apic-desktop.musixmatch.com/ws/1.1"
_APP_ID = "web-desktop-app-v1.0"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
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


# ── Normalisation / matching (copies locales : module autonome, comme lrclib_api) ─
def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _norm(s: str) -> str:
    s = _strip_accents((s or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


_PAREN_RE = re.compile(r"[\(\[\{].*?[\)\]\}]")
_FEAT_RE = re.compile(r"\b(feat|ft|featuring|with|avec)\b.*$")


def _title_core(title: str) -> str:
    t = _strip_accents((title or "").lower())
    t = _PAREN_RE.sub(" ", t)
    t = _FEAT_RE.sub(" ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _title_match(a: str, b: str) -> float:
    ca, cb = _title_core(a), _title_core(b)
    if not ca or not cb:
        return 0.0
    if ca == cb:
        return 1.0
    ratio = SequenceMatcher(None, ca, cb).ratio()
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

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
                "Accept-Language": "en",
                # Amadoue le load-balancer AWS ; inoffensif sinon (cf. plugin de référence).
                "Cookie": "AWSELB=0; AWSELBCORS=0",
            }
        )

        # Cache token en mémoire : (token, obtained_at_epoch).
        self._token: str | None = None
        self._token_ts: float = 0.0

        # Token épinglé optionnel : amorce le cache, expiry gérée normalement.
        pinned = (os.getenv("MUSIXMATCH_USER_TOKEN") or "").strip()
        if pinned and "UpgradeOnly" not in pinned:
            self._token, self._token_ts = pinned, time.time()

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
                if tok and "UpgradeOnly" not in tok and (now - ts) < _TOKEN_TTL:
                    self._token, self._token_ts = tok, ts
                    return tok
        except Exception as e:  # cache corrompu → on l'ignore
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
        except Exception as e:  # échec disque non bloquant (copie mémoire conservée)
            logger.debug(f"Musixmatch: écriture cache token impossible ({e})")

    def _invalidate_token(self) -> None:
        self._token, self._token_ts = None, 0.0
        try:
            if self.token_file.exists():
                self.token_file.unlink()
        except Exception:
            pass

    def _fetch_new_token(self) -> str | None:
        """Appelle `token.get`, applique les gardes-fous, met en cache. None si échec."""
        status, env = self._api_get("token.get", {"user_language": "en"}, with_token=False)
        if env is None:
            return None
        if status == _STATUS_AUTH or _envelope_status(env) == _STATUS_AUTH:
            logger.warning("Musixmatch: 401 sur token.get (IP flaggée / CAPTCHA-gate ?)")
            return None
        token = ((env.get("message") or {}).get("body") or {}).get("user_token") or ""
        # Garde-fou #2 : token de forme valide mais inutilisable.
        if not token or "UpgradeOnly" in token:
            logger.warning("Musixmatch: token 'UpgradeOnly'/vide rejeté (IP restreinte)")
            return None
        self._save_token(token)
        logger.debug("Musixmatch: nouveau usertoken obtenu")
        return token

    def _get_token(self, force_refresh: bool = False) -> str | None:
        if not force_refresh:
            cached = self._load_cached_token()
            if cached:
                return cached
        else:
            self._invalidate_token()
        return self._fetch_new_token()

    # ── HTTP ──────────────────────────────────────────────────────────────────────
    def _api_get(
        self, action: str, params: dict, with_token: bool = True
    ) -> tuple[int | None, dict | None]:
        """
        GET vers l'endpoint desktop avec retries réseau/5xx (respecte DELAY/MAX_RETRIES).
        Ajoute `app_id`, `format`, `t` (anti-cache) et le `usertoken` si demandé.
        Renvoie (http_status, enveloppe_json) ; (status, None) si parsing/échec définitif.
        """
        q = dict(params)
        q["app_id"] = _APP_ID
        q["format"] = "json"
        q["t"] = str(int(time.time() * 1000))
        if with_token:
            tok = self._load_cached_token() or self._fetch_new_token()
            if not tok:
                return _STATUS_AUTH, None
            q["usertoken"] = tok

        url = f"{_API_BASE}/{action}"
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                r = self.session.get(url, params=q, timeout=self.timeout)
                if r.status_code == _STATUS_OK:
                    try:
                        return _STATUS_OK, r.json()
                    except ValueError as e:
                        last_err = f"JSON invalide ({e})"
                        return _STATUS_OK, None  # 200 mais corps illisible : inutile de réessayer
                if r.status_code in (401, 403):
                    return _STATUS_AUTH, None  # auth : ne pas réessayer aveuglément
                last_err = f"HTTP {r.status_code}"  # 5xx/429 → retry
            except requests.RequestException as e:
                last_err = str(e)
            if attempt < MAX_RETRIES - 1:
                time.sleep(max(DELAY_BETWEEN_REQUESTS, 0.5) * (attempt + 1))
        logger.debug(f"Musixmatch {action} échec ({last_err})")
        return None, None

    # ── Extraction depuis la réponse macro ────────────────────────────────────────
    @staticmethod
    def _macro_calls(env: dict) -> dict[str, dict]:
        body = (env.get("message") or {}).get("body") or {}
        calls = body.get("macro_calls")
        return calls if isinstance(calls, dict) else {}

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
    def get_synced(
        self,
        track_name: str,
        artist_name: str,
        duration: float | None = None,
        album_name: str | None = None,
    ) -> dict | None:
        """
        Récupère les paroles synchronisées Musixmatch (SOURCE 3, dernier recours).

        Renvoie un dict homogène avec `lrclib_api` :
        {'lyrics_synced', 'lyrics', 'source':'Musixmatch', 'musixmatch_track_id',
        'duration', 'instrumental'} ou None (rien trouvé / désactivé / erreur).
        `album_name` est ignoré (non utilisé par l'endpoint) — présent pour homogénéité
        de signature avec `LRCLIBAPI.get_synced`.
        """
        if not self.enabled or not track_name or not artist_name:
            return None

        # Passe 1, puis retry unique après refresh si échec d'auth (garde-fou #3).
        result = self._try_fetch(track_name, artist_name, duration, force_token=False)
        if result is _AUTH_FAILURE:
            logger.debug("Musixmatch: auth échouée → refresh token + retry")
            result = self._try_fetch(track_name, artist_name, duration, force_token=True)
        return None if result is _AUTH_FAILURE else result

    def _try_fetch(
        self, track_name: str, artist_name: str, duration: float | None, force_token: bool
    ):
        """Une passe complète. Renvoie un dict, None, ou la sentinelle _AUTH_FAILURE."""
        if force_token and self._get_token(force_refresh=True) is None:
            return _AUTH_FAILURE

        params = {
            "q_track": track_name,
            "q_artist": artist_name,
            "namespace": "lyrics_richsynced",
            "optional_calls": "track.richsync",
            "subtitle_format": "lrc",
        }
        # Match serveur par durée réelle quand on la connaît (cohérent projet).
        if duration and duration > 0:
            params["f_subtitle_length"] = str(int(round(duration)))
            params["f_subtitle_length_max_deviation"] = "3"

        status, env = self._api_get("macro.subtitles.get", params, with_token=True)
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

    def get_synced_as_source3(
        self, track_name: str, artist_name: str, duration: float | None = None
    ) -> dict | None:
        """
        Variante prête à brancher dans la branche « LRCLIB ET YTM vides » du pipeline :
        renvoie la forme de `lyrics_sync.compare_synced`
        (`{'lrc','source','confidence','note'}`) ou None. `confidence=1` (source unique,
        dernier recours → candidate à vérification manuelle, même sémantique que le BPM).
        """
        hit = self.get_synced(track_name, artist_name, duration=duration)
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
