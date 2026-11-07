"""Désactive les pages d'un HOMONYME gardées sur le profil d'un artiste.

    python scripts/desactiver_homonymes.py Sniper --fiches 20395,20479        # à blanc
    python scripts/desactiver_homonymes.py Sniper --fiches 20371 --disque 259551272 --apply
    python scripts/desactiver_homonymes.py Sniper --fiches 20395 --base copie.db

Un profil Genius peut mêler plusieurs artistes (Sniper, 1507 : le groupe français
et le Sniper de K-Rino, qu'une URI Spotify distingue, 2026-11-06). Le tri de ces
pages reste une décision humaine : ce script n'en devine aucune. Une fiche qu'un
autre artiste porte aussi (ligne sœur) est gardée : le groupe y est sans doute.

La désactivation est réversible (motif `homonyme`, origine `manuel`, réactivable
dans l'IHM) et aucune règle ne la lève. `--disque` refuse aussi le disque Deezer
(`memoriser_disque_refuse`), sinon le run Deezer suivant le reproposerait.
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

MOTIF = "homonyme"


def fiches_de(dm, artiste_id: int, ids: list[int]) -> dict[int, dict]:
    """`{id: fiche}` des seules fiches de l'artiste, avec leurs lignes sœurs."""
    trouvees: dict[int, dict] = {}
    with dm.engine.connect() as conn:
        for i in ids:
            f = (
                conn.execute(
                    text(
                        "SELECT id, title, album, primary_artist_name, genius_id FROM tracks "
                        "WHERE id = :i AND artist_id = :a"
                    ),
                    {"i": i, "a": artiste_id},
                )
                .mappings()
                .first()
            )
            if f is None:
                continue
            soeurs = (
                conn.execute(
                    text(
                        "SELECT a.name FROM tracks t JOIN artists a ON a.id = t.artist_id "
                        "WHERE t.genius_id = :g AND t.artist_id != :a"
                    ),
                    {"g": f["genius_id"], "a": artiste_id},
                )
                .scalars()
                .all()
                if f["genius_id"]
                else []
            )
            trouvees[i] = {**f, "soeurs": list(soeurs)}
    return trouvees


def titre_du_disque(dm, album_deezer: int) -> str | None:
    with dm.engine.connect() as conn:
        return conn.execute(
            text("SELECT title FROM releases WHERE deezer_album_id = :d"), {"d": album_deezer}
        ).scalar()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("artiste")
    parser.add_argument("--fiches", required=True, help="ID des fiches, séparés par des virgules")
    parser.add_argument("--disque", type=int, action="append", help="ID d'album Deezer à refuser")
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.corrections_fiches import memoriser_disque_refuse
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.disabled_tracks_manager import DisabledTracksManager
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")
    artiste = dm.get_artist_by_name(args.artiste)
    if artiste is None:
        print(f"❌ Artiste inconnu : {args.artiste}")
        return 1

    ids = [int(x) for x in args.fiches.split(",") if x.strip()]
    fiches = fiches_de(dm, artiste.id, ids)
    for i in ids:
        if i not in fiches:
            print(f"⛔ #{i} : pas une fiche de {artiste.name}, ignorée")
    retenues = []
    for i, f in fiches.items():
        if f["soeurs"]:
            print(f"⛔ #{i} « {f['title']} » : aussi chez {', '.join(f['soeurs'])}, gardée")
            continue
        retenues.append(i)
        print(f"#{i} « {f['title']} » ({f['album'] or '—'}) — {f['primary_artist_name'] or '—'}")
    disques = {d: titre_du_disque(dm, d) for d in args.disque or []}
    for d, titre in disques.items():
        print(f"Disque Deezer {d} « {titre or '?'} » à refuser")
    print(f"\n{len(retenues)} fiches à désactiver, {len(disques)} disques à refuser")
    if not args.apply or not (retenues or disques):
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0

    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    if not sauvegarde.create_backup("before_desactiver_homonymes"):
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    if retenues and not DisabledTracksManager().desactiver(artiste.name, retenues, MOTIF, "manuel"):
        print("❌ Désactivation impossible (fichier illisible ?) — rien de plus n'est écrit.")
        return 1
    for d, titre in disques.items():
        memoriser_disque_refuse(artiste.name, d, titre or "")
    print(f"✅ {len(retenues)} fiches désactivées, {len(disques)} disques refusés")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
