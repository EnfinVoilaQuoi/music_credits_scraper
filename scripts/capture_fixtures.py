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
from urllib.parse import urlencode

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
# Spotify web : sentinelle ISHA, choisie EXPRÈS. C'est l'artiste sur lequel le
# projet s'est déjà trompé d'identité (streams de Limsa d'Aulnay écrits sur Isha,
# JOURNAL 2026-07-02), et ses « Recommandés » sont truffés de Limsa d'Aulnay :
# la fixture éprouve donc le parsing ET le garde-fou d'attribution par ID sur le
# cas qui a réellement cassé.
_SPOTIFY_WEB_TRACK_ID = "3EDDunSmj8RbiVGU0Qr0M8"  # ISHA — CR600 (Bonus Track)
_SPOTIFY_WEB_ARTIST_ID = "0dSh0CIa0HPd9kJmJSmGQo"  # ISHA
_RIAA_ARTIST = "Daft Punk"  # catalogue RIAA stable et court (pas de rap FR chez RIAA)


def _riaa_artist_url() -> str:
    """URL de recherche RIAA par artiste, demandée AU SCRAPER.

    Le formulaire du site a été refait le 2026-09-06 (paramètres renommés) : une
    URL recopiée dans ce script se serait périmée sans bruit, et la capture
    aurait enregistré une page vide qu'on aurait prise pour la vérité.
    """
    from src.scrapers.riaa_scraper_v2 import RIAAScraperV2

    return RIAAScraperV2._search_url(artist=_RIAA_ARTIST)


_LRCLIB_PARAMS = {  # /get exact : Josman — Dans le vide (album Matrix, 243 s en base)
    "track_name": "Dans le vide",
    "artist_name": "Josman",
    "album_name": "Matrix",
    "duration": 243,
}
_GETSONGBPM_LOOKUP = "song:Harder, Better, Faster, Stronger artist:Daft Punk"

# BPI : fenêtre d'UN MOIS figée dans le passé. Le mois (et non le jour) parce
# qu'il faut une page PLEINE — 24 lignes — pour que la fixture éprouve aussi la
# pagination ; et le passé parce que la page d'accueil changerait toutes les
# semaines et périmerait la fixture sans que rien n'ait cassé.
_BPI_DEBUT, _BPI_FIN = "2020-01-01", "2020-01-31"
# SIGALA — EASY LOVE : trois paliers datés dont UN replié derrière « Show 1 more »
# (`div.hidden`). C'est précisément la page qui prouve qu'il ne faut pas se fier
# au texte visible.
_BPI_DETAIL = "/format/2/artist/3345/title/16392"
_BPI_ARTISTE = "sigala"  # 18 entités : l'artiste dont le nom EST une famille d'ids


def _bpi_liste_url() -> str:
    """URL de recherche BPI, demandée AU SCRAPER (cf. `_riaa_artist_url`)."""
    from src.scrapers.bpi_scraper import BpiScraper

    return BpiScraper().url_liste(debut=_BPI_DEBUT, fin=_BPI_FIN)


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
        "name": "spotify_web_track",
        "path": "spotify_web/track.html",
        "url": f"https://open.spotify.com/intl-fr/track/{_SPOTIFY_WEB_TRACK_ID}",
        "method": "spotify_web",
    },
    {
        "name": "spotify_web_artist",
        "path": "spotify_web/artist.html",
        "url": f"https://open.spotify.com/intl-fr/artist/{_SPOTIFY_WEB_ARTIST_ID}",
        "method": "spotify_web",
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
        # URL construite par le scraper lui-même : le formulaire du site a changé
        # (2026-09-06) et une URL recopiée ici se serait périmée en silence.
        "url": _riaa_artist_url(),
        "method": "riaa",
    },
    {
        "name": "bpi_list",
        "path": "bpi/bpi_list.html",
        "url": _bpi_liste_url(),
        "method": "bpi",
    },
    {
        "name": "bpi_detail",
        "path": "bpi/bpi_detail.html",
        "url": f"https://certified-awards.bpi.co.uk{_BPI_DETAIL}",
        "method": "bpi_nu",
    },
    {
        "name": "bpi_artists",
        "path": "bpi/bpi_artists.html",
        "url": f"https://certified-awards.bpi.co.uk/artists?q={_BPI_ARTISTE}",
        "method": "bpi_nu",
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


def _fetch_spotify_web(url: str) -> str | None:
    """Spotify web = SPA : `requests` rend 200 sur un HTML VIDE de tout compteur
    (157 Ko sans un seul `playcount`, mesuré le 2026-09-04). On capture donc la
    page RENDUE, via la session patchright du scraper — profil dédié et locale
    épinglée, sinon le libellé des auditeurs mensuels change avec la machine.
    Le JS déplie les titres populaires (5 → 10) avant la capture."""
    from src.concurrency import async_loop
    from src.scrapers.spotify_web_scraper import _EXPAND_JS, SpotifyWebScraper

    async def _run() -> str | None:
        scraper = SpotifyWebScraper(headless=True)
        async with scraper.session() as sess:
            return await sess.fetch(
                url,
                wait_for='css:[data-testid="tracklist-row"]',
                js_before_wait=_EXPAND_JS,
                delay_before_return=1.0,
            )

    try:
        return async_loop.run_sync(_run())
    except Exception as e:
        print(f"   erreur Spotify web : {e}")
        return None


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


def _fetch_bpi(url: str, *, hx: bool = True) -> str | None:
    """GET nu + en-tête htmx. Aucun navigateur : le site est rendu côté serveur.

    `hx=False` pour les pages complètes (détail, annuaire), qui n'attendent pas
    l'en-tête — seul l'endpoint de résultats en dépend, et sans lui il rend une
    coquille vide qu'on prendrait pour une page valide.
    """
    import httpx

    from src.scrapers.bpi_scraper import _HX, _UA

    try:
        reponse = httpx.get(
            url, headers={**_UA, **(_HX if hx else {})}, timeout=30, follow_redirects=True
        )
        reponse.raise_for_status()
        return reponse.text
    except httpx.HTTPError as e:
        print(f"  ✗ BPI : {type(e).__name__}: {e}")
        return None


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
    elif method == "spotify_web":
        content = _fetch_spotify_web(entry["url"])
    elif method == "riaa":
        content = _fetch_riaa(entry["url"])
    elif method == "ultratop":
        content = _fetch_ultratop(entry["params"])
    elif method == "bpi":
        content = _fetch_bpi(entry["url"])
    elif method == "bpi_nu":
        content = _fetch_bpi(entry["url"], hx=False)
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
