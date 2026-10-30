"""Rattrapage de `youtube_video_views` : la colonne qui porte le compteur
d'une AUTRE vidéo de la fiche reprend celui de la vidéo du lien, ou se vide.

    python scripts/rattraper_vues_du_lien.py                 # à blanc (défaut)
    python scripts/rattraper_vues_du_lien.py --apply         # backup + écriture
    python scripts/rattraper_vues_du_lien.py --base copie.db # sur une autre base
    python scripts/rattraper_vues_du_lien.py --sans-lien     # fiches SANS lien : vidées

Avant e5266ec, `update_video_views` écrivait dans `tracks.youtube_video_*` la
première vidéo mesurée quand la vidéo du lien n'avait pas de méta : vues et
nature d'une autre vidéo. Les colonnes ne suivent plus que la vidéo du lien,
mais ce qui avait été posé reste (`TrackRepository.vues_d_une_autre_video`).
Une valeur montrée fausse se vide quand rien ne mesure la vidéo du lien — elle
n'est pas remplacée par une autre.

`--sans-lien` : les fiches SANS lien YouTube qui gardent vues et nature d'une
vidéo (posées avant 444a8a8) se vident (décision utilisateur 2026-10-30).
"""

import argparse
import collections
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    parser.add_argument("--sans-lien", action="store_true", help="vider les fiches sans lien")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    if args.sans_lien:
        return _sans_lien(dm, args)
    cas = dm.vues_d_une_autre_video()
    corriges = [c for c in cas if c["juste"]]
    print(
        f"{len(cas)} fiche(s) aux vues d'une autre vidéo : {len(corriges)} reprennent les vues "
        f"de la vidéo du lien, {len(cas) - len(corriges)} se vident "
        f"(dont {sum(1 for c in cas if not c['juste'] and c['nature'] and not c['nature_apres'])}"
        " avec leur nature)"
    )
    for artiste, n in collections.Counter(c["artiste"] for c in cas).most_common():
        print(f"  {artiste} : {n}")
    for c in cas:
        apres = (
            f"{c['juste'][0]} (relevé {c['juste'][1]})"
            if c["juste"]
            else "vidée (lien jamais relevé)"
        )
        nature = f" ; nature {c['nature']} → {c['nature_apres']}" * (
            c["nature"] != c["nature_apres"]
        )
        print(
            f"  #{c['track_id']} {c['artiste']} — {c['titre']} : lien {c['video_lien']}, "
            f"vues {c['vues']} (= {c['autre_video']}) → {apres}{nature}"
        )

    if not args.apply or not cas:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not _sauvegarder(dm, args, "before_rattraper_vues_du_lien"):
        return 1
    ecrites = dm.rattraper_vues_du_lien(cas)
    reste = dm.vues_d_une_autre_video()
    print(f"✅ {ecrites} fiche(s) écrite(s) ; reste {len(reste)} cas.")
    return 0 if not reste else 2


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


def _sans_lien(dm, args) -> int:
    cas = dm.vues_sans_lien()
    print(
        f"{len(cas)} fiche(s) sans lien YouTube gardent vues ou nature "
        f"({sum(c['vues'] or 0 for c in cas):,} vues) : vidées"
    )
    for artiste, n in collections.Counter(c["artiste"] for c in cas).most_common():
        print(f"  {artiste} : {n}")
    for c in sorted(cas, key=lambda c: -(c["vues"] or 0))[:15]:
        print(f"  #{c['track_id']} {c['artiste']} — {c['titre']} : {c['vues']} ({c['nature']})")
    if not args.apply or not cas:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not _sauvegarder(dm, args, "before_vider_vues_sans_lien"):
        return 1
    ecrites = dm.vider_vues_sans_lien([c["track_id"] for c in cas])
    reste = dm.vues_sans_lien()
    print(f"✅ {ecrites} fiche(s) vidée(s) ; reste {len(reste)} cas.")
    return 0 if not reste else 2


if __name__ == "__main__":
    sys.exit(main())
