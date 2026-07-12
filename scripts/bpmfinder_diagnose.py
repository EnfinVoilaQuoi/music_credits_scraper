"""Diagnostic BPM Finder — pourquoi « pas de résultat en 90s » ?

Instrumente BPMFinderScraper de l'extérieur (réseau, console JS, JWT,
screenshots) et imprime un VERDICT selon l'arbre :
  A. aucun POST /api/yt après le clic Upload → sélecteur/UI cassé
  B. POST /api/yt en erreur (401/403/429/5xx ou corps d'erreur) → session/quota/backend
  C. POST /api/yt OK mais aucune carte détectée par _CARD_RE → format des cartes changé
  D. carte apparue mais après le timeout → analyse simplement plus lente

Usage :
    python scripts/bpmfinder_diagnose.py [URL_YOUTUBE] [--timeout 120]
    (défaut : ré-analyse un videoId du cache — succès passé garanti analysable)
"""

import argparse
import base64
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")  # emojis Windows
except Exception:
    pass

from src.config import DATA_DIR
from src.scrapers.bpmfinder_scraper import _CACHE_FILE, _CARD_RE, BPMFinderScraper

DIAG_DIR = DATA_DIR / "diagnostics"
API_MARK = "audioaidynamics.com/api"


def decode_jwt_exp(token: str):
    """Retourne (exp_datetime, expiré?) depuis le payload JWT, sans vérifier la signature."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        if not exp:
            return None, None
        return datetime.fromtimestamp(exp), exp < time.time()
    except Exception:
        return None, None


def pick_default_url() -> str:
    """Un videoId du cache (analyse déjà réussie par le passé) sinon un défaut."""
    try:
        cache = json.loads(Path(_CACHE_FILE).read_text(encoding="utf-8"))
        if cache:
            vid = sorted(cache)[0]
            return f"https://www.youtube.com/watch?v={vid}"
    except Exception:
        pass
    return "https://www.youtube.com/watch?v=6U4ImcGixlY"  # Josman - Room Service (vu dans les logs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default=None)
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()
    url = args.url or pick_default_url()

    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"🔬 Diagnostic BPM Finder — {url}")
    print(f"📁 Captures dans {DIAG_DIR}\n")

    scraper = BPMFinderScraper(headless=False)
    # bypass cache CIBLÉ : forcer une vraie analyse de CE videoId sans toucher
    # aux autres entrées (vider tout le dict ferait réécrire un cache amputé)
    vid = BPMFinderScraper._video_id(url)
    if vid:
        scraper.cache.pop(vid, None)

    # NB Playwright sync : PAS d'appel bloquant (resp.text()) dans un handler
    # d'événement → on stocke l'objet Response et on lit les corps APRÈS.
    api_events = []  # [(t, 'req'|'resp', method, url, status, resp_obj)]

    def on_request(req):
        if API_MARK in req.url:
            api_events.append((time.time(), "req", req.method, req.url, None, None))
            print(f"  → {req.method} {req.url}")

    def on_response(resp):
        if API_MARK in resp.url:
            api_events.append(
                (time.time(), "resp", resp.request.method, resp.url, resp.status, resp)
            )
            print(f"  ← {resp.status} {resp.request.method} {resp.url}")

    def on_console(msg):
        if msg.type in ("error", "warning"):
            print(f"  🖥️ console.{msg.type}: {msg.text[:200]}")

    try:
        scraper._ensure_driver()
        scraper.context.on("request", on_request)
        scraper.context.on("response", on_response)
        scraper.page.on("console", on_console)

        # ── État de la session AVANT analyse ──
        cookies = {c["name"]: c for c in scraper.context.cookies()}
        tok = cookies.get("access_token", {}).get("value")
        if tok:
            exp_dt, expired = decode_jwt_exp(tok)
            status = (
                ("EXPIRÉ ❌" if expired else "valide ✅")
                if expired is not None
                else "exp non lisible"
            )
            print(f"🔑 access_token présent — {status}" + (f" (exp: {exp_dt})" if exp_dt else ""))
        else:
            print("🔑 pas de cookie access_token (login à venir dans analyze())")

        scraper._goto_analyzer()
        scraper.page.screenshot(path=str(DIAG_DIR / f"{stamp}_1_analyzer.png"))

        t0 = time.time()
        result = scraper.analyze(url, timeout_s=args.timeout)
        elapsed = time.time() - t0

        scraper.page.screenshot(path=str(DIAG_DIR / f"{stamp}_2_fin.png"), full_page=True)
        body_text = ""
        try:
            body_text = scraper.page.inner_text("body") or ""
            (DIAG_DIR / f"{stamp}_page.txt").write_text(body_text, encoding="utf-8")
        except Exception:
            pass

        # ── Lire les corps de réponse (hors handler : appels bloquants OK ici) ──
        def body_of(resp_obj) -> str:
            try:
                return (resp_obj.text() or "")[:600]
            except Exception as e:
                return f"<corps illisible: {e}>"

        # ── VERDICT ──
        print("\n" + "=" * 62)
        yt_resps = [e for e in api_events if e[1] == "resp" and "/api/yt" in e[3]]
        yt_reqs = [e for e in api_events if e[1] == "req" and "/api/yt" in e[3]]
        cards_now = _CARD_RE.findall(body_text)

        if result:
            print(f"✅ ANALYSE RÉUSSIE en {elapsed:.0f}s : {result}")
            if elapsed > 90:
                print("⚠️ VERDICT D : ça marche mais > 90s → monter timeout_s dans data_enricher.")
            else:
                print(
                    "🤔 Réussi ici mais échoue dans l'app : comparer les conditions"
                    " (headless, thread, session) — relancer en headless=True pour confirmer."
                )
        elif not yt_reqs:
            print("❌ VERDICT A : AUCUN POST /api/yt après le clic Upload.")
            print(
                "   → Le clic ne déclenche rien : bouton/sélecteur à revoir"
                " (inspecter la fenêtre + screenshots)."
            )
        elif yt_resps and any(e[4] >= 400 for e in yt_resps):
            worst = max(yt_resps, key=lambda e: e[4])
            print(f"❌ VERDICT B : /api/yt répond {worst[4]}.")
            print(f"   corps: {body_of(worst[5])[:400]}")
            print(
                "   → 401/403 = session/JWT ; 429 = quota ; 5xx = backend"
                " (souvent : ingestion YouTube cassée côté site)."
            )
        elif yt_resps:
            print(
                f"❌ VERDICT C (probable) : /api/yt = {yt_resps[-1][4]} mais aucune nouvelle carte parsée."
            )
            print(f"   Cartes matchées par _CARD_RE dans la page finale : {len(cards_now)}")
            print(
                f"   → comparer {stamp}_page.txt / screenshots au format attendu"
                " « Key: X minor … BPM: NN … Camelot: NA » et ajuster _CARD_RE."
            )
            print(f"   corps /api/yt: {body_of(yt_resps[-1][5])[:400]}")
        else:
            print(
                "❌ POST /api/yt parti mais AUCUNE réponse reçue avant le timeout"
                " → backend qui ne répond pas (VERDICT B, variante lenteur/panne)."
            )
        print(f"\n📸 Preuves : {stamp}_*.png / {stamp}_page.txt dans {DIAG_DIR}")
        print("Événements API captés:")
        for t, kind, method, u, s, _ in api_events:
            print(f"  +{t - t0:6.1f}s {kind:4} {method:4} {str(s or ''):>4} {u}")
    finally:
        try:
            scraper.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
