"""Liens YouTube sans ligne `track_videos` : YouTube a-t-il encore la vidéo ?

    python scripts/constater_videos_mortes.py                     # à blanc : mesure et rapport
    python scripts/constater_videos_mortes.py --apply             # backup, pose les constats
    python scripts/constater_videos_mortes.py --apply --retirer   # et retire les liens morts

À blanc, seul YouTube est interrogé (~25 unités de quota). `--retirer` applique
la règle `video_morte` et elle seule, artiste par artiste (refus de règle,
journal des corrections, « ↩ Rétablir ») : à ne lancer qu'après décision de
l'utilisateur, jamais pendant un lot. Voir `services.videos_mortes`.
"""

import argparse
import sys
from collections import Counter

from src.services import revue, revue_auto, videos_mortes
from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.logger import configurer_journaux


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument(
        "--retirer", action="store_true", help="avec --apply : retire aussi les liens morts"
    )
    args = parser.parse_args()
    configurer_journaux()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from src.api.ytmusic_api import YTMusicAPI

    dm = DataManager()
    liens = videos_mortes.liens_sans_ligne(dm.engine)
    mesure = videos_mortes.mesurer(YTMusicAPI(), liens)
    fiches = [f for vid in mesure.absentes for f in liens[vid]]
    print(
        f"{len(liens)} vidéo(s) sans ligne : {len(mesure.vivantes)} vivante(s), "
        f"{len(mesure.absentes)} absente(s) ({len(fiches)} fiche(s)), "
        f"{len(mesure.sans_reponse)} sans réponse"
    )
    par_artiste = Counter(f["artiste"] for f in fiches)
    for nom, n in par_artiste.most_common():
        print(f"  {nom} : {n}")
    if not args.apply:
        print("(à blanc — relancer avec --apply pour poser les constats)")
        return 0
    backup = get_backup_manager().create_backup("before_constater_videos_mortes")
    print(f"💾 Backup : {backup}")
    videos_mortes.poser_constats(
        dm.engine,
        [(f["id"], vid) for vid in mesure.absentes for f in liens[vid]],
        [(f["id"], vid) for vid in mesure.vivantes for f in liens[vid]],
    )
    print(f"✅ {len(fiches)} constat(s) posé(s).")
    if not args.retirer:
        return 0
    revue.VIDEOS_MORTES_BRANCHEES = True
    retires = 0
    for nom in par_artiste:
        artiste = dm.get_artist_by_name(nom)
        artiste.tracks = dm.get_artist_tracks(artiste.id)
        bilan = revue_auto.corriger(dm, artiste, seulement=frozenset({"video_morte"}))
        retires += len(bilan.appliquees)
        for echec in bilan.echecs:
            print(f"  ⚠️ {nom} : {echec}")
    print(f"✅ {retires} lien(s) retiré(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
