"""Crédits de l'API Genius (`/songs/{id}`) face aux crédits scrapés (source
`genius`), sur un échantillon de morceaux — mesure du 2026-09-25, rejouée et
détaillée le 2026-09-26 pour le chantier « crédits de l'API Genius ».

Lancer depuis la racine de music_credits_scraper :
    venv/Scripts/python.exe scripts/mesure_credits_api_vs_scrape.py [--n 20] [--graine 7]

La lecture de la fiche passe par `credits_genius_api.credits_du_detail` —
celle que l'import utilise —, donc la mesure juge le code réel et non une
seconde traduction. Chaque écart est détaillé : rôle ET libellé brut côté
scrape (`role_detail` d'un `Other`), et ce que l'API dit de la même personne.
Lecture seule ; une requête Genius par morceau.

Résultat du 2026-09-26 (graine 7) : 366/381 crédits scrapés retrouvés (96 %) ;
les écarts sont des lieux « Recorded At » (pas des artistes, et découpés à tort
à la virgule par le scrape d'alors) et des « Assistant Mixing Engineer » que la
base traduisait encore avec l'ancienne table. Un seul crédit réel propre à
l'API : Kid Cudi « Programmer » sur « Unfuckwittable ».
"""

import argparse
import collections
import random
import sqlite3
import sys
import time
from contextlib import closing

import requests

from src.config import GENIUS_API_KEY
from src.utils.credits_genius_api import credits_du_detail
from src.utils.title_matching import normalize_name

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

p = argparse.ArgumentParser()
p.add_argument("--n", type=int, default=20)
p.add_argument("--graine", type=int, default=7)
a = p.parse_args()

H = {"Authorization": f"Bearer {GENIUS_API_KEY}"}

with closing(sqlite3.connect("file:data/music_credits.db?mode=ro", uri=True)) as c:
    ids = c.execute(
        "select t.id, t.genius_id, t.title from tracks t where t.genius_id is not null "
        "and t.secondary_role is null "
        "and (select count(*) from credits where track_id = t.id and source = 'genius') >= 5"
    ).fetchall()
    random.seed(a.graine)
    echantillon = random.sample(ids, a.n)

    tot_s = tot_a = commun = 0
    manques = collections.Counter()
    details_manque, details_api_seule = [], []
    for tid, gid, titre in echantillon:
        scr = {}
        for nom, role, detail in c.execute(
            "select name, role, role_detail from credits where track_id = ? and source = 'genius'",
            (tid,),
        ):
            scr[(normalize_name(nom), role)] = (nom, detail)
        song = requests.get(f"https://api.genius.com/songs/{gid}", headers=H, timeout=20).json()[
            "response"
        ]["song"]
        time.sleep(0.4)
        api = {
            (normalize_name(cr.name), cr.role.value): cr.role_detail or cr.role.value
            for cr in credits_du_detail(song)
        }
        par_nom_api = collections.defaultdict(list)
        for (n, r), lib in api.items():
            par_nom_api[n].append(f"{lib} → {r}")
        tot_s += len(scr)
        tot_a += len(api)
        commun += len(set(scr) & set(api))
        for (n, r), (nom, detail) in scr.items():
            if (n, r) not in api:
                manques[r] += 1
                details_manque.append(
                    (titre, nom, r, detail, par_nom_api.get(n) or ["(absent de l'API)"])
                )
        for (n, r), lib in api.items():
            if (n, r) not in scr:
                details_api_seule.append((titre, n, lib, r))

print(
    f"{a.n} morceaux : scrape {tot_s}, API {tot_a}, communs {commun} "
    f"({100 * commun / max(tot_s, 1):.0f} %)"
)
print("Rôles du scrape absents de l'API :", manques.most_common())
print("\n── Crédits du scrape absents de l'API (titre · nom · rôle · libellé brut · API)")
for d in details_manque:
    print("  ", d)
print("\n── Crédits de l'API absents du scrape (titre · nom · libellé API · rôle traduit)")
for d in details_api_seule:
    print("  ", d)
