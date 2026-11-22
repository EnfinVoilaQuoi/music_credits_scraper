"""Consigne les fusions faites par `editions.absorber` sans ligne `fusionner_dans`.

    python scripts/consigner_fusions_d_editions.py                 # à blanc (défaut)
    python scripts/consigner_fusions_d_editions.py --apply         # backup + écriture
    python scripts/consigner_fusions_d_editions.py --base copie.db # sur une autre base

Une fusion passe par `data/corrections/fiches.json` (règle du 2026-11-19) : non
consignée, elle n'est ni exportée (`notes/scraper/decisions/corrections.json`)
ni lue par les juges des fusions (`genius_ids_absorbes`, `titres_absorbes`).
`editions.absorber`, appelé par des one-shot (`scripts/ponctuels/fusions_20261110.py` :
11 fusions validées le 2026-11-10, Sofiane, Kalash Criminel, Kanye West, The
Game), ne la consignait pas : seule la ligne d'édition (`source = 'fusion'`,
page Genius de la fiche absorbée) en garde la trace.

Visée : une ligne `track_editions` de source `fusion` qui porte une page Genius,
sur une fiche à page Genius, sans `fusionner_dans` de cette page pour l'artiste
de la fiche. L'écriture passe par `corrections_fiches.memoriser_fusion`.
"""

import argparse
import sys
from pathlib import Path


def a_consigner(editions: list[tuple], consignees: set[tuple]) -> list[dict]:
    """PUR. `editions` : (fiche, artiste, titre, page gardée, titre absorbé, page
    absorbée) ; `consignees` : {(artiste, clé de fiche « g<page> »)} déjà en
    `fusionner_dans`. Une entrée par (artiste, page absorbée)."""
    vus, cas = set(), []
    for tid, artiste, titre, garde, titre_absorbe, absorbee in editions:
        if not garde or not absorbee or int(garde) == int(absorbee):
            continue
        cle = (artiste, f"g{int(absorbee)}")
        if cle in consignees or cle in vus:
            continue
        vus.add(cle)
        cas.append(
            {
                "fiche": tid,
                "artiste": artiste,
                "titre": titre,
                "titre_absorbe": titre_absorbe,
                "absorbee": {"genius_id": int(absorbee)},
                "gardee": {"genius_id": int(garde)},
            }
        )
    return cas


def _cas(dm) -> list[dict]:
    from sqlalchemy import text

    with dm.engine.connect() as conn:
        editions = conn.execute(
            text(
                "SELECT t.id, a.name, t.title, t.genius_id, e.title, e.genius_id "
                "FROM track_editions e JOIN tracks t ON t.id = e.track_id "
                "JOIN artists a ON a.id = t.artist_id "
                "WHERE e.source = 'fusion' AND e.genius_id IS NOT NULL "
                "AND t.genius_id IS NOT NULL ORDER BY a.name, t.id"
            )
        ).all()
        consignees = {
            (r[0], r[1])
            for r in conn.execute(
                text("SELECT artiste, cle_fiche FROM corrections WHERE action = 'fusionner_dans'")
            )
        }
    return a_consigner([tuple(r) for r in editions], consignees)


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

    cas = _cas(dm)
    for c in cas:
        print(
            f"  [{c['artiste']}] #{c['fiche']} « {c['titre']} » (g{c['gardee']['genius_id']}) "
            f"← « {c['titre_absorbe']} » (g{c['absorbee']['genius_id']})"
        )
    print(f"{len(cas)} fusion(s) à consigner.")
    if not args.apply or not cas:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not _sauvegarder(dm, args, "before_consigner_fusions_d_editions"):
        return 1
    from src.utils.corrections_fiches import memoriser_fusion

    for c in cas:
        memoriser_fusion(c["artiste"], c["absorbee"], c["gardee"])
    reste = _cas(dm)
    print(f"✅ {len(cas) - len(reste)} fusion(s) consignée(s) ; reste {len(reste)}.")
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
