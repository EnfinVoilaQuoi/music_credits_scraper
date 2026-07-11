"""
Capture des fixtures HTML/JSON pour les tests de parsers (tests/fixtures/).

Chaque entrée de CAPTURES enregistre une page réelle + un sidecar `.meta.json`
(url, date, méthode). Les tests `tests/test_*_fixtures.py` rejouent ensuite les
parsers sur ces fichiers, hors ligne. C'est la brique centrale de la procédure
en cas de casse d'une source (docs/maintenance-sources.md) : re-capturer ici,
puis `python -m pytest tests/test_<source>_fixtures.py -v` — le test rouge
localise le parseur cassé.

Usage :
    python scripts/capture_fixtures.py --list
    python scripts/capture_fixtures.py --all
    python scripts/capture_fixtures.py --only kworb,genius_song_page
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

import requests

if "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.config import DELAY_BETWEEN_REQUESTS

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ── Sentinelles : pages stables, artistes du corpus quand c'est possible ───────
_KWORB_ARTIST_ID = "6dbdXbyAWk2qx8Qttw0knR"  # Josman
_SPOTIFY_TRACK_ID = "4WYhQviUDsXVzLp6oncwJS"  # Josman — Dans le vide
_GENIUS_SONG_URL = "https://genius.com/Josman-dans-le-vide-lyrics"
_RIAA_ARTIST = "Daft Punk"  # catalogue RIAA stable et court (pas de rap FR chez RIAA)
_LRCLIB_PARAMS = {  # /get exact : Josman — Dans le vide (album Matrix, 243 s en base)
    "track_name": "Dans le vide",
    "artist_name": "Josman",
    "album_name": "Matrix",
    "duration": 243,
}
_GETSONGBPM_LOOKUP = "song:Harder, Better, Faster, Stronger artist:Daft Punk"

CAPTURES: list[dict] = [
    {
        "name": "kworb_artist_songs",
        "path": "kworb/artist_songs.html",
        "url": f"https://kworb.net/spotify/artist/{_KWORB_ARTIST_ID}_songs.html",
        "method": "requests",
    },
    {
        "name": "spotify_embed_track",
        "path": "spotify_embed/track.html",
        "url": f"https://open.spotify.com/embed/track/{_SPOTIFY_TRACK_ID}",
        "method": "requests",
        "fallback": "playwright",
    },
    {
        "name": "genius_song_page",
        "path": "genius/song_page.html",
        "url": _GENIUS_SONG_URL,
        "method": "requests",
        "fallback": "playwright",
    },
    {
        "name": "riaa_search",
        "path": "riaa/search_results.html",
        "url": (
            "https://www.riaa.com/gold-platinum/?tab_active=default-award"
            f"&ar={quote(_RIAA_ARTIST)}&ti=&lab=&genre=&format=&date_option="
            "&from=&to=&award=&type=&category=&adv=SEARCH#search_section"
        ),
        "method": "riaa",
    },
    {
        "name": "brma_year",
        "path": "brma/ultratop_2021_singles.html",
        "url": "https://www.ultratop.be/fr/or-platine/2021/singles",
        "method": "ultratop",
        "params": {"year": 2021, "category": "singles"},
    },
    {
        "name": "lrclib_get",
        "path": "lrclib/get_exact.json",
        "url": f"https://lrclib.net/api/get?{urlencode(_LRCLIB_PARAMS)}",
        "method": "requests",
    },
    {
        "name": "getsongbpm_search",
        "path": "getsongbpm/search.json",
        "url": None,  # construite à la volée avec GETSONGBPM_API_KEY (jamais stockée)
        "method": "getsongbpm",
    },
]


# ── Méthodes de fetch ──────────────────────────────────────────────────────────
def _fetch_requests(url: str) -> str | None:
    try:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=30)
        if resp.status_code != 200:
            print(f"   HTTP {resp.status_code}")
            return None
        # Tous les sites capturés servent de l'UTF-8 (piège kworb : pas de
        # charset dans le header → requests retombe en latin-1 et mojibake).
        resp.encoding = "utf-8"
        return resp.text
    except requests.RequestException as e:
        print(f"   erreur réseau : {e}")
        return None


def _fetch_playwright(url: str) -> str | None:
    from src.scrapers.playwright_manager import get_playwright

    pw = get_playwright()
    browser = pw.chromium.launch(headless=True)
    try:
        page = browser.new_page(user_agent=_UA)
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(2_000)
        return page.content()
    except Exception as e:
        print(f"   erreur Playwright : {e}")
        return None
    finally:
        browser.close()


def _fetch_riaa(url: str) -> str | None:
    """RIAA est derrière Cloudflare : on réutilise le rendu patchright du scraper
    (get_details=True pour capturer aussi l'historique MORE DETAILS)."""
    from src.scrapers.riaa_scraper_v2 import RIAAScraperV2

    return RIAAScraperV2(headless=True)._render(url, load_all=True, get_details=True)


def _fetch_ultratop(params: dict) -> str | None:
    """Ultratop = Cloudflare strict : tout navigateur d'automation BOUCLE sur le
    challenge, même en fenêtre visible (piège documenté, JOURNAL 2026-06-29).
    Seule la route CDP passe : vrai Chrome lancé hors automation, auquel
    patchright s'attache."""
    from src.scrapers.cdp_chrome import ensure_cdp_chrome

    cdp_url = ensure_cdp_chrome()
    if not cdp_url:
        print("   Chrome introuvable — route CDP obligatoire pour Ultratop (cf. JOURNAL)")
        return None
    os.environ["GENIUS_CDP_URL"] = cdp_url  # lu à l'import de crawl4ai_scraper_base
    import src.scrapers.crawl4ai_scraper_base as cf_base

    cf_base._CDP_URL = cdp_url  # au cas où le module serait déjà importé
    from src.scrapers.ultratop_fetch import fetch_ultratop_html

    return fetch_ultratop_html(params["year"], params["category"])


def _fetch_getsongbpm() -> str | None:
    api_key = os.getenv("GETSONGBPM_API_KEY")
    if not api_key:
        print("   GETSONGBPM_API_KEY absente → capture sautée")
        return None
    params = {"api_key": api_key, "type": "both", "lookup": _GETSONGBPM_LOOKUP, "limit": 5}
    try:
        resp = requests.get(
            "https://api.getsong.co/search/",
            params=params,
            timeout=15,
            headers={"Accept": "application/json", "User-Agent": _UA},
        )
        if resp.status_code != 200:
            print(f"   HTTP {resp.status_code}")
            return None
        data = resp.json()
        if not isinstance(data.get("search"), list) or not data["search"]:
            print(f"   réponse sans résultats : {str(data)[:200]}")
            return None
        return json.dumps(data, indent=2, ensure_ascii=False)
    except (requests.RequestException, ValueError) as e:
        print(f"   erreur : {e}")
        return None


# ── Capture ────────────────────────────────────────────────────────────────────
def capture_one(entry: dict) -> bool:
    print(f"→ {entry['name']} ({entry['path']})")
    method = entry["method"]
    if method == "requests":
        content = _fetch_requests(entry["url"])
        if content is None and entry.get("fallback") == "playwright":
            print("   requests KO → tentative Playwright")
            content = _fetch_playwright(entry["url"])
    elif method == "playwright":
        content = _fetch_playwright(entry["url"])
    elif method == "riaa":
        content = _fetch_riaa(entry["url"])
    elif method == "ultratop":
        content = _fetch_ultratop(entry["params"])
    elif method == "getsongbpm":
        content = _fetch_getsongbpm()
    else:
        print(f"   méthode inconnue : {method}")
        return False

    if not content:
        print("   ❌ capture échouée")
        return False

    target = FIXTURES_DIR / entry["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    meta = {
        "name": entry["name"],
        "url": entry["url"],
        "method": method,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "size_bytes": len(content.encode("utf-8")),
    }
    target.with_suffix(target.suffix + ".meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"   ✅ {meta['size_bytes']:,} octets → {target.relative_to(FIXTURES_DIR.parent.parent)}")
    return True


def _matches(entry: dict, tokens: list[str]) -> bool:
    source_dir = entry["path"].split("/", 1)[0]
    return any(t == entry["name"] or t == source_dir or entry["name"].startswith(t) for t in tokens)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture les fixtures des tests de parsers")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="liste les captures disponibles")
    group.add_argument("--all", action="store_true", help="capture tout")
    group.add_argument(
        "--only",
        metavar="NOMS",
        help="captures ciblées, séparées par des virgules (nom ou source, ex. kworb,riaa)",
    )
    args = parser.parse_args()

    if args.list:
        for entry in CAPTURES:
            print(f"  {entry['name']:<22} {entry['method']:<11} → tests/fixtures/{entry['path']}")
        return 0

    if args.only:
        tokens = [t.strip() for t in args.only.split(",") if t.strip()]
        selected = [e for e in CAPTURES if _matches(e, tokens)]
        if not selected:
            print(f"Aucune capture ne correspond à : {args.only} (voir --list)")
            return 1
    else:
        selected = CAPTURES

    failures = 0
    for i, entry in enumerate(selected):
        if i:
            time.sleep(DELAY_BETWEEN_REQUESTS)
        if not capture_one(entry):
            failures += 1

    print(f"\n{len(selected) - failures}/{len(selected)} capture(s) réussie(s)")
    if failures:
        print("Relancer les captures en échec avec --only, ou voir docs/maintenance-sources.md")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
