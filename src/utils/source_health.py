"""État de santé des sources (bibliothèque, sans GUI).

Deux niveaux de sonde, par source :
  - RAPIDE : une requête HTTP légère (la source répond-elle ?). Toute source en a une.
  - COMPLÈTE : rejoue le vrai chemin fetch+parse sur une **sentinelle** stable et
    vérifie des attendus minimaux → détecte un changement de structure qui casserait
    le pipeline. V1 : seulement les sources requests/JSON (Kworb, LRCLIB, Deezer,
    GetSongBPM, Genius API). Les sources Playwright/login (RIAA, BRMA, SongBPM,
    BPM Finder, scrape Genius, embed Spotify) restent en sonde rapide.

Ce module n'importe RIEN de src.gui (AUDIT §8.4 : cœur pilotable sans interface).
La GUI et la CLI branchent leurs callbacks (`progress_cb`, `should_stop`).

Statuts : ok | degraded | broken | unknown.
Persistance : data/sources_health.json (fusion par clé, jamais d'écrasement des
sources non re-sondées).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime

import requests

from src.config import DATA_DIR, DELAY_BETWEEN_REQUESTS, GENIUS_API_KEY, GETSONGBPM_API_KEY
from src.utils.logger import get_logger

logger = get_logger(__name__)

HEALTH_FILE = DATA_DIR / "sources_health.json"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_FAST_TIMEOUT = 12

# ── Sentinelles (mêmes que scripts/capture_fixtures.py) ────────────────────────
_KWORB_ARTIST_ID = "6dbdXbyAWk2qx8Qttw0knR"  # Josman
_SPOTIFY_TRACK_ID = "4WYhQviUDsXVzLp6oncwJS"  # Josman — Dans le vide
_DEEZER_TRACK_ID = 3135556  # Daft Punk — Harder Better Faster Stronger (id stable Deezer)
_LRCLIB_SENTINEL = {
    "track": "Dans le vide",
    "artist": "Josman",
    "album": "Matrix",
    "duration": 243,
}


# ── Modèles ────────────────────────────────────────────────────────────────────
class ProbeSkipped(Exception):
    """Sonde non exécutable (clé API absente, login requis) → statut unknown."""


@dataclass
class SourceStatus:
    key: str
    label: str
    status: str  # ok | degraded | broken | unknown
    level: str  # fast | full | none
    latency_ms: int | None = None
    last_checked: str | None = None
    last_ok: str | None = None
    message: str = ""


@dataclass
class SourceSpec:
    key: str
    label: str
    fast_url: str | None = None
    fast_marker: str | None = None  # texte attendu dans une réponse 200
    tolerate_403: bool = False  # 403/503 anti-bot → degraded (pas broken)
    fast_probe: Callable[[], list[str]] | None = None  # override du GET par défaut
    full_probe: Callable[[], list[str]] | None = None  # rejoue fetch+parse (sentinelle)
    notes: str = ""


# ── Sondes complètes (imports paresseux : aucun coût à l'import du module) ──────
def _probe_kworb() -> list[str]:
    from src.scrapers.kworb_scraper import KworbScraper

    page = KworbScraper().scrape_songs(_KWORB_ARTIST_ID)
    if not page:
        return ["page songs introuvable (404 / réseau)"]
    if not page.get("entries"):
        return ["0 entrée parsée (structure de table changée ?)"]
    return []


def _probe_lrclib() -> list[str]:
    from src.api.lrclib_api import LRCLIBAPI

    hit = LRCLIBAPI().get_exact(
        _LRCLIB_SENTINEL["track"],
        _LRCLIB_SENTINEL["artist"],
        _LRCLIB_SENTINEL["album"],
        _LRCLIB_SENTINEL["duration"],
    )
    if not hit:
        return ["/get sentinelle sans résultat"]
    if not hit.get("lyrics_synced"):
        return ["résultat sans paroles synchronisées"]
    return []


def _probe_deezer() -> list[str]:
    from src.api.deezer_api import DeezerAPI

    track = DeezerAPI().get_track_by_id(_DEEZER_TRACK_ID)
    if not track:
        return ["track sentinelle introuvable"]
    if not track.get("duration"):
        return ["réponse sans durée (format JSON changé ?)"]
    return []


def _get_getsongbpm_json() -> dict:
    if not GETSONGBPM_API_KEY:
        raise ProbeSkipped("GETSONGBPM_API_KEY absente")
    resp = requests.get(
        "https://api.getsong.co/search/",
        params={
            "api_key": GETSONGBPM_API_KEY,
            "type": "both",
            "lookup": "song:Harder, Better, Faster, Stronger artist:Daft Punk",
            "limit": 5,
        },
        headers={"Accept": "application/json", "User-Agent": _UA},
        timeout=_FAST_TIMEOUT,
    )
    if resp.status_code == 401:
        raise ProbeSkipped("clé GetSongBPM invalide (401)")
    resp.raise_for_status()
    return resp.json()


def _probe_getsongbpm_fast() -> list[str]:
    _get_getsongbpm_json()
    return []


def _probe_getsongbpm_full() -> list[str]:
    data = _get_getsongbpm_json()
    hits = data.get("search")
    if not isinstance(hits, list) or not hits:
        return ["recherche sentinelle sans résultat"]
    songs = [h for h in hits if isinstance(h, dict) and ("tempo" in h or "key_of" in h)]
    if not songs:
        return ["aucun objet 'song' (tempo/key_of) dans la réponse"]
    return []


def _genius_api_search() -> requests.Response:
    if not GENIUS_API_KEY:
        raise ProbeSkipped("GENIUS_API_KEY absente")
    resp = requests.get(
        "https://api.genius.com/search",
        params={"q": "Daft Punk"},
        headers={"Authorization": f"Bearer {GENIUS_API_KEY}"},
        timeout=_FAST_TIMEOUT,
    )
    if resp.status_code == 401:
        raise ProbeSkipped("token Genius invalide (401)")
    resp.raise_for_status()
    return resp


def _probe_genius_api_fast() -> list[str]:
    _genius_api_search()
    return []


def _probe_genius_api_full() -> list[str]:
    data = _genius_api_search().json()
    hits = (data.get("response") or {}).get("hits")
    if not isinstance(hits, list) or not hits:
        return ["recherche sans hits (format API changé ?)"]
    return []


# ── Déclaration des sources ────────────────────────────────────────────────────
SOURCES: list[SourceSpec] = [
    SourceSpec(
        key="kworb",
        label="Kworb (streams Spotify)",
        fast_url="https://kworb.net/spotify/",
        fast_marker="Spotify",
        full_probe=_probe_kworb,
    ),
    SourceSpec(
        key="lrclib",
        label="LRCLIB (paroles synchronisées)",
        fast_url="https://lrclib.net/api/get?track_name=Dans+le+vide"
        "&artist_name=Josman&album_name=Matrix&duration=243",
        fast_marker="syncedLyrics",
        full_probe=_probe_lrclib,
    ),
    SourceSpec(
        key="deezer",
        label="Deezer (durée canonique)",
        fast_url=f"https://api.deezer.com/track/{_DEEZER_TRACK_ID}",
        fast_marker="duration",
        full_probe=_probe_deezer,
    ),
    SourceSpec(
        key="getsongbpm",
        label="GetSongBPM (BPM/key)",
        fast_probe=_probe_getsongbpm_fast,
        full_probe=_probe_getsongbpm_full,
        notes="clé GETSONGBPM_API_KEY requise",
    ),
    SourceSpec(
        key="genius_api",
        label="Genius API (liste morceaux)",
        fast_probe=_probe_genius_api_fast,
        full_probe=_probe_genius_api_full,
        notes="token GENIUS_API_KEY requis",
    ),
    SourceSpec(
        key="genius_scrape",
        label="Genius (scrape crédits/paroles)",
        fast_url="https://genius.com/",
        tolerate_403=True,
        notes="crédits/paroles via Playwright + llama3.2 ; 403 sur requests = normal",
    ),
    SourceSpec(
        key="spotify_embed",
        label="Spotify embed (Track ID artistes)",
        fast_url=f"https://open.spotify.com/embed/track/{_SPOTIFY_TRACK_ID}",
        fast_marker="__NEXT_DATA__",
    ),
    SourceSpec(
        key="riaa",
        label="RIAA (certifications US)",
        fast_url="https://www.riaa.com/gold-platinum/",
        tolerate_403=True,
        notes="scrape patchright (Cloudflare laxiste)",
    ),
    SourceSpec(
        key="brma",
        label="BRMA / Ultratop (certifications BE)",
        fast_url="https://www.ultratop.be/fr/or-platine/2024/singles",
        tolerate_403=True,
        notes="Cloudflare STRICT : scrape via route CDP (vrai Chrome)",
    ),
    SourceSpec(
        key="songbpm",
        label="SongBPM (scrape BPM/key)",
        fast_url="https://songbpm.com/",
        tolerate_403=True,
        notes="dernier recours BPM ; Cloudflare possible",
    ),
    SourceSpec(
        key="bpmfinder",
        label="BPM Finder (audioaidynamics)",
        fast_url="https://audioaidynamics.com",
        notes="login requis (BPMFINDER_EMAIL/PASSWORD) — sonde complète manuelle",
    ),
]

SOURCES_BY_KEY = {s.key: s for s in SOURCES}


# ── Exécution des sondes ───────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _run_probe(spec: SourceSpec, probe: Callable[[], list[str]], level: str) -> SourceStatus:
    """Exécute une sonde-callable (contrat : [] = ok, [anomalies] = broken)."""
    start = time.monotonic()
    try:
        anomalies = probe()
    except ProbeSkipped as e:
        return SourceStatus(spec.key, spec.label, "unknown", "none", None, _now(), None, str(e))
    except requests.RequestException as e:
        latency = int((time.monotonic() - start) * 1000)
        return SourceStatus(
            spec.key, spec.label, "broken", level, latency, _now(), None, f"réseau : {e}"
        )
    except Exception as e:  # une sonde qui plante = source cassée, pas un crash de l'app
        latency = int((time.monotonic() - start) * 1000)
        return SourceStatus(
            spec.key, spec.label, "broken", level, latency, _now(), None, f"erreur : {e}"
        )

    latency = int((time.monotonic() - start) * 1000)
    if anomalies:
        return SourceStatus(
            spec.key, spec.label, "broken", level, latency, _now(), None, " ; ".join(anomalies)
        )
    return SourceStatus(spec.key, spec.label, "ok", level, latency, _now(), _now(), "OK")


def _run_fast_get(spec: SourceSpec) -> SourceStatus:
    """Sonde rapide par défaut : GET léger + vérif de marqueur."""
    start = time.monotonic()
    try:
        resp = requests.get(spec.fast_url, headers={"User-Agent": _UA}, timeout=_FAST_TIMEOUT)
    except requests.RequestException as e:
        latency = int((time.monotonic() - start) * 1000)
        return SourceStatus(
            spec.key, spec.label, "broken", "fast", latency, _now(), None, f"injoignable : {e}"
        )

    latency = int((time.monotonic() - start) * 1000)
    code = resp.status_code
    if code in (403, 503) and spec.tolerate_403:
        return SourceStatus(
            spec.key,
            spec.label,
            "degraded",
            "fast",
            latency,
            _now(),
            None,
            f"anti-bot HTTP {code} (attendu — sonde complète via le scraper)",
        )
    if code != 200:
        return SourceStatus(
            spec.key, spec.label, "broken", "fast", latency, _now(), None, f"HTTP {code}"
        )
    if spec.fast_marker and spec.fast_marker not in resp.text:
        return SourceStatus(
            spec.key,
            spec.label,
            "degraded",
            "fast",
            latency,
            _now(),
            None,
            f"200 mais '{spec.fast_marker}' absent (contenu inattendu)",
        )
    return SourceStatus(spec.key, spec.label, "ok", "fast", latency, _now(), _now(), "OK")


def check_fast(spec: SourceSpec) -> SourceStatus:
    """Sonde rapide : joignabilité (+ marqueur). Ne valide PAS le parsing."""
    if spec.fast_probe is not None:
        return _run_probe(spec, spec.fast_probe, "fast")
    if spec.fast_url:
        return _run_fast_get(spec)
    return SourceStatus(
        spec.key, spec.label, "unknown", "none", None, _now(), None, "aucune sonde rapide"
    )


def check_full(spec: SourceSpec) -> SourceStatus:
    """Sonde complète : rejoue fetch+parse. Retombe sur la rapide si non implémentée."""
    if spec.full_probe is not None:
        return _run_probe(spec, spec.full_probe, "full")
    status = check_fast(spec)
    if not status.message.endswith("(rapide seulement)"):
        note = spec.notes or "sonde complète non implémentée"
        status.message = f"{status.message} — {note} (rapide seulement)"
    return status


def check_all(
    level: str = "fast",
    only: list[str] | None = None,
    progress_cb: Callable[[SourceStatus], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[SourceStatus]:
    """Sonde toutes les sources (ou `only`). `progress_cb` appelé après chacune ;
    `should_stop` testé ENTRE deux sources (arrêt propre depuis la GUI)."""
    specs = [SOURCES_BY_KEY[k] for k in only if k in SOURCES_BY_KEY] if only else SOURCES
    results: list[SourceStatus] = []
    for i, spec in enumerate(specs):
        if should_stop and should_stop():
            logger.info("check_all : arrêt demandé")
            break
        if i:
            time.sleep(DELAY_BETWEEN_REQUESTS)
        status = check_full(spec) if level == "full" else check_fast(spec)
        results.append(status)
        if progress_cb:
            progress_cb(status)
    return results


# ── Persistance ────────────────────────────────────────────────────────────────
def load_health() -> dict[str, dict]:
    """Dernier état connu par clé de source (dict brut), ou {} si absent."""
    if not HEALTH_FILE.exists():
        return {}
    try:
        return json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        logger.warning(f"sources_health.json illisible : {e}")
        return {}


def save_health(statuses: list[SourceStatus]) -> None:
    """Fusionne les statuts fournis dans le fichier (préserve les sources absentes,
    et le dernier last_ok connu si la source n'est plus OK)."""
    existing = load_health()
    for st in statuses:
        prev = existing.get(st.key, {})
        data = asdict(st)
        if st.last_ok is None and prev.get("last_ok"):
            data["last_ok"] = prev["last_ok"]  # garder la dernière réussite connue
        existing[st.key] = data
    HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_FILE.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ── Procédure de casse (résumé affiché dans la GUI) ────────────────────────────
BREAKAGE_PROCEDURE = (
    "Une source est cassée (broken) ? Distinguer panne réseau et changement de structure :\n"
    "  1. python scripts/check_sources_health.py --full --only <source>\n"
    "     → 'injoignable/HTTP' = site down (attendre) ; '0 entrée/format changé' = structure.\n"
    "  2. Changement de structure → re-capturer la fixture :\n"
    "     python scripts/capture_fixtures.py --only <source>\n"
    "  3. python -m pytest tests/test_<source>_fixtures.py -v\n"
    "     → le test rouge localise le parseur cassé.\n"
    "  4. Réparer le parseur jusqu'au vert, puis re-sonder en complet.\n"
    "Détails : docs/maintenance-sources.md"
)
