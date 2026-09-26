"""Oracle des LRC : le texte synchronisé correspond-il aux paroles Genius ?

Mesure du 2026-09-26 (chantier « panneau À trancher », WIP) : la recherche de
paroles YouTube Music retenait le 1ᵉʳ résultat du bon ARTISTE sans vérifier le
titre — 1 388 LRC YTM sur 2 809 jugeables portaient les paroles d'un autre
morceau (*Intro (A2)* de Booba avec le LRC de *G5 (Intro)*).

Score = recouvrement des mots (> 2 lettres, accents retirés, en-têtes « [...] »
ignorés), MAXIMUM des deux sens — sans quoi un Genius partiel (snippet) face à
un LRC complet paraîtrait étranger (*Wolves (BOOTS Reference)*). Jugeable si
chaque texte a au moins 8 mots distincts.

Seuils calibrés : < 0,4 faux (formel sous 0,2) · 0,4-0,6 à trancher · ≥ 0,6
juste. Témoin : LRCLIB à 95 % ≥ 0,8.

    venv/Scripts/python.exe scripts/oracle_lrc.py              # distribution par source
    venv/Scripts/python.exe scripts/oracle_lrc.py --liste 0.4  # fiches sous le seuil

Lecture seule.
"""

import argparse
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from contextlib import closing

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_TRANCHES = ((0.2, "< 0,2"), (0.4, "0,2-0,4"), (0.6, "0,4-0,6"), (0.8, "0,6-0,8"), (9, "≥ 0,8"))


def mots(texte: str | None) -> set[str]:
    t = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\[[^\]]*\]", " ", t)
    return {w for w in re.findall(r"[a-z0-9']+", t) if len(w) > 2}


def score(paroles: str | None, lrc: str | None) -> float | None:
    ms, mg = mots(lrc), mots(paroles)
    if len(ms) < 8 or len(mg) < 8:
        return None
    commun = len(ms & mg)
    return round(max(commun / len(ms), commun / len(mg)), 2)


def tranche(s: float) -> str:
    return next(nom for borne, nom in _TRANCHES if s < borne)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--liste", type=float, help="lister les LRC YTM sous ce score")
    a = p.parse_args()
    with closing(sqlite3.connect("file:data/music_credits.db?mode=ro", uri=True)) as c:
        rows = c.execute(
            "SELECT t.id, a.name, t.title, t.lyrics, t.lyrics_synced, t.lyrics_synced_source "
            "FROM tracks t JOIN artists a ON a.id = t.artist_id "
            "WHERE t.lyrics_synced IS NOT NULL AND t.lyrics IS NOT NULL "
            "AND t.lyrics_source LIKE 'genius%'"
        ).fetchall()
    par_source: dict[str, Counter] = {}
    sous = []
    for tid, art, titre, paroles, lrc, source in rows:
        s = score(paroles, lrc)
        if s is None:
            continue
        par_source.setdefault(source or "?", Counter())[tranche(s)] += 1
        if a.liste is not None and source == "YouTube Music" and s < a.liste:
            sous.append((s, tid, art, titre))
    for source, cpt in sorted(par_source.items()):
        total = sum(cpt.values())
        print(f"{source} ({total} jugeables)")
        for _, nom in _TRANCHES:
            print(f"   {nom:8s} {cpt[nom]:5d}")
    for s, tid, art, titre in sorted(sous):
        print(f"{s:.2f}  #{tid}  {art} — {titre}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
