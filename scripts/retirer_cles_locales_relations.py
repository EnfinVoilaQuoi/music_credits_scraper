"""Retire des relations stockées (`tracks.relationships`) les clés locales à la
machine — le `cover_path` des samples, posé par `media_enricher` sur UNE ligne
et qui faisait diverger les lignes sœurs (`soeurs_divergentes` 10 → 48 entre
le 2026-10-12 et le 2026-10-19, 39 familles chez Kanye West).

    python scripts/retirer_cles_locales_relations.py            # à blanc (défaut)
    python scripts/retirer_cles_locales_relations.py --apply    # backup + écriture

Puis `python scripts/realigner_soeurs.py` (à blanc, puis `--apply`) pour les
familles qui restent divergentes, et l'audit :
`python -m src.cli audit --detail soeurs_divergentes`.
"""

import argparse
import sys

from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.logger import configurer_journaux


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    args = parser.parse_args()
    configurer_journaux()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    dm = DataManager()
    fiches = dm.retirer_cles_locales_des_relations()
    print(f"{len(fiches)} fiche(s) portent une clé locale dans leurs relations")
    if not args.apply:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not fiches:
        return 0
    backup = get_backup_manager().create_backup("before_retirer_cles_locales_relations")
    print(f"💾 Backup : {backup}")
    print(f"✅ {len(dm.retirer_cles_locales_des_relations(appliquer=True))} fiche(s) réécrite(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
