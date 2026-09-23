"""
Client API Musixmatch (endpoint non officiel des lecteurs) — paroles synchronisées.

Rôle dans le projet : **SOURCE 3** des timestamps, en **dernier recours gated**, uniquement
quand LRCLIB (source 1) ET YouTube Music (source 2) échouent tous les deux. Ne JAMAIS
l'utiliser en source primaire de batch (cf. gestion du rate limit ci-dessous).

Mécanisme (reverse-engineering des lecteurs Musixmatch, popularisé par syncedlyrics /
YTubic / navidrome-musixmatch-plugin) :
  - L'API officielle exige un contrat payant. Les lecteurs, eux, tapent `/ws/1.1` avec un
    `usertoken` de session obtenu gratuitement via `token.get`.
  - **Client retenu : iOS** (`mac-ios-v2.0` sur `apic.musixmatch.com`, réglable via
    `settings.musixmatch_app_id` / `musixmatch_api_base`). Mesuré le 2026-09-23 : le client
    desktop (`web-desktop-app-v1.0` sur `apic-desktop`, celui de tous les projets de
    référence) ne rend plus que le jeton LEURRE sur notre IP ; le client iOS rend un vrai
    jeton, le bon morceau et un vrai LRC ; `android-player-v1.0` répond 401 `captcha`.
  - Un seul appel `macro.subtitles.get` renvoie d'un coup : le morceau apparié
    (`matcher.track.get`), le LRC ligne-à-ligne (`track.subtitles.get`), le richsync
    mot-à-mot (`track.richsync.get`) et le texte brut (`track.lyrics.get`). On
    privilégie l'appel unique pour minimiser le nombre de requêtes.

Gardes-fous — la partie non triviale :
  1. **Cache du jeton, lié à son client** : mémoire + fichier (`data/.musixmatch_token.json`),
     avec une TTL prudente. Le fichier porte l'`app_id` qui a obtenu le jeton : un jeton d'un
     AUTRE client est ignoré, car c'est précisément ce croisement qui déclenche le contenu
     leurre (garde-fou 6).
  2. **Token factice** : quand l'IP est bridée, `token.get` répond 200 avec un jeton de forme
     valide mais inutilisable — « UpgradeOnly », ou un seul caractère répété (56 zéros
     observés le 2026-09-05). `_is_degenerate_token` les rejette AVANT usage ET avant mise en
     cache : accepté, un leurre empoisonne le cache pour toute la durée du TTL.
  3. **Retry 401** : un token périmé est rejeté au niveau HTTP (401/403), dans l'enveloppe
     racine (`message.header.status_code`) **ou dans un SOUS-APPEL macro** (cf.
     `_macro_auth_failed`). On invalide le jeton et on réessaie une fois — si le plancher
     de `token.get` le permet (garde-fou 5).
  4. **Dégradation propre** : toute erreur (réseau, parsing, blocage) renvoie None sans
     jamais lever — c'est une source facultative, elle ne doit jamais casser le pipeline.
     Mais chaque cas garde SON verdict : un transport en échec n'est pas une absence, une
     réponse illisible est un `parse`, un 401 `captcha` est un `blocked`, pas un `auth`.
  5. **Économie de `token.get`** — l'appel que Musixmatch bride, et par sa FRÉQUENCE (un
     second appel dans la minute : 401 `captcha`, mesuré le 2026-09-23) :
     - un **plancher** entre deux `token.get` (`settings.musixmatch_token_get_min_interval_s`),
       horloge écrite dans le fichier du jeton AVANT l'appel : il vaut pour tous les
       process et survit à une relance. Sous le plancher, l'appel est `skipped` ;
     - une **fenêtre de repos** (`settings.musixmatch_token_cooldown_s`, 2026-09-06) quand
       `token.get` ne rend plus de jeton utilisable : on cesse d'interroger, un seul
       WARNING, reprise automatique — jamais de disjoncteur définitif. Elle aussi est
       écrite dans le fichier : une relance de la CLI ne la remet plus à zéro.
  6. **Contenu leurre** (2026-09-23) : l'endpoint, interrogé avec un jeton qui n'est pas le
     sien, répond 200 PARTOUT avec un faux morceau (« NOKIA » de Drake, track_id
     226291677, LRC de charabia) quelle que soit la requête. Rejeté par le contrôle de
     titre, il ressortait en `absent` — un blocage déguisé en « pas de données ». Il est
     reconnu par son identifiant (`_LEURRES_CONNUS`) ou, s'il changeait, par un même
     `track_id` rendu pour deux titres sans rapport ; verdict `blocked` + repos. Le
     contrôle de titre ne suffit pas : « Drake – NOKIA » le passerait.
  7. **Mémoire des absences** : un morceau pour lequel Musixmatch a répondu sans synchro
     n'est pas redemandé avant `settings.musixmatch_absent_retry_days` (fichier
     `data/.musixmatch_absents.json`, même patron que le cache négatif ReccoBeats). Mesuré
     sur 30 candidats : 2 synchros, les deux tiers ne sont pas du tout chez Musixmatch.

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
Token épinglé optionnel : `MUSIXMATCH_USER_TOKEN` (env) amorce le cache (il doit venir du
MÊME client que `musixmatch_app_id`) ; en cas d'échec d'auth, on retombe sur `token.get`.
"""

