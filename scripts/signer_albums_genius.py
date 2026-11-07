"""Note qui signe l'album Genius des morceaux d'un autre, relu dans le BRUT.

    python scripts/signer_albums_genius.py                 # à blanc
    python scripts/signer_albums_genius.py --apply         # backup, puis les écrivains
    python scripts/signer_albums_genius.py --base copie.db --brut data/brut/brut.db

Le morceau d'un autre compte quand l'artiste signe le disque (décision utilisateur
2026-11-07, `participation.signe_son_disque`). Les lectures de pages notent désormais
cette signature (`album_artist`) ; ce script la rattrape, sans réseau, pour les seules
fiches que la règle juge encore « sur le disque » : la page de la fiche d'abord, sinon
une autre page du même album (une fiche de tracklist n'a pas été lue seule). Rien
dans le brut : rien d'écrit, la fiche reste hors des totaux.
"""

import argparse
import collections
import json
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

REQUETE_PAGE = re.compile(r"/songs/(\d+)(?:\?|$)")


def signatures_du_brut(lectures) -> tuple[dict[int, str], dict[int, str]]:
    """PUR. `({page: artiste de son album}, {album: son artiste})` depuis les lectures
    `(requete, corps, …)` de `/songs/{id}`, dans l'ordre : la dernière l'emporte."""
    par_page, par_album = {}, {}
    for requete, corps, *_ in lectures:
        m = REQUETE_PAGE.search(requete)
        if not m:
            continue
        try:
            song = (json.loads(corps).get("response") or {}).get("song") or {}
        except ValueError:
            continue
        album = song.get("album")
        artiste = (album.get("artist") or {}).get("name") if isinstance(album, dict) else None
        if artiste:
            par_page[int(m.group(1))] = artiste
            if album.get("id"):
                par_album[int(album["id"])] = artiste
    return par_page, par_album


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    parser.add_argument("--brut", type=Path, help="autre magasin que data/brut/brut.db")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.enrichment.observation import Observation
    from src.persistence import brut
    from src.services.audit import _pistes, fiches_sans_participation
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    with closing(sqlite3.connect(f"file:{Path(dm.db_path).as_posix()}?mode=ro", uri=True)) as c:
        c.row_factory = sqlite3.Row
        cas = fiches_sans_participation(c, _pistes(c, None))
        ids = [i for k in cas for i in k.pistes]
        fiches = {
            r["id"]: r
            for r in c.execute(
                "SELECT t.id, t.title, t.album, t.genius_id, a.name AS artiste FROM tracks t "
                f"JOIN artists a ON a.id = t.artist_id WHERE t.id IN ({','.join(map(str, ids)) or '0'})"
            )
        }
        albums = collections.defaultdict(set)
        for r in c.execute(
            "SELECT rt.track_id, rel.identity_key FROM release_tracks rt "
            "JOIN releases rel ON rel.id = rt.release_id WHERE rel.identity_key LIKE 'genius:%'"
        ):
            albums[r["track_id"]].add(int(r["identity_key"].split(":", 1)[1]))

    par_page, par_album = signatures_du_brut(brut.parcourir("genius_api", args.brut))
    print(f"Brut : {len(par_page)} pages, {len(par_album)} albums signés")

    a_noter = []
    for i in ids:
        f = fiches[i]
        signe = par_page.get(f["genius_id"]) or next(
            (par_album[a] for a in sorted(albums[i]) if a in par_album), None
        )
        print(f"#{i} {f['artiste']} « {f['title']} » ({f['album'] or '—'}) : {signe or 'inconnu'}")
        if signe:
            a_noter.append((i, signe))
    print(f"\n{len(a_noter)} signatures à noter sur {len(ids)} fiches")
    if not args.apply or not a_noter:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0

    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    if not sauvegarde.create_backup("before_signer_albums_genius"):
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    for i, signe in a_noter:
        dm.upsert_observations(i, [Observation("album_artist", signe, "genius")])
    print(f"✅ {len(a_noter)} signatures notées")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
