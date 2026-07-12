"""Bootstrap manuel de la session BPM Finder (audioaidynamics.com).

Ouvre une fenêtre Chrome VISIBLE : connecte-toi à la main (email/mot de passe),
puis reviens dans ce terminal et appuie sur Entrée — la session (cookies +
localStorage) est sauvegardée dans data/.bpmfinder_session.json et le scraper
la réutilisera en headless sans re-login.

À relancer seulement si la session expire ET que le login automatique échoue.

Usage :
    python scripts/bpmfinder_login.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.scrapers.bpmfinder_scraper import ANALYZER_URL, BPMFinderScraper

scraper = BPMFinderScraper(headless=False)
scraper._ensure_driver()
scraper.page.goto(ANALYZER_URL, wait_until="domcontentloaded")

print("\n🌐 Fenêtre ouverte — connecte-toi sur le site (menu profil en bas à gauche).")
input("   Quand tu es connecté, appuie sur Entrée ici pour sauvegarder la session… ")

scraper._save_session()
scraper.close()
print("✅ Session sauvegardée — le scraper l'utilisera automatiquement.")
