"""Rattrapage de l'invariant `spotify_principal` : l'ID Spotify PRINCIPAL
(`tracks.spotify_id`) reçoit sa ligne dans `track_spotify_ids`, et
`is_primary` recopie la colonne.

    python scripts/aligner_ids_spotify_principaux.py                 # à blanc (défaut)
    python scripts/aligner_ids_spotify_principaux.py --apply         # backup + écriture
    python scripts/aligner_ids_spotify_principaux.py --base copie.db # sur une autre base

Constat du 2026-10-30 : 88 fiches d'A2H portaient un ID sans sa ligne. Les
journaux du 2026-09-08 (11:44-12:54) montrent chacun de ces ID posé par le
provider SongBPM (« Spotify ID ajouté depuis SongBPM »), quelques heures avant
que `add_spotify_id` ne marque ses ID à écrire (`_spotify_ids_pending`,
dc58835) : la colonne était écrite, la table jamais.

La provenance de la ligne posée est lue dans ces journaux quand ils la disent
(`--sans-journaux` pour s'en passer), sinon NULL (« non tracée ») — une
provenance qu'on ne connaît pas ne s'invente pas. ADDITIF : rien n'est retiré.
"""

import argparse
import collections
import re
import sys
from pathlib import Path

#: Le seul producteur historique qui écrivait la colonne sans la ligne :
#: SongBPM, avant e6b5541 (2026-09-28) qui lui a retiré l'écriture.
_JOURNAL_PRODUCTEURS = {
    re.compile(r"Spotify ID ajouté depuis SongBPM: ([0-9A-Za-z]{22})"): "songbpm",
}


def provenances_des_journaux(dossier: Path, ids: set[str]) -> dict[str, str]:
    """{spotify_id: source} pour les `ids` dont un journal nomme le producteur
    (première mention retenue, journaux lus dans l'ordre des dates)."""
    trouvees: dict[str, str] = {}
    for journal in sorted(dossier.glob("*_scraper.log")):
        with journal.open(encoding="utf-8", errors="replace") as f:
            for ligne in f:
                for motif, source in _JOURNAL_PRODUCTEURS.items():
                    m = motif.search(ligne)
                    if m and m.group(1) in ids and m.group(1) not in trouvees:
                        trouvees[m.group(1)] = source
        if len(trouvees) == len(ids):
            break
    return trouvees


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    parser.add_argument(
        "--journaux", type=Path, help="dossier des journaux (défaut : celui de la config)"
    )
    parser.add_argument(
        "--sans-journaux", action="store_true", help="ne pas lire la provenance dans les journaux"
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.config import LOGS_DIR
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    ecarts = dm.ids_spotify_principaux_desalignes()
    sans_ligne = {e["spotify_id"] for e in ecarts if e["spotify_id"] and e["ligne"] is None}
    provenances = (
        {}
        if args.sans_journaux
        else provenances_des_journaux(args.journaux or LOGS_DIR, sans_ligne)
    )

    nature = collections.Counter(
        (
            "sans ligne"
            if e["spotify_id"] and e["ligne"] is None
            else (
                "ligne non principale" if e["spotify_id"] and not e["ligne"] else "autre principale"
            )
        )
        for e in ecarts
    )
    print(f"{len(ecarts)} fiche(s) désalignée(s) : {dict(nature)}")
    print(
        f"Provenance lue dans les journaux : {dict(collections.Counter(provenances.values()))} "
        f"; non tracée : {len(sans_ligne) - len(provenances)}"
    )
    for artiste, n in collections.Counter(e["artiste"] for e in ecarts).most_common():
        print(f"  {artiste} : {n}")
    for e in ecarts:
        geste = (
            f"+ ligne {e['spotify_id']} (source {provenances.get(e['spotify_id'], 'NULL')})"
            if e["spotify_id"] and e["ligne"] is None
            else f"is_primary → {e['spotify_id'] or 'aucune'}"
        )
        autres = f" ; principale avant : {', '.join(e['autres_principales'])}" * bool(
            e["autres_principales"]
        )
        print(f"  #{e['track_id']} {e['artiste']} — {e['titre']} : {geste}{autres}")

    if not args.apply or not ecarts:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup("before_aligner_ids_spotify_principaux")
    if not backup:
        print("❌ Backup impossible — abandon.")
        return 1
    print(f"💾 Backup : {backup}")
    traitees = dm.aligner_ids_spotify_principaux(provenances)
    reste = dm.ids_spotify_principaux_desalignes()
    print(f"✅ {traitees} fiche(s) alignée(s) ; reste {len(reste)} écart(s).")
    return 0 if not reste else 2


if __name__ == "__main__":
    sys.exit(main())
