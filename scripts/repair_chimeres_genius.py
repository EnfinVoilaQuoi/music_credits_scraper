"""Fiches chimères : un morceau Genius au MAUVAIS genius_id (e36, 2026-09-24).

Avant e36, `save_track` retrouvait une fiche par son titre : des morceaux Genius
DIFFÉRENTS au même titre fusionnaient, le premier `genius_id` restait et les
suivants étaient refusés (log « genius_id concurrent »). Deux suites :

  · les morceaux REFUSÉS n'ont jamais eu de fiche — un run discographie les crée
    désormais tout seul (leur genius_id ne désigne plus aucune fiche) ;
  · une fiche peut garder le genius_id d'un AUTRE morceau que celui qu'elle
    décrit (streams, ID Spotify, album) : « goosebumps » garde la page de la
    cover de Skylar Grey alors qu'elle porte les streams de Travis Scott.
    C'est ce second cas que ce script corrige, AVANT le run discographie
    (sinon celui-ci créerait une seconde fiche pour le bon morceau).

Verdict par fiche (`verdict`) : les artistes de son ID Spotify, sinon
l'artiste principal, sinon l'album (colonne contaminée : ce dernier critère
n'est JAMAIS appliqué automatiquement, comme les indécis).

    python scripts/repair_chimeres_genius.py            # dry-run
    python scripts/repair_chimeres_genius.py --apply    # backup + écriture

Après --apply : run discographie des artistes touchés (crée les morceaux
refusés, covers comprises), puis `python -m src.cli credits <nom> --manquants`.
"""

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests

from src.config import DATA_DIR, GENIUS_API_KEY
from src.utils.spotify_identity import lire_identite_http
from src.utils.title_matching import normalize_name, normalize_title

_LOGS = Path(DATA_DIR) / "logs"
_COLLISION = re.compile(
    r"genius_id concurrent sur « (?P<titre>.*) » \(id=(?P<tid>\d+)\) : "
    r"(?P<garde>\d+) conservé, (?P<refuse>\d+) REFUSÉ"
)


def collisions() -> dict[int, set[int]]:
    """{track_id: {genius_id refusés}} d'après les logs du scraper."""
    refuses: dict[int, set[int]] = {}
    for log in sorted(_LOGS.glob("*_scraper.log")):
        for ligne in log.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _COLLISION.search(ligne)
            if m:
                refuses.setdefault(int(m["tid"]), set()).add(int(m["refuse"]))
    return refuses


def _chanson(genius_id: int, cache: dict) -> dict | None:
    if genius_id not in cache:
        r = requests.get(
            f"https://api.genius.com/songs/{genius_id}",
            headers={"Authorization": f"Bearer {GENIUS_API_KEY}"},
            timeout=15,
        )
        cache[genius_id] = r.json()["response"]["song"] if r.ok else None
        time.sleep(0.2)
    return cache[genius_id]


def _artistes_du_chant(c: dict) -> set[str]:
    noms = [(c.get("primary_artist") or {}).get("name")]
    noms += [a.get("name") for a in c.get("primary_artists") or []]
    noms += [a.get("name") for a in c.get("featured_artists") or []]
    return {normalize_name(n) for n in noms if n}


def _principal(c: dict) -> set[str]:
    noms = [(c.get("primary_artist") or {}).get("name")]
    noms += [a.get("name") for a in c.get("primary_artists") or []]
    return {normalize_name(n) for n in noms if n}


def verdict(
    fiche: dict, candidats: list[dict], artistes_spotify: set[str] | None
) -> tuple[str, dict | None, str]:
    """PUR. (`ok` | `a_corriger` | `indecis`, candidat retenu, raison).

    La colonne `album` d'une chimère est CONTAMINÉE (dernier écrivain) : elle ne
    sert qu'en dernier recours. Ce qui désigne le contenu de la fiche :
      1. les artistes de son ID Spotify (streams, durée, BPM viennent de lui) ;
      2. le morceau dont l'artiste de la fiche est PRINCIPAL ;
      3. l'album.
    """

    def _conclure(retenus, raison):
        choisi = retenus[0]
        return ("ok" if choisi["id"] == fiche["genius_id"] else "a_corriger"), choisi, raison

    if artistes_spotify:
        retenus = [c for c in candidats if _principal(c) & artistes_spotify]
        if len(retenus) == 1:
            return _conclure(retenus, "artistes de l'ID Spotify")
    moi = normalize_name(fiche["artiste"])
    retenus = [c for c in candidats if moi in _principal(c)]
    if len(retenus) == 1:
        return _conclure(retenus, "artiste principal")
    album = normalize_title(fiche["album"] or "")
    if album:
        retenus = [
            c
            for c in candidats
            if normalize_title((c.get("album") or {}).get("name") or "") == album
        ]
        if len(retenus) == 1:
            return _conclure(retenus, "album (colonne peut-être contaminée)")
    return "indecis", None, "aucun critère ne tranche"


