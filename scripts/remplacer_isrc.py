"""Pose À LA MAIN l'ISRC choisi d'un enregistrement (2026-10-29).

L'ISRC est une colonne d'identité : un verdict la remplit, ne la remplace
jamais. Quand deux ISRC sont observés pour le même enregistrement (« Tradition »
d'Isha et PLK : Deezer l'a publié deux fois, sous deux ISRC), les lignes sœurs
peuvent garder chacune le sien. Ce script pose l'ISRC choisi sur la fiche ET
ses sœurs, sans retirer aucune observation ; seul un ISRC déjà observé dans la
famille est accepté.

    python scripts/remplacer_isrc.py --fiche 7977 --isrc BEDO12000073
    python scripts/remplacer_isrc.py --fiche 7977 --isrc BEDO12000073 --apply
"""

import argparse
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.track_soeurs import ids_soeurs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fiche", type=int, required=True, help="track_id")
    parser.add_argument("--isrc", required=True, help="l'ISRC à poser")
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    args = parser.parse_args()

    cible = args.isrc.strip().upper()
    dm = DataManager()
    with dm.engine.connect() as conn:
        genius_id = conn.execute(
            text("SELECT genius_id FROM tracks WHERE id = :t"), {"t": args.fiche}
        ).scalar()
        jumelles, _ = ids_soeurs(conn, args.fiche, genius_id)
        famille = [args.fiche, *jumelles]
        observes = set()
        for tid in famille:
            titre, isrc, artiste = conn.execute(
                text(
                    "SELECT t.title, t.isrc, a.name FROM tracks t "
                    "LEFT JOIN artists a ON a.id = t.artist_id WHERE t.id = :t"
                ),
                {"t": tid},
            ).one()
            obs = conn.execute(
                text(
                    "SELECT source, value FROM observations WHERE track_id = :t AND field = 'isrc'"
                ),
                {"t": tid},
            ).all()
            observes |= {str(v).strip().upper() for _, v in obs if v}
            fleche = "=" if str(isrc or "").upper() == cible else f"→ {cible}"
            print(f"#{tid} {artiste} — « {titre} » : {isrc} {fleche}  {dict(obs)}")
    if cible not in observes:
        print(f"❌ {cible} n'est observé sur aucune ligne de la famille : non posé")
        return 1
    if not args.apply:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    print(f"💾 Backup : {get_backup_manager().create_backup('before_remplacer_isrc')}")
    changees = dm.remplacer_isrc(args.fiche, cible)
    print(f"✅ {len(changees)} ligne(s) changée(s) : {changees}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
