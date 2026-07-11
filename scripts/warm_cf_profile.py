#!/usr/bin/env python3
"""
Réchauffe le profil navigateur persistant utilisé par le scraper Genius.

Ouvre un Chromium (patchright = undetected) sur le **même profil** que le
scraper. Tu résous le challenge Cloudflare **comme un humain** (et tu peux
naviguer), puis tu fermes : le cookie `cf_clearance` est écrit dans le profil.
Ensuite le scraper Genius passe en **headless** sans te re-demander de challenge.

Lancement (depuis la racine du repo) :
    python scripts/warm_cf_profile.py
    python scripts/warm_cf_profile.py "https://genius.com/Medine-thalys-lyrics"

Astuce extensions : pour charger une extension décompressée (ex. un solveur),
décommente la ligne `args=[...]` ci-dessous avec son chemin.
"""

import asyncio
import os
import sys

# Rendre `src` importable quand on lance le script directement
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scrapers.crawl4ai_scraper_base import _PROFILE_DIR, _USER_AGENT  # noqa: E402

DEFAULT_URL = "https://genius.com/Medine-thalys-lyrics"


async def main(url: str):
    from patchright.async_api import async_playwright

    os.makedirs(_PROFILE_DIR, exist_ok=True)
    print(f"📂 Profil persistant : {_PROFILE_DIR}")
    print(f"🌐 Ouverture de : {url}")

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            _PROFILE_DIR,
            headless=False,
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 900},
            # Pour charger une extension décompressée :
            # args=["--disable-extensions-except=C:\\chemin\\ext", "--load-extension=C:\\chemin\\ext"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=120_000)
        except Exception as e:
            print(f"(navigation : {e} — continue quand même)")

        print("\n👉 Résous le challenge Cloudflare dans la fenêtre, navigue si tu veux.")
        print("   Quand la page de PAROLES s'affiche normalement, reviens ici")
        print("   et appuie sur [Entrée] pour enregistrer le profil et fermer.\n")
        await asyncio.get_event_loop().run_in_executor(None, input)

        # Vérif rapide : le cookie cf_clearance est-il là ?
        try:
            cookies = await ctx.cookies()
            has_clear = any(c.get("name") == "cf_clearance" for c in cookies)
            print(
                f"🍪 cf_clearance présent : {'OUI ✅' if has_clear else 'non ❌'} "
                f"({len(cookies)} cookies au total)"
            )
        except Exception:
            pass

        await ctx.close()

    print("\n✅ Profil réchauffé. Relance l'app : le scrape Genius doit passer en headless.")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    asyncio.run(main(target))