#: Critères assez sûrs pour une correction sans validation humaine.
_AUTOMATIQUES = {"artistes de l'ID Spotify", "artiste principal"}


def _appliquer(dm, genius, fiche: dict, choisi: dict) -> str:
    """Pose la bonne page. Rend un statut lisible."""
    artiste = dm.get_artist_by_name(fiche["artiste"])
    relation = genius._verify_artist_credit(choisi["id"], artiste.genius_id)
    if relation is None:
        return "SAUTÉ : l'artiste n'est pas crédité au détail de la page"
    kind, role = relation
    principal = (choisi.get("primary_artist") or {}).get("name")
    date = genius.precision_de_la_date(choisi)
    if date is None:
        brute = genius._extract_release_date_from_song(choisi)
        date = brute.strftime("%Y-%m-%d") if brute else None
    ok = dm.rattacher_page_genius(
        fiche["id"],
        genius_id=choisi["id"],
        genius_url=choisi.get("url"),
        album=genius._extract_album_from_song(choisi),
        date_observee=date,
        is_featuring=kind != "primary",
        primary_artist_name=None if kind == "primary" else principal,
        secondary_role=role if kind == "secondary" else None,
    )
    return f"corrigé ({kind}{' ' + role if role else ''})" if ok else "REFUSÉ (voir log)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrire (défaut : dry-run)")
    args = parser.parse_args()
    dm = genius = None
    if args.apply:
        from src.api.genius_api import GeniusAPI
        from src.utils.data_manager import DataManager
        from src.utils.database_backup import get_backup_manager

        dm = DataManager()  # applique e36 si besoin (backup automatique)
        print(f"💾 Backup : {get_backup_manager().create_backup('before_repair_chimeres_genius')}")
        genius = GeniusAPI()
    base = sqlite3.connect(f"file:{Path(DATA_DIR) / 'music_credits.db'}?mode=ro", uri=True)
    base.row_factory = sqlite3.Row
    cache: dict = {}
    compte = {"ok": 0, "a_corriger": 0, "indecis": 0}
    for tid, refuses in sorted(collisions().items()):
        fiche = base.execute(
            "SELECT t.id, t.title, t.album, t.genius_id, t.spotify_id, a.name AS artiste "
            "FROM tracks t JOIN artists a ON a.id = t.artist_id WHERE t.id = ?",
            (tid,),
        ).fetchone()
        if fiche is None:
            continue
        ids = [fiche["genius_id"], *sorted(refuses - {fiche["genius_id"]})]
        candidats = [c for c in (_chanson(g, cache) for g in ids if g) if c]
        identite = lire_identite_http(fiche["spotify_id"]) if fiche["spotify_id"] else None
        artistes = {normalize_name(a) for a in (identite or {}).get("artists") or [] if a}
        v, choisi, raison = verdict(dict(fiche), candidats, artistes or None)
        compte[v] += 1
        if v == "ok":
            continue
        statut = ""
        if dm is not None and v == "a_corriger" and raison in _AUTOMATIQUES:
            statut = "  ⇒ " + _appliquer(dm, genius, dict(fiche), choisi)
            time.sleep(0.2)
        elif dm is not None:
            statut = "  ⇒ à valider à la main"
        print(
            f"[{v}] {fiche['artiste']} — « {fiche['title']} » (id {tid}, album "
            f"{fiche['album']!r}, Spotify {sorted(artistes) or '—'}) — {raison}{statut}"
        )
        for c in candidats:
            marque = "→" if choisi and c["id"] == choisi["id"] else " "
            garde = " (gardé)" if c["id"] == fiche["genius_id"] else ""
            album_c = (c.get("album") or {}).get("name")
            print(f"     {marque} {c['id']}{garde}  {c['full_title'][:70]}  | {album_c}")
    print(f"\nTOTAL {compte}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
