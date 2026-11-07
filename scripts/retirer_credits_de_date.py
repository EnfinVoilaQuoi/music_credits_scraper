"""Retire les « crédits » qui sont des métadonnées de la page Genius : « Released in »
(1 325 lignes dont le nom est une date, mesuré le 2026-11-06), « Expected » (22).

    python scripts/retirer_credits_de_date.py                 # à blanc (défaut)
    python scripts/retirer_credits_de_date.py --apply         # backup + écriture
    python scripts/retirer_credits_de_date.py --base copie.db # sur une autre base

Le scrape ne les produit plus (`credit_roles.LIBELLES_HORS_CREDITS`) ; la date de la
fiche n'en vient pas (aucun lecteur, et aucune de ces fiches n'est sans date).
Seul le rôle `Other` part : une année en Producer, Writer ou Featured Artist
(« 1993 », « 2080 ») est peut-être un nom. Listée, jamais retirée.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    cas = dm.credits_hors_credits()
    print(f"{len(cas)} ligne(s) à retirer, sur {len({c['track_id'] for c in cas})} fiche(s) :")
    for detail, n in Counter(c["role_detail"] for c in cas).most_common():
        print(f"  {detail} : {n}")
    for c in cas[:15]:
        print(f"  #{c['track_id']} {c['role_detail']} : {c['name']!r}")

    laissees = [
        c for c in dm.credits_au_nom_d_annee() if c["role"] != "Other" or c["id"] not in _ids(cas)
    ]
    print(f"{len(laissees)} année(s) en nom laissée(s), rien ne prouve que ce n'est pas un nom :")
    for c in laissees:
        print(
            f"  (laissé) #{c['track_id']} {c['title']} — {c['role']}"
            f"{' / ' + c['role_detail'] if c['role_detail'] else ''} : {c['name']!r}"
        )

    if not args.apply or not cas:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not _sauvegarder(dm, args, "before_retirer_credits_de_date"):
        return 1
    _, retires = dm.reclasser_credits({}, [c["id"] for c in cas])
    reste = dm.credits_hors_credits()
    print(f"✅ {retires} ligne(s) retirée(s) ; reste {len(reste)}.")
    return 0 if not reste else 2


def _ids(cas) -> set[int]:
    return {c["id"] for c in cas}


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
