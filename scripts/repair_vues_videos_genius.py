"""Retire des streams YouTube Music les vues des vidéos liées par Genius qui ne
sont pas le morceau (décision utilisateur 2026-10-10, `services.vues_comptees`).

Le lien reste sur la fiche (contexte : la pub, le podcast, le sketch où le
morceau a été montré) ; seul le TOTAL est recalculé par l'écrivain
`recalculer_vues_ytm`, hors vidéos écartées et hors vidéos partagées avec une
autre fiche — vidé s'il ne reste rien. Une vidéo validée (✔️ dans la fiche, ou
« ✓ Normal » dans le panneau) compte toujours.

    python scripts/repair_vues_videos_genius.py            # dry-run (défaut)
    python scripts/repair_vues_videos_genius.py --apply    # backup + écriture
"""

from __future__ import annotations

import argparse
import sys

from src.services.vues_comptees import cles_validees, videos_exclues
from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager


def plan(dm) -> list[tuple]:
    """(artiste, fiche, vidéos écartées, vues écartées)."""
    faits = []
    for nom in dm.get_artist_names():
        artiste = dm.get_artist_by_name(nom)
        valides = cles_validees(dm, artiste.id)
        for t in dm.get_artist_tracks(artiste.id):
            exclues = videos_exclues(t, t.videos or [], valides)
            vues = sum(v.views or 0 for v in t.videos or [] if v.video_id in exclues)
            if exclues and vues:
                faits.append((nom, t, exclues, vues))
    return faits


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    a = p.parse_args(argv)
    dm = DataManager()
    faits = plan(dm)
    for nom, t, exclues, vues in sorted(faits, key=lambda f: -f[3]):
        titres = ", ".join(f"« {v.title} »" for v in t.videos if v.video_id in exclues)
        print(f"  [{nom}] #{t.id} « {t.title} » −{vues:,} vues ← {titres[:110]}")
    print(f"\n{len(faits)} fiche(s), {sum(f[3] for f in faits):,} vues écartées des totaux")
    if not a.apply:
        print("(dry-run — relancer avec --apply pour écrire)")
        return 0
    backup = get_backup_manager().create_backup("before_repair_vues_videos_genius")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"💾 Backup : {backup}")
    for _nom, t, exclues, _vues in faits:
        dm.recalculer_vues_ytm(t.id, exclues)
    print(f"✅ {len(faits)} total(aux) YouTube Music recalculé(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