import asyncio
import json
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
        MUSIXMATCH_ABSENT_RETRY_DAYS,
        MUSIXMATCH_API_BASE,
        MUSIXMATCH_APP_ID,
        MUSIXMATCH_TOKEN_COOLDOWN_S,
        MUSIXMATCH_TOKEN_GET_MIN_INTERVAL_S,
    )
except ImportError:  # exécution hors package (tests standalone)
    DELAY_BETWEEN_REQUESTS, MAX_RETRIES = 1, 3
    MUSIXMATCH_TOKEN_COOLDOWN_S = 600
    MUSIXMATCH_APP_ID = "mac-ios-v2.0"
    MUSIXMATCH_API_BASE = "https://apic.musixmatch.com/ws/1.1"
    MUSIXMATCH_TOKEN_GET_MIN_INTERVAL_S = 300
    MUSIXMATCH_ABSENT_RETRY_DAYS = 30
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
from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Clé de `source_health.SOURCES` sous laquelle cet usage est compté.
_SOURCE = "musixmatch"

# ── Constantes du client ────────────────────────────────────────────────────────
_API_BASE = MUSIXMATCH_API_BASE
_APP_ID = MUSIXMATCH_APP_ID
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
# TTL locale prudente (calibrée sur l'ancien client desktop, ~10 min côté serveur).
# Le jeton iOS vit plus longtemps (> 10 min mesuré) : un 401 déclenche de toute
# façon le rafraîchissement. Mesurer avant d'allonger (WIP, fiabilisation).
_TOKEN_TTL = 9 * 60  # secondes
_TOKEN_FILE = Path(DATA_DIR) / ".musixmatch_token.json"
_ABSENTS_FILENAME = ".musixmatch_absents.json"

_STATUS_OK = 200
_STATUS_AUTH = 401  # blocage token / CAPTCHA-gate

# Seuils de validation du morceau apparié (mêmes ordres de grandeur que lrclib_api).
_TITLE_MATCH_MIN = 0.72
_ARTIST_MATCH_MIN = 0.55

#: Morceaux servis en CONTENU LEURRE, quelle que soit la requête (garde-fou 6).
#: Mesuré le 2026-09-23 sur 6 requêtes variées : toujours « NOKIA » de Drake.
_LEURRES_CONNUS = frozenset({226291677})

# Sentinelles internes de `_try_fetch_async` (chacune porte SON verdict).
_AUTH_FAILURE = object()  # jeton refusé → invalidation + retry
_EN_ATTENTE = object()  # pas de jeton, et le plancher de token.get interdit d'en demander
_LEURRE = object()  # contenu leurre servi (garde-fou 6)
_ILLISIBLE = object()  # 200 dont la structure n'est pas celle attendue
_INDETERMINE = object()  # transport en échec : les tentatives parlent, pas nous


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


