"""Répare les textes Genius abîmés par un encodage (`src.utils.encodage`) : titres,
paroles et anecdotes qui portent un caractère de contrôle C1 (U+0080 à U+009F).

    python scripts/reparer_encodage_genius.py                 # à blanc (défaut)
    python scripts/reparer_encodage_genius.py --apply         # backup + écriture
    python scripts/reparer_encodage_genius.py --base copie.db # sur une autre base

Cas d'origine (2026-11-06) : 3 titres (« Le myst\\x8fre feminin »…), 28 paroles,
1 anecdote. Hors périmètre, listés mais laissés : le LRC de LRCLIB et le titre de
vidéo YouTube, qui ne viennent pas de Genius.
"""

import argparse
import sys
from pathlib import Path

CHAMPS = {"lyrics": "paroles", "anecdotes": "anecdote"}


def _extrait(avant: str, apres: str) -> str:
    """Le premier endroit qui change, avant → après."""
    i = next((k for k, (a, b) in enumerate(zip(avant, apres, strict=False)) if a != b), 0)
    debut = max(0, i - 15)
    return f"{avant[debut:i + 15]!r} → {apres[debut:i + 15]!r}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.encodage import reparer_c1
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    cas = dm.textes_a_caracteres_c1()
    for c in cas:
        print(f"  #{c['id']} {c['champ']} : {_extrait(c['valeur'], reparer_c1(c['valeur']))}")
    print(f"{len(cas)} texte(s) à réparer")
    for c in dm.textes_c1_hors_genius():
        print(f"  (laissé) {c}")

    if not args.apply or not cas:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not _sauvegarder(dm, args, "before_reparer_encodage_genius"):
        return 1
    echecs = 0
    for c in cas:
        neuf = reparer_c1(c["valeur"])
        if c["champ"] == "title":
            ok = dm.rename_track(c["id"], neuf)
        else:
            ok = dm.corriger_texte_de_page(c["id"], **{c["champ"]: neuf})
        echecs += 0 if ok else 1
    reste = dm.textes_a_caracteres_c1()
    print(f"✅ {len(cas) - echecs} texte(s) réparé(s) ; reste {len(reste)}.")
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


if __name__ == "__main__":
    sys.exit(main())
