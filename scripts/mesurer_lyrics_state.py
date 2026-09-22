"""Que vaut `lyrics_state` de Genius ? — LECTURE SEULE, avant de coder le lot 4.

Genius expose un champ `lyrics_state` (`complete`, `unreleased`,
`incomplete`…). Il est déjà dans le payload album (`get_album_tracks_from_url`)
et jeté aussitôt. Avant d'en faire la source du constat « inédit » (e34), trois
questions, et **aucune ne se devine** :

  ① que vaut-il sur les 168 morceaux que notre marqueur d'astérisque retient ?
  ② `GET /songs/{id}` l'expose-t-il, ou seulement la page ? (e27 a trouvé
     `instrumental` PAGE-seulement : ne rien supposer par analogie) ;
  ③ combien de morceaux SORTIS le porterait-il à tort ?

La réponse décide de la suite : si l'API le donne, le constat s'écrit depuis
l'API (`unreleased` ⇒ 1, **`complete` ⇒ 0**, et c'est ce 0 qui rend le tri-état
auto-réparateur le jour où le morceau sort) ; sinon il faut un marqueur de PAGE
sur le gabarit d'e27 et une fixture.

N'écrit RIEN. Un échantillon aléatoire mais DÉTERMINISTE (graine fixe) pour que
deux exécutions se comparent.

Usage :
    python scripts/mesurer_lyrics_state.py                 # 40 inédits + 40 témoins
    python scripts/mesurer_lyrics_state.py --taille 80
"""

import argparse
import collections
import random
import re
import sqlite3
import sys
import time

# Fix encodage Windows (règle projet : reconfigure, jamais de re-wrapping)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests

from src.config import GENIUS_API_KEY

DB_PATH = "data/music_credits.db"

#: Le marqueur d'inédit mesuré le 2026-09-22 : une étoile finale COLLÉE à un
#: caractère. Écarte « Jeune N**** » (étoile sur étoile), « * » et
#: « Interlude * » (étoile détachée) — 3 contre-exemples réels sur 171.
MARQUEUR = re.compile(r"[^\s*]\*$")


def _corpus(conn):
    """(inédits présumés, témoins) — des morceaux à `genius_id` seulement."""
    inedits, temoins = [], []
    for track_id, titre, artiste, genius_id in conn.execute(
        "SELECT t.id, t.title, a.name, t.genius_id FROM tracks t "
        "JOIN artists a ON a.id = t.artist_id WHERE t.genius_id IS NOT NULL"
    ):
        if not titre:
            continue
        (inedits if MARQUEUR.search(titre.rstrip()) else temoins).append(
            (track_id, titre, artiste, genius_id)
        )
    return inedits, temoins


def _interroger(genius_id: int) -> tuple[str | None, str | None]:
    """(`lyrics_state` vu par l'API, motif d'échec). Un seul `GET /songs/{id}`."""
    try:
        reponse = requests.get(
            f"https://api.genius.com/songs/{genius_id}",
            headers={"Authorization": f"Bearer {GENIUS_API_KEY}"},
            timeout=20,
        )
    except requests.RequestException as e:
        return None, f"réseau : {e}"
    if reponse.status_code != 200:
        return None, f"HTTP {reponse.status_code}"
    try:
        song = (reponse.json().get("response") or {}).get("song") or {}
    except ValueError as e:
        return None, f"JSON illisible : {e}"
    if "lyrics_state" not in song:
        # LA question ② : le champ existe-t-il seulement ici ?
        return None, "champ ABSENT du payload /songs"
    return song.get("lyrics_state"), None


def _sonder(lot, libelle: str, delai: float) -> collections.Counter:
    etats = collections.Counter()
    print(f"\n── {libelle} ({len(lot)}) ──")
    for track_id, titre, artiste, genius_id in lot:
        etat, motif = _interroger(genius_id)
        etats[etat or f"— {motif}"] += 1
        if etat != "complete":
            print(f"   #{track_id} {artiste} — {titre!r} → {etat or motif}")
        time.sleep(delai)
    return etats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--taille", type=int, default=40, help="morceaux par groupe")
    parser.add_argument("--delai", type=float, default=0.3, help="secondes entre deux appels")
    args = parser.parse_args()

    if not GENIUS_API_KEY:
        print("❌ GENIUS_API_KEY absente — ce script lit l'API Genius.")
        return 1

    conn = sqlite3.connect(DB_PATH)
    try:
        inedits, temoins = _corpus(conn)
    finally:
        conn.close()

    print(f"Corpus : {len(inedits)} inédit(s) présumé(s), {len(temoins)} témoin(s) à genius_id.")
    tirage = random.Random(20260922)  # déterministe : deux mesures se comparent
    echantillon_inedits = inedits[: args.taille]
    echantillon_temoins = tirage.sample(temoins, min(args.taille, len(temoins)))

    etats_inedits = _sonder(echantillon_inedits, "INÉDITS présumés (astérisque)", args.delai)
    etats_temoins = _sonder(echantillon_temoins, "TÉMOINS (titre nu)", args.delai)

    print("\n═══ Verdict ═══")
    for libelle, etats in (("inédits présumés", etats_inedits), ("témoins", etats_temoins)):
        total = sum(etats.values()) or 1
        print(f"\n{libelle} :")
        for etat, n in etats.most_common():
            print(f"   {n:4} ({100 * n / total:5.1f} %)  {etat}")

    absent = any(str(k).startswith("— champ ABSENT") for k in etats_inedits | etats_temoins)
    print(
        "\n➜ L'API n'expose PAS `lyrics_state` : il faudra un marqueur de PAGE (gabarit e27)."
        if absent
        else "\n➜ L'API expose `lyrics_state` : le constat peut s'écrire depuis l'API "
        "(`unreleased` ⇒ 1, `complete` ⇒ 0)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
