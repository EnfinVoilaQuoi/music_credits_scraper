"""Retire le disque Deezer d'un HOMONYME et le refuse (famille d'audit `disque_homonyme`).

    python scripts/retirer_disque_homonyme.py "Anton Serra"                  # à blanc, tout ce que l'audit voit
    python scripts/retirer_disque_homonyme.py "Anton Serra" --disque 6294406 # à blanc, ce disque
    python scripts/retirer_disque_homonyme.py "Anton Serra" --apply          # backup + écriture
    python scripts/retirer_disque_homonyme.py "Anton Serra" --base copie.db
    python scripts/retirer_disque_homonyme.py "Anton Serra" --oublier 6294406 --apply

Un profil Deezer peut mêler deux homonymes (Anton Serra, 1666208 : le rappeur et
un flûtiste) : les écarts Deezer ont créé d'office les fiches du second le
2026-11-04. Ne part qu'un disque dont AUCUNE fiche n'a de page Genius ni d'ID
Spotify. Le disque est REFUSÉ (`memoriser_disque_refuse`), sinon le run Deezer
suivant le recréerait ; `--oublier` lève le refus, et le run suivant le repropose.
"""

import argparse
import sys
from pathlib import Path

from sqlalchemy import text


def disques_de(dm, artiste_id: int) -> dict[int, dict]:
    """`{album Deezer: {titre, fiches}}` : les fiches de l'artiste par parution
    Deezer, chacune avec ce qui la lierait à l'artiste."""
    with dm.engine.connect() as conn:
        lignes = conn.execute(
            text(
                "SELECT r.id AS release_id, r.deezer_album_id, r.title AS disque, t.id, t.title, "
                "t.genius_id, t.spotify_id FROM releases r "
                "JOIN release_tracks rt ON rt.release_id = r.id "
                "JOIN tracks t ON t.id = rt.track_id "
                "WHERE r.deezer_album_id IS NOT NULL AND t.artist_id = :a "
                "ORDER BY r.title, t.title"
            ),
            {"a": artiste_id},
        ).mappings()
        disques: dict[int, dict] = {}
        for r in lignes:
            d = disques.setdefault(
                int(r["deezer_album_id"]),
                {"titre": r["disque"], "release_id": r["release_id"], "fiches": []},
            )
            d["fiches"].append(dict(r))
    return disques


def sans_aucun_lien(disque: dict) -> bool:
    """PUR. Aucune fiche du disque n'a de page Genius ni d'ID Spotify."""
    return not any(f["genius_id"] or f["spotify_id"] for f in disque["fiches"])


def supprimer_parution_vide(dm, release_id: int) -> bool:
    """La parution que plus aucune fiche ne porte."""
    with dm.engine.begin() as conn:
        if conn.execute(
            text("SELECT 1 FROM release_tracks WHERE release_id = :r LIMIT 1"), {"r": release_id}
        ).first():
            return False
        conn.execute(
            text("DELETE FROM release_identifiers WHERE release_id = :r"), {"r": release_id}
        )
        conn.execute(text("DELETE FROM releases WHERE id = :r"), {"r": release_id})
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("artiste")
    parser.add_argument("--disque", type=int, action="append", help="ID d'album Deezer")
    parser.add_argument("--oublier", type=int, help="lève le refus de ce disque")
    parser.add_argument("--min-fiches", type=int, default=3, help="sans --disque (défaut : 3)")
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.corrections_fiches import memoriser_disque_refuse, oublier_disque_refuse
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")
    artiste = dm.get_artist_by_name(args.artiste)
    if artiste is None:
        print(f"❌ Artiste inconnu : {args.artiste}")
        return 1

    if args.oublier:
        if not args.apply:
            print(f"Refus du disque {args.oublier} à lever (à blanc — --apply pour écrire)")
            return 0
        fait = oublier_disque_refuse(artiste.name, args.oublier)
        print("✅ Refus levé" if fait else "Rien à lever")
        return 0

    disques = disques_de(dm, artiste.id)
    if args.disque:
        cibles = {i: disques[i] for i in args.disque if i in disques}
        for i in args.disque:
            if i not in disques:
                print(f"⚠️ Disque {i} : aucune fiche de {artiste.name}")
    else:
        cibles = {
            i: d
            for i, d in disques.items()
            if len(d["fiches"]) >= args.min_fiches and sans_aucun_lien(d)
        }
    retenus = {}
    for i, d in sorted(cibles.items(), key=lambda x: x[1]["titre"]):
        if not sans_aucun_lien(d):
            print(
                f"⛔ « {d['titre']} » ({i}) : une fiche a une page Genius ou un ID Spotify, gardé"
            )
            continue
        retenus[i] = d
        print(f"\n« {d['titre']} » (album Deezer {i}) : {len(d['fiches'])} fiches")
        for f in d["fiches"]:
            print(f"   #{f['id']} {f['title']}")
    if not args.apply or not retenus:
        print("\n(à blanc — relancer avec --apply pour écrire)")
        return 0

    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    if not sauvegarde.create_backup("before_retirer_disque_homonyme"):
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    fiches = parutions = 0
    for i, d in retenus.items():
        memoriser_disque_refuse(artiste.name, i, d["titre"])
        for f in d["fiches"]:
            fiches += bool(dm.delete_track(f["id"]))
        parutions += supprimer_parution_vide(dm, d["release_id"])
    print(f"\n✅ {fiches} fiches supprimées, {parutions} parutions, {len(retenus)} disques refusés")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
