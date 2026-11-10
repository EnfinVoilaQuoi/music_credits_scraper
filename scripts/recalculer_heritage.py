"""Rattrapage : les crédits HÉRITÉS que l'héritage d'aujourd'hui ne poserait plus.

    python scripts/recalculer_heritage.py                 # à blanc (défaut)
    python scripts/recalculer_heritage.py --apply         # backup + écriture
    python scripts/recalculer_heritage.py --base copie.db # sur une autre base

Jusqu'au 2026-11-10, l'héritage des crédits d'une version depuis son original
(`version_heritage.heriter`) n'était qu'une UNION : un crédit hérité restait quand
l'original le perdait (Genius l'a retiré ou scindé) ou quand la règle de sa famille
ne transmettait plus le rôle (une a cappella n'hérite pas de la production). Le
passage (`versions.heriter_versions`) le recalcule désormais ; ce script applique
le même recalcul à toute la base sans attendre un run.

Le juge est celui de l'audit (`audit.heritages_perimes`, invariant
`heritage_sans_source`) ; l'écriture passe par `retirer_credits_herites` (sœurs
comprises). Une version dont l'original n'est plus unique ou plus retrouvé n'est
pas touchée : rien ne dit d'où viennent ses crédits.
"""

import argparse
import sys
from contextlib import closing
from pathlib import Path


def a_retirer(db_path) -> list[tuple]:
    """`(fiche, socle, périmés)` des fiches à crédits hérités sans source."""
    from src.services import audit

    with closing(audit._connexion(db_path)) as c:
        return [
            (t, socle, perimes)
            for t, socle, _, perimes in audit.heritages_perimes(c, audit._pistes(c, None))
            if socle is not None and perimes
        ]


def rapport(fiches) -> list[str]:
    lignes = []
    for t, socle, perimes in fiches:
        lignes.append(f"#{t['id']} {t['artiste']} « {t['title']} » ← #{socle.id} « {socle.title} »")
        lignes += [f"    − {nom} ({role}) : {motif}" for nom, role, motif in perimes]
    n = sum(len(p) for _, _, p in fiches)
    lignes.append(f"\n{n} crédit(s) hérité(s) sans source sur {len(fiches)} fiche(s)")
    return lignes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    fiches = a_retirer(dm.db_path)
    print("\n".join(rapport(fiches)))
    if not args.apply or not fiches:
        if fiches:
            print("\n(à blanc — relancer avec --apply pour écrire)")
        return 0
    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup("before_recalculer_heritage")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"💾 Backup : {backup}")
    retires = sum(
        dm.retirer_credits_herites(t["id"], [(nom, role) for nom, role, _ in perimes])
        for t, _, perimes in fiches
    )
    print(f"✅ {retires} ligne(s) de crédit hérité retirée(s), sœurs comprises")
    return 0


if __name__ == "__main__":
    sys.exit(main())
