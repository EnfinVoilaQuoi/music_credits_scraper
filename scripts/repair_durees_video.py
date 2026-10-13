"""Retire les durées tirées d'une vidéo qui n'est pas le morceau (CORRECTIFS 2.5).

Rejoue sur la base la règle de la phase 1 (`duree_youtube.duree_a_declarer`) :
une vidéo ORDINAIRE ne donne sa durée que si elle nomme le morceau (le juge du
panneau, `revue.video_nomme`) et n'est ni un extrait ni une émission
(`duree_youtube.NON_MORCEAU`). Les durées `youtube_video` qu'aucune vidéo de la
fiche ne justifie plus sont retirées par l'écrivain `retirer_mesures_source`
(sœurs comprises), et la colonne est ré-arbitrée sur les sources restantes —
ou vidée : « LEGO » ne garde pas les 3 181 s d'un Planète Rap.

    python scripts/repair_durees_video.py            # dry-run (défaut)
    python scripts/repair_durees_video.py --apply    # backup + écriture
"""

from __future__ import annotations

import argparse
import sys
from types import SimpleNamespace

from sqlalchemy import text

from src.services.revue import video_nomme
from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.duree_youtube import NON_MORCEAU, SOURCE_VIDEO, est_un_clip


def candidates(dm) -> list[dict]:
    with dm.engine.connect() as conn:
        lignes = (
            conn.execute(
                text(
                    "SELECT t.id, t.title, t.duration, a.name AS artiste, o.value "
                    "FROM observations o JOIN tracks t ON t.id = o.track_id "
                    "JOIN artists a ON a.id = t.artist_id "
                    "WHERE o.field = 'duration' AND o.source = :s"
                ),
                {"s": SOURCE_VIDEO},
            )
            .mappings()
            .all()
        )
        videos: dict[int, list] = {}
        for r in conn.execute(text("SELECT track_id, title, source FROM track_videos")).mappings():
            if r["title"]:
                videos.setdefault(r["track_id"], []).append(
                    SimpleNamespace(title=r["title"], source=r["source"])
                )
    faits = []
    for r in lignes:
        fiche = SimpleNamespace(title=r["title"])
        vids = videos.get(r["id"], [])
        if not any(
            not NON_MORCEAU.search(v.title)
            and not est_un_clip(v.title, fiche.title)
            and video_nomme(fiche, v)
            for v in vids
        ):
            faits.append({**r, "video": vids[0].title if vids else None})
    return faits


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    a = p.parse_args(argv)
    dm = DataManager()
    faits = candidates(dm)
    for f in sorted(faits, key=lambda x: (x["artiste"], x["title"])):
        retenue = " (en colonne)" if str(f["duration"]) == str(f["value"]) else ""
        print(
            f"  [{f['artiste']}] #{f['id']} « {f['title']} » {f['value']} s{retenue} ← « {f['video']} »"
        )
    print(f"\n{len(faits)} durée(s) vidéo à retirer")
    if not a.apply:
        print("(dry-run — relancer avec --apply pour écrire)")
        return 0
    backup = get_backup_manager().create_backup("before_repair_durees_video")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"💾 Backup : {backup}")
    n = sum(dm.retirer_mesures_source(f["id"], SOURCE_VIDEO, champs=("duration",)) for f in faits)
    print(f"✅ {n} observation(s) retirée(s), durées ré-arbitrées")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
