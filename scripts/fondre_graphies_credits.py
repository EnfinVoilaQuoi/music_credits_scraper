"""Rattrapage : un crédit, une ligne — les graphies d'une même personne se fondent.

Même personne, même rôle, même détail sur deux lignes d'une fiche (« Al Tarba »
de Discogs, « Al’Tarba » de Genius) : une seule reste, à la graphie Genius ; les
autres sources passent en confirmation, leur graphie est gardée
(`credits.graphies`, e71 ; décision utilisateur 2026-11-25). Le juge est celui
des écrivains (`credit_normalize.graphies_a_fondre`).

    python scripts/fondre_graphies_credits.py [--base <copie>] [--liste]   # à blanc
    python scripts/fondre_graphies_credits.py --apply                      # backup, puis écrit
"""

import argparse
import sys
from collections import Counter
from pathlib import Path


def bilan(a_fondre: dict[int, list[tuple]]) -> tuple[Counter, Counter]:
    """`(par sources, par paire de noms)` ; chaque paire est comptée par fiche."""
    sources, paires = Counter(), Counter()
    for groupes in a_fondre.values():
        for gagnant, perdants in groupes:
            sources["+".join(sorted({x.source or "?" for x in (gagnant, *perdants)}))] += 1
            for p in perdants:
                paires[gagnant.name, p.name, p.source] += 1
    return sources, paires


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    parser.add_argument("--liste", action="store_true", help="toutes les paires de noms")
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

    a_fondre = dm.fiches_a_graphies_a_fondre()
    sources, paires = bilan(a_fondre)
    print(f"{sum(sources.values())} groupe(s) sur {len(a_fondre)} fiche(s)")
    for libelle, n in sources.most_common():
        print(f"  {libelle} : {n}")
    for (garde, fondu, source), n in paires.most_common(None if args.liste else 20):
        print(f"  {n:4} × « {fondu} » ({source}) → « {garde} »")
    if not args.apply:
        print("\n(à blanc — relancer avec --apply pour écrire)")
        return 0
    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup("before_fondre_graphies_credits")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"💾 Backup : {backup}")
    print(f"✅ {dm.fondre_graphies_credits(a_fondre)} ligne(s) fondue(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
