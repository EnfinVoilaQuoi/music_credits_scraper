"""Applique à l'EXISTANT les règles de `src/utils/pages_genius.py` (2026-09-24).

L'import les applique désormais aux nouveaux morceaux ; ce script rattrape la
base, sans aucun appel réseau (les relations Genius sont déjà en base) :

  · supprime les pages qui ne sont pas des morceaux (traductions, livrets —
    « A7 [Livret] » portait l'ID Spotify et les streams d'« A7 ») ;
  · pose le rôle secondaire « Cover » / « Remix » sur les versions de tiers d'un
    morceau de l'artiste (Justice Der « Astrothunder » : « Writer » → « Cover »).

    python scripts/backfill_pages_genius.py            # dry-run
    python scripts/backfill_pages_genius.py --apply    # backup + écriture
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.config import DATA_DIR
from src.utils.pages_genius import page_non_morceau, role_de_version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrire (défaut : dry-run)")
    args = parser.parse_args()

    base = sqlite3.connect(f"file:{Path(DATA_DIR) / 'music_credits.db'}?mode=ro", uri=True)
    lignes = base.execute(
        "SELECT t.id, a.name, t.title, t.secondary_role, t.relationships "
        "FROM tracks t JOIN artists a ON a.id = t.artist_id ORDER BY a.name, t.title"
    ).fetchall()
    a_supprimer = [(tid, art, titre, page_non_morceau(titre)) for tid, art, titre, *_ in lignes]
    a_supprimer = [x for x in a_supprimer if x[3]]
    roles = []
    for tid, art, titre, role, rels in lignes:
        nouveau = role_de_version(json.loads(rels) if rels else [], art, role)
        if nouveau and nouveau != role:
            roles.append((tid, art, titre, role, nouveau))

    dm = None
    if args.apply:
        from src.utils.data_manager import DataManager
        from src.utils.database_backup import get_backup_manager

        dm = DataManager()
        print(f"💾 Backup : {get_backup_manager().create_backup('before_backfill_pages_genius')}")

    print(f"── Pages qui ne sont pas des morceaux : {len(a_supprimer)}")
    for tid, art, titre, nature in a_supprimer:
        fait = f"  ⇒ supprimée : {dm.delete_track(tid)}" if dm else ""
        print(f"   [{nature}] {art} — « {titre} »{fait}")
    print(f"── Rôles Cover / Remix : {len(roles)}")
    for tid, art, titre, avant, apres in roles:
        fait = f"  ⇒ {dm.record_secondary_role(tid, apres)}" if dm else ""
        print(f"   {art} — « {titre} » : {avant} → {apres}{fait}")
    if dm is None:
        print("\n(dry-run — relancer avec --apply pour écrire)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
