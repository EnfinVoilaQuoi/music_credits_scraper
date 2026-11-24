"""Refuse la piste YouTube Music d'une fiche : celle que la recherche de paroles
a trouvée PAR LE TITRE et qui n'est pas le morceau (2026-11-24).

    python scripts/refuser_piste_ytm.py 31617                 # à blanc
    python scripts/refuser_piste_ytm.py 31617 --apply         # backup + écriture
    python scripts/refuser_piste_ytm.py 31617 --video BUxLXviXuLA --apply
    python scripts/refuser_piste_ytm.py 31617 --oublier BUxLXviXuLA --apply

La durée `ytmusic` vient du résultat de la recherche « artiste titre » ; sans
`--video`, la piste se relit dans le BRUT (`ytmusic_brut.video_de_la_duree`).
`--apply` : le refus (nature `piste_ytm`, tout l'enregistrement, lu par
`synced_lyrics_resolver` : ni durée, ni LRC, ni texte de cette piste) et le
retrait de l'observation de durée `ytmusic` (sœurs comprises, la colonne se
recalcule). « Enfants terribles » de Sinik : 248 s d'une autre piste, Spotify
232 s, 3:52 à l'écoute.
"""

import argparse
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("fiche", type=int, help="identifiant de la fiche")
    parser.add_argument("--video", help="videoId à refuser (sinon relu dans le brut)")
    parser.add_argument("--oublier", help="lève le refus de ce videoId")
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.api.ytmusic_brut import video_de_la_duree
    from src.utils import corrections_fiches as cf
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")
    from sqlalchemy import text

    with dm.engine.connect() as conn:
        aid = conn.execute(
            text("SELECT artist_id FROM tracks WHERE id = :t"), {"t": args.fiche}
        ).scalar()
    track = (
        next((t for t in dm.get_artist_tracks(aid) if t.id == args.fiche), None) if aid else None
    )
    if track is None:
        print(f"❌ Fiche inconnue : #{args.fiche}")
        return 1
    duree = next(
        (
            int(float(o.value))
            for o in dm.get_observations(track.id)
            if o.field == "duration" and o.source == "ytmusic"
        ),
        None,
    )
    print(f"#{track.id} « {track.title} » — durée ytmusic : {duree or '—'} s")

    if args.oublier:
        print(f"Refus de la piste YTM {args.oublier} à lever")
    else:
        video = args.video
        if video is None and duree:
            noms = (track.artist.name if track.artist else None, track.primary_artist_name)
            video = video_de_la_duree(noms, track.title, duree)
        if not video:
            print("❌ Piste YTM introuvable dans le brut : la donner par --video")
            return 1
        deja = video in cf.pistes_ytm_refusees(track)
        print(
            f"Piste refusée : https://music.youtube.com/watch?v={video}"
            + (" (déjà refusée)" if deja else "")
            + (f" ; observation ytmusic {duree} s retirée" if duree else "")
        )
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
    if not sauvegarde.create_backup("before_refuser_piste_ytm"):
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    if args.oublier:
        print(
            "✅ refus levé" if cf.oublier_piste_ytm_refusee(track, args.oublier) else "rien à lever"
        )
        return 0
    cf.memoriser_piste_ytm_refusee(track, video)
    if duree:
        dm.delete_observations(track.id, "duration", sources=["ytmusic"])
    print("✅ piste refusée" + (", durée ytmusic retirée" if duree else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