def _cle_absence(track_name: str, artist_name: str) -> str:
    """Clé de la mémoire des absences : la REQUÊTE, pas le morceau en base."""
    return f"{_norm(artist_name)}::{_norm(track_name)}"


class MusixmatchAPI:
    """Client lecture seule pour l'endpoint Musixmatch (paroles synchronisées)."""

    def __init__(
        self, timeout: int = 12, token_file: Path | None = None, enabled: bool | None = None
    ):
        self.timeout = timeout
        self.token_file = Path(token_file) if token_file else _TOKEN_FILE
        # La mémoire des absences vit à côté du jeton : un `token_file` de test
        # l'entraîne dans le même dossier temporaire.
        self.absents_file = self.token_file.with_name(_ABSENTS_FILENAME)
        # Coupe-circuit global (env prioritaire, argument explicite sinon).
        if enabled is None:
            enabled = os.getenv("MUSIXMATCH_ENABLED", "true").strip().lower() != "false"
        self.enabled = enabled

        # Cache token en mémoire : (token, obtained_at_epoch).
        self._token: str | None = None
        self._token_ts: float = 0.0

        # Fenêtre de repos : instant (epoch) avant lequel on n'interroge plus.
        self._repos_jusqua: float = 0.0
        # Nature du dernier refus de jeton : un 401 `captcha` est un BLOCAGE ;
        # None = token.get n'a pas abouti côté transport (ce n'est pas un refus).
        self._dernier_refus: IssueKind | None = IssueKind.AUTH
        # Détecteur générique de leurre : track_id apparié → titre demandé.
        self._titre_par_track_id: dict[int, str] = {}
        # Mémoire des absences (chargée paresseusement).
        self._absents: dict[str, float] | None = None

        # Token épinglé optionnel : amorce le cache, expiry gérée normalement.
        pinned = (os.getenv("MUSIXMATCH_USER_TOKEN") or "").strip()
        if pinned and not _is_degenerate_token(pinned):
            self._token, self._token_ts = pinned, time.time()

    # ── État persistant (jeton + horloges) ──────────────────────────────────────
    def _lire_etat(self) -> dict:
        try:
            if self.token_file.exists():
                data = json.loads(self.token_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError) as e:  # cache corrompu → on l'ignore
            logger.debug(f"Musixmatch: état illisible ({e})")
        return {}

    def _ecrire_etat(self, **maj) -> None:
        """Fusionne `maj` dans le fichier d'état (jamais d'écrasement des horloges)."""
        etat = self._lire_etat()
        etat.update(maj)
        try:
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.write_text(json.dumps(etat), encoding="utf-8")
        except OSError as e:  # échec disque non bloquant (copie mémoire conservée)
            logger.debug(f"Musixmatch: écriture de l'état impossible ({e})")

    # ── Fenêtre de repos ────────────────────────────────────────────────────────
    def _au_repos(self) -> bool:
        """La source est-elle en repos ? Journalise la reprise, une seule fois.

        La fin de fenêtre est relue dans le fichier : une fenêtre armée par un
        AUTRE process (ou avant une relance) vaut ici aussi.
        """
        try:
            fichier = float(self._lire_etat().get("repos_jusqua") or 0.0)
        except (TypeError, ValueError):
            fichier = 0.0
        fin = max(self._repos_jusqua, fichier)
        if not fin:
            return False
        if time.time() < fin:
            self._repos_jusqua = fin
            return True
        if self._repos_jusqua:
            logger.info("Musixmatch : fin de la fenêtre de repos, on retente.")
        self._repos_jusqua = 0.0
        return False

    def _mettre_au_repos(self, raison: str) -> None:
        """Arme la fenêtre. Un SEUL warning par fenêtre, pas un par morceau."""
        duree = MUSIXMATCH_TOKEN_COOLDOWN_S
        if duree <= 0:
            return
        deja_armee = self._repos_jusqua > time.time()
        self._repos_jusqua = time.time() + duree
        self._ecrire_etat(repos_jusqua=self._repos_jusqua)
        if not deja_armee:
            logger.warning(
                f"Musixmatch : {raison} — source mise au repos {duree} s "
                "(insister sur une IP bridée ne fait que l'aggraver)."
            )

    # ── Gestion du token ────────────────────────────────────────────────────────
    def _load_cached_token(self) -> str | None:
        """Token encore valide (mémoire puis fichier), sinon None.

        Un jeton du fichier n'est repris que s'il a été obtenu par le MÊME client
        (`app_id`) : envoyé à un autre, il fait servir le contenu leurre.
        """
        now = time.time()
        if self._token and (now - self._token_ts) < _TOKEN_TTL:
            return self._token
        data = self._lire_etat()
        tok = data.get("token")
        try:
            ts = float(data.get("obtained_at") or 0)
        except (TypeError, ValueError):
            return None
        if (
            tok
            and data.get("app_id") == _APP_ID
            and not _is_degenerate_token(tok)
            and (now - ts) < _TOKEN_TTL
        ):
            self._token, self._token_ts = tok, ts
            return tok
        return None

    def _save_token(self, token: str) -> None:
        self._token, self._token_ts = token, time.time()
        self._ecrire_etat(token=token, obtained_at=self._token_ts, app_id=_APP_ID)

    def _invalidate_token(self) -> None:
        """Oublie le jeton — mais PAS les horloges du fichier (plancher, repos)."""
        self._token, self._token_ts = None, 0.0
        if self._lire_etat().get("token"):
            self._ecrire_etat(token=None, obtained_at=0)

    def _token_get_autorise(self) -> bool:
        """Le plancher entre deux `token.get` est-il franchi (tous process confondus) ?"""
        try:
            dernier = float(self._lire_etat().get("last_token_get") or 0.0)
        except (TypeError, ValueError):
            dernier = 0.0
        return time.time() - dernier >= MUSIXMATCH_TOKEN_GET_MIN_INTERVAL_S

    # ── Mémoire des absences ────────────────────────────────────────────────────
    def _charger_absents(self) -> dict[str, float]:
        if self._absents is None:
            self._absents = {}
            try:
                if self.absents_file.exists():
                    data = json.loads(self.absents_file.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        self._absents = data
            except (OSError, ValueError) as e:
                logger.debug(f"Musixmatch: mémoire des absences illisible ({e})")
        return self._absents

    def _absence_recente(self, track_name: str, artist_name: str) -> bool:
        """Absence déjà constatée et pas encore périmée ?

        Un horodatage illisible compte comme PÉRIMÉ : mieux vaut redemander que
        figer une absence sur une donnée douteuse (règle du cache ReccoBeats).
        """
        vu = self._charger_absents().get(_cle_absence(track_name, artist_name))
        try:
            age = time.time() - float(vu)
        except (TypeError, ValueError):
            return False
        return 0 <= age < MUSIXMATCH_ABSENT_RETRY_DAYS * 86_400

    def _noter_absence(self, track_name: str, artist_name: str) -> None:
        absents = self._charger_absents()
        absents[_cle_absence(track_name, artist_name)] = time.time()
        try:
            self.absents_file.parent.mkdir(parents=True, exist_ok=True)
            self.absents_file.write_text(json.dumps(absents), encoding="utf-8")
        except OSError as e:
            logger.debug(f"Musixmatch: écriture de la mémoire des absences impossible ({e})")

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
        return MusixmatchAPI._sous_appel_refuse(calls) is not None

    @staticmethod
    def _sous_appel_refuse(calls: dict[str, dict]) -> dict | None:
        """Header du premier sous-appel en 401, sinon None."""
        for call in calls.values():
            if not isinstance(call, dict):
                continue
            header = ((call.get("message") or {}).get("header")) or {}
            if header.get("status_code") == _STATUS_AUTH:
                return header
        return None

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

    def _est_leurre(self, track: dict | None, q_track: str, q_artist: str) -> bool:
        """Le morceau apparié est-il le CONTENU LEURRE (garde-fou 6) ?

        Deux signes : l'identifiant du leurre mesuré, ou — si Musixmatch en
        changeait — un même `track_id` déjà rendu pour un titre sans rapport ET
        rejeté par le contrôle de match. La seconde condition protège le cas
        normal d'un vrai morceau rendu pour deux graphies d'un même titre.
        """
        if not track:
            return False
        tid = track.get("track_id")
        if tid in _LEURRES_CONNUS:
            return True
        if tid is None:
            return False
        deja = self._titre_par_track_id.get(tid)
        if (
            deja is not None
            and _title_match(deja, q_track) < _TITLE_MATCH_MIN
            and not self._verify_match(track, q_track, q_artist)
        ):
            return True
        self._titre_par_track_id.setdefault(tid, q_track)
        return False

    # ── HTTP (AsyncHttpSession partagée) ─────────────────────────────────────────
    # Les I/O fichier du cache token et de l'état restent sync dans la coroutine
    # (~1 ms, hors boucle chaude) — assumé : la paire `_token`/`_token_ts` reste
    # ainsi cohérente sans verrou, faute de point d'entrelacement.
    async def _api_get_async(
        self, http: "AsyncHttpSession", action: str, params: dict
    ) -> tuple[int | None, dict | None]:
        """GET sur l'endpoint (retries 5xx/429, sleep non bloquant).

        Rend `(200, env)`, `(200, None)` sur corps illisible, `(401, None)` sur
        refus HTTP, `(None, None)` quand le transport a échoué après retries.
        """
        q = dict(params)
        q["app_id"] = _APP_ID
        q["format"] = "json"
        q["t"] = str(int(time.time() * 1000))

        url = f"{_API_BASE}/{action}"
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                r = await http.get(url, params=q, headers=_ASYNC_HEADERS, timeout=self.timeout)
                if r.status_code == _STATUS_OK:
                    try:
                        return _STATUS_OK, r.json()
                    except ValueError as e:
                        logger.debug(f"Musixmatch {action} : JSON invalide ({e})")
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

    def _noter_refus(self, header: dict | None) -> None:
        """Retient la nature d'un refus : `captcha` = blocage, sinon auth."""
        hint = (header or {}).get("hint")
        self._dernier_refus = IssueKind.BLOCKED if hint == "captcha" else IssueKind.AUTH

    async def _fetch_new_token_async(self, http: "AsyncHttpSession") -> str | None:
        """Demande un jeton neuf (plancher, gardes-fous #2, mise en cache).

        L'horloge du plancher est écrite AVANT l'appel : c'est la TENTATIVE qui
        consomme le budget de Musixmatch, pas son succès.
        """
        if not self._token_get_autorise():
            logger.debug("Musixmatch: token.get différé (plancher entre deux demandes)")
            return None
        self._ecrire_etat(last_token_get=time.time())
        self._dernier_refus = IssueKind.AUTH
        status, env = await self._api_get_async(http, "token.get", {"user_language": "en"})
        if status == _STATUS_AUTH or _envelope_status(env) == _STATUS_AUTH:
            header = ((env or {}).get("message") or {}).get("header")
            self._noter_refus(header)
            hint = (header or {}).get("hint")
            self._mettre_au_repos(f"401 sur token.get{f' ({hint})' if hint else ''}")
            return None
        if env is None:
            # Transport en échec (ou corps illisible) : ce n'est pas un refus.
            self._dernier_refus = None
            return None
        token = ((env.get("message") or {}).get("body") or {}).get("user_token") or ""
        # Garde-fou #2 : token de forme valide mais inutilisable.
        if _is_degenerate_token(token):
            logger.debug(f"Musixmatch: token factice rejeté ({token[:12]!r}…, IP restreinte)")
            self._dernier_refus = IssueKind.BLOCKED
            self._mettre_au_repos(f"jeton factice {token[:12]!r}…")
            return None
        self._save_token(token)
        logger.debug(f"Musixmatch: nouveau usertoken obtenu ({_APP_ID})")
        return token

    # ── Point d'entrée ────────────────────────────────────────────────────────────
    async def get_synced_async(
        self,
        http: "AsyncHttpSession",
        track_name: str,
        artist_name: str,
        duration: float | None = None,
        album_name: str | None = None,
    ) -> dict | None:
        """Paroles synchronisées d'un morceau (retry unique après refresh du jeton)."""
        if not self.enabled or not track_name or not artist_name:
            return None
        # Le retry après refresh de token est une SECONDE tentative du même appel
        # logique : une observation les couvre toutes deux, un seul verdict.
        with source_usage.observe(_SOURCE, label=f"{artist_name} — {track_name}") as obs:
            if self._au_repos():
                obs.skipped("fenêtre de repos (jeton refusé)")
                return None
            if self._absence_recente(track_name, artist_name):
                obs.skipped(f"absence constatée il y a < {MUSIXMATCH_ABSENT_RETRY_DAYS} j")
                return None
            result = await self._try_fetch_async(http, track_name, artist_name, duration, False)
            if result is _AUTH_FAILURE:
                logger.debug("Musixmatch: auth échouée → refresh token + retry")
                result = await self._try_fetch_async(http, track_name, artist_name, duration, True)
                if result is _EN_ATTENTE:
                    # Le jeton vient d'être refusé : ce n'est pas une simple attente.
                    result = _AUTH_FAILURE
            if result is _AUTH_FAILURE:
                obs.fail(self._dernier_refus or IssueKind.AUTH, "token refusé même après refresh")
                return None
            if result is _EN_ATTENTE:
                obs.skipped("token.get différé (plancher entre deux demandes)")
                return None
            if result is _LEURRE:
                obs.fail(IssueKind.BLOCKED, "contenu leurre (jeton d'un autre client ?)")
                return None
            if result is _ILLISIBLE:
                obs.parse_error("réponse macro sans la structure attendue")
                return None
            if result is _INDETERMINE:
                return None  # les tentatives du transport font le verdict
            if not result or not result.get("lyrics_synced"):
                # Musixmatch a répondu, sans synchro : une vraie absence, mémorisée.
                self._noter_absence(track_name, artist_name)
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
        """Un appel `macro.subtitles.get` ; rend un dict, None (absent) ou une sentinelle."""
        if force_token:
            self._invalidate_token()
        tok = self._load_cached_token()
        if tok is None:
            if not self._token_get_autorise():
                return _EN_ATTENTE
            tok = await self._fetch_new_token_async(http)
            if tok is None:
                # `_dernier_refus` à None : token.get n'a pas abouti côté transport.
                return _INDETERMINE if self._dernier_refus is None else _AUTH_FAILURE

        params = {
            "q_track": track_name,
            "q_artist": artist_name,
            "usertoken": tok,
            "namespace": "lyrics_richsynced",
            "optional_calls": "track.richsync",
            "subtitle_format": "lrc",
        }
        if duration and duration > 0:
            params["f_subtitle_length"] = str(int(round(duration)))
            params["f_subtitle_length_max_deviation"] = "3"

        status, env = await self._api_get_async(http, "macro.subtitles.get", params)
        if status == _STATUS_AUTH:
            self._dernier_refus = IssueKind.AUTH
            self._invalidate_token()
            return _AUTH_FAILURE
        if env is None:
            return _ILLISIBLE if status == _STATUS_OK else _INDETERMINE
        if _envelope_status(env) == _STATUS_AUTH:
            self._noter_refus((env.get("message") or {}).get("header"))
            self._invalidate_token()
            return _AUTH_FAILURE

        calls = self._macro_calls(env)
        if not calls:
            # Un 200 racine sans sous-appels n'est pas la forme d'une absence
            # (qui porte un `matcher.track.get` en 404) : la structure a changé.
            return _ILLISIBLE if _envelope_status(env) == _STATUS_OK else None
        refus = self._sous_appel_refuse(calls)
        if refus is not None:
            self._noter_refus(refus)
            self._invalidate_token()
            return _AUTH_FAILURE

        track = self._matched_track(calls)
        if self._est_leurre(track, track_name, artist_name):
            logger.debug("Musixmatch: contenu leurre (track_id=%s)", (track or {}).get("track_id"))
            self._invalidate_token()
            self._mettre_au_repos("contenu leurre servi")
            return _LEURRE
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
        """Forme `compare_synced` (conf=1 : source unique, dernier recours)."""
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
