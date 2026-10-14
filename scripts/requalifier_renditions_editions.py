"""Retire les « renditions » Kworb qui sont devenues des ÉDITIONS du morceau.

Décision utilisateur du 2026-10-15 : « Titre (Bonus Track) » est « Titre »
(même enregistrement rangé en fin de disque) — ses streams s'ajoutent au
total, comme ceux d'un « Radio Edit ». Avant, Kworb rangeait l'ID Spotify
« DKR - Bonus Track » en RENDITION : une ligne affichée après le total, hors
total (Booba « E.L.E.P.H.A.N.T - Bonus Track », 41 M). Le run Kworb suivant
note désormais cet ID comme édition et compte ses streams ; l'ancienne ligne
de rendition resterait, figée. Ce script la retire.

Une ligne n'est retirée que si son libellé est une édition de diffusion du
morceau qui la porte (`editions.socle_de`) — jamais une vraie autre prise
(live, acoustique, démo…).

    python scripts/requalifier_renditions_editions.py            # à blanc (défaut)
    python scripts/requalifier_renditions_editions.py --apply    # backup + écriture
"""

import argparse
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from src.services.editions import socle_de
from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager


def a_retirer(dm) -> list[dict]:
    with dm.engine.connect() as conn:
        lignes = conn.execute(
            text(
                "SELECT s.track_id, s.spotify_id, s.label, s.streams, t.title "
                "FROM track_spotify_ids s JOIN tracks t ON t.id = s.track_id "
                "WHERE s.kind = 'rendition' AND s.label IS NOT NULL"
            )
        ).mappings()
        return [
            dict(r)
            for r in lignes
            if socle_de(r["label"], [_Fiche(r["track_id"], r["title"])]) is not None
        ]


class _Fiche:
    def __init__(self, tid, titre):
        self.id, self.title = tid, titre


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    args = parser.parse_args()

    dm = DataManager()
    lignes = a_retirer(dm)
    for r in lignes:
        print(
            f"   #{r['track_id']} « {r['title']} » ← « {r['label']} » ({r['streams'] or 0:,} streams)"
        )
    print(f"\n{len(lignes)} rendition(s) devenue(s) édition(s)")
    if not args.apply or not lignes:
        print(
            "(à blanc — relancer avec --apply pour écrire ; le run Kworb suivant compte leurs streams)"
        )
        return 0
    backup = get_backup_manager().create_backup("before_requalifier_renditions_editions")
    if not backup:
        print("❌ Backup impossible — abandon.")
        return 1
    print(f"💾 Backup : {backup}")
    with dm.engine.begin() as conn:
        for r in lignes:
            conn.execute(
                text(
                    "DELETE FROM track_spotify_ids WHERE track_id = :t AND spotify_id = :s "
                    "AND kind = 'rendition'"
                ),
                {"t": r["track_id"], "s": r["spotify_id"]},
            )
    print(f"✅ {len(lignes)} ligne(s) retirée(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
