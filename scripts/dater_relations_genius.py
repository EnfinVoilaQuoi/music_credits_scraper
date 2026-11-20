"""Ajoute l'année des morceaux visés (sample, reprise…) aux relations Genius
déjà enregistrées, depuis la dernière lecture de la page gardée dans le brut
(`src.utils.annees_relations`).

    python scripts/dater_relations_genius.py                 # à blanc (défaut)
    python scripts/dater_relations_genius.py --apply         # backup + écriture
    python scripts/dater_relations_genius.py --base copie.db # sur une autre base
    python scripts/dater_relations_genius.py --relire --apply

Hors ligne par défaut : seul le brut est lu. `--relire` lit par l'API Genius
(`GET /songs/{id}`, gardé dans le brut) les seules pages qui y manquent : aucune
étape ciblée ne relit le détail d'un morceau PRINCIPAL déjà complet (2026-11-20 :
1 002 pages). Seule l'année manquante s'ajoute ; rien d'autre ne change, rien
n'est retiré.
"""

import argparse
import collections
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    parser.add_argument("--brut", type=Path, help="autre brut que data/brut/brut.db")
    parser.add_argument(
        "--relire", action="store_true", help="lit par l'API les pages absentes du brut"
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from sqlalchemy import text

    import src.utils.data_manager as module_dm
    from src.api.genius_api import GeniusAPI
    from src.persistence import brut
    from src.utils.annees_relations import dater_relations
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    with dm.engine.connect() as conn:
        lignes = conn.execute(
            text(
                "SELECT t.id, t.genius_id, t.title, a.name, t.relationships FROM tracks t "
                "JOIN artists a ON a.id = t.artist_id "
                "WHERE t.genius_id IS NOT NULL AND t.relationships LIKE '%\"url\"%' "
                "ORDER BY t.id"
            )
        ).fetchall()

    cas, sans_lecture, relues, echecs = [], 0, 0, 0
    lues_par_page: dict[int, list] = {}
    api = GeniusAPI() if args.relire else None
    for track_id, genius_id, titre, artiste, relations_json in lignes:
        try:
            posees = json.loads(relations_json)
        except (TypeError, ValueError):
            continue
        if not any(isinstance(r, dict) and r.get("url") and not r.get("year") for r in posees):
            continue
        if genius_id not in lues_par_page:
            lues_par_page[genius_id] = _relations_lues(brut, GeniusAPI, genius_id, args.brut)
            if lues_par_page[genius_id] is None and api is not None:
                lues_par_page[genius_id] = _relire(api, genius_id)
                if lues_par_page[genius_id] is None:
                    echecs += 1
                else:
                    relues += 1
                    if relues % 100 == 0:
                        print(f"  … {relues} page(s) relue(s)", flush=True)
        lues = lues_par_page[genius_id]
        if lues is None:
            sans_lecture += 1
            continue
        datees = dater_relations(posees, lues)
        if datees is not None:
            cas.append((track_id, artiste, titre, posees, datees))

    print(
        f"{len(lignes)} fiche(s) à relation Genius ; {len(cas)} à dater ; "
        f"{sans_lecture} sans lecture de page dans le brut"
    )
    if api is not None:
        print(f"Pages relues par l'API : {relues} ; sans réponse : {echecs}")
    for artiste, n in collections.Counter(c[1] for c in cas).most_common(15):
        print(f"  {artiste} : {n}")
    for _, artiste, titre, posees, datees in cas[:25]:
        nouvelles = [r for r in datees if r not in posees]
        print(
            f"  {artiste} — {titre} : "
            + " ; ".join(f"{r['type']} {r['title']} ({r['year']})" for r in nouvelles)
        )

    if not args.apply or not cas:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not _sauvegarder(dm, args, "before_dater_relations_genius"):
        return 1
    ecrites = sum(1 for track_id, *_, datees in cas if dm.record_relationships(track_id, datees))
    print(f"✅ {ecrites} / {len(cas)} fiche(s) écrite(s) (sœurs comprises).")
    return 0 if ecrites == len(cas) else 2


def _relations_lues(brut, genius_api, genius_id: int, chemin) -> list | None:
    """Les relations de la DERNIÈRE lecture de la page `/songs/<id>` gardée, ou None."""
    prefixe = f"https://api.genius.com/songs/{genius_id}?"
    lectures = brut.dernieres_lectures("genius_api", prefixe, chemin)
    for corps in reversed(list(lectures.values())):
        try:
            song = (json.loads(corps).get("response") or {}).get("song")
        except (TypeError, ValueError):
            continue
        if isinstance(song, dict):
            return genius_api._extract_relationships(song)
    return None


def _relire(api, genius_id: int) -> list | None:
    """Les relations de la page lue par l'API (gardée dans le brut), ou None."""
    import time

    import requests

    from src.config import DELAY_BETWEEN_REQUESTS

    try:
        song = (api.genius.song(genius_id) or {}).get("song")
    except (requests.RequestException, AssertionError) as e:
        print(f"  ⚠️ page #{genius_id} : {e}")
        return None
    finally:
        time.sleep(DELAY_BETWEEN_REQUESTS)
    return api._extract_relationships(song) if isinstance(song, dict) else None


def _sauvegarder(dm, args, nom: str) -> bool:
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager

    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup(nom)
    if not backup:
        print("❌ Backup impossible — abandon.")
        return False
    print(f"💾 Backup : {backup}")
    return True


if __name__ == "__main__":
    sys.exit(main())
