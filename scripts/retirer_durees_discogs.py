"""Rattrapage : les durées Discogs posées sur une autre version ou une autre page.

    python scripts/retirer_durees_discogs.py                 # à blanc (défaut)
    python scripts/retirer_durees_discogs.py --apply         # backup + écriture
    python scripts/retirer_durees_discogs.py --base copie.db # sur une autre base

Avant le juge de la piste (`discogs_identity.piste_concorde`, 2026-10-30), la
recherche Discogs prenait la première piste au même titre réduit : les cinq
remix « Cudi the Kid » de Kid Cudi portaient les 193 s de l'original, la
« Reborn » de Dirty Nice les 324 s de *KIDS SEE GHOSTS*. Ce que le juge
refuserait est reconstitué d'après la base (`discogs_durees.juger`) ; une
observation ne part que si un TÉMOIN indépendant montre l'erreur (durée de la
fiche venue d'ailleurs, ou celle de la page d'origine). Le retrait passe par
`DataManager.forget_discography_observation` (sœurs comprises) ; la colonne se
ré-arbitre sur les sources restantes, ou se vide. Un cas que le témoin ne
départage pas reste en place et figure au rapport.
"""

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import text


def lire_fiches(dm):
    """Les fiches de la base, avec leurs durées observées et leur place Discogs."""
    from src.utils.discogs_durees import Fiche

    with dm.engine.connect() as conn:
        durees: dict[int, dict] = defaultdict(dict)
        for tid, source, valeur in conn.execute(
            text("SELECT track_id, source, value FROM observations WHERE field = 'duration'")
        ):
            durees[tid][source] = valeur
        places = dict(
            conn.execute(
                text(
                    "SELECT track_id, value FROM observations "
                    "WHERE field = 'discogs_position' AND source = 'discogs'"
                )
            ).all()
        )
        lignes = conn.execute(
            text(
                "SELECT t.id, t.artist_id, a.name AS artiste, t.title, t.album, t.genius_id, "
                "t.discogs_id, t.duration, t.primary_artist_name "
                "FROM tracks t JOIN artists a ON a.id = t.artist_id"
            )
        ).mappings()
        lignes = list(lignes)
    noms: dict[int, frozenset] = {}
    for r in lignes:
        if r["artist_id"] not in noms:
            noms[r["artist_id"]] = frozenset(
                dm.noms_de_lartiste(r["artist_id"], r["artiste"])
                | dm.noms_des_formations(r["artist_id"])
            )
    fiches, colonnes = [], {}
    for r in lignes:
        colonnes[r["id"]] = r["duration"]
        fiches.append(
            Fiche(
                id=r["id"],
                artist_id=r["artist_id"],
                artiste=r["artiste"],
                title=r["title"] or "",
                album=r["album"],
                genius_id=r["genius_id"],
                discogs_id=r["discogs_id"],
                position=places.get(r["id"]),
                durations_observees=dict(durees.get(r["id"], {})),
                principal=r["primary_artist_name"] or r["artiste"],
                noms_de_l_artiste=noms[r["artist_id"]],
            )
        )
    return fiches, colonnes


def _ligne(v, colonnes) -> str:
    f = v.fiche
    col = colonnes.get(f.id)
    en_colonne = " (en colonne)" if str(col) == str(f.duree_discogs) else ""
    origine = f" ← #{v.origine.id} « {v.origine.title} »" if v.origine else ""
    return (
        f"  #{f.id} [{f.artiste}] « {f.title} » ({f.album or 'sans disque'}) — "
        f"{v.suspicion}{origine} : {v.motif}{en_colonne}"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.discogs_durees import juger
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    fiches, colonnes = lire_fiches(dm)
    avec = sum(1 for f in fiches if f.duree_discogs is not None)
    verdicts = juger(fiches)
    par_etat = {e: [v for v in verdicts if v.etat == e] for e in ("retirer", "indecis", "confirme")}
    a_retirer = par_etat["retirer"]
    print(
        f"{avec} fiche(s) à durée Discogs ; {len(verdicts)} enregistrement(s) que le juge "
        f"de la piste refuserait : {len(a_retirer)} à retirer (le témoin montre l'erreur), "
        f"{len(par_etat['indecis'])} laissé(s) faute de témoin qui départage, "
        f"{len(par_etat['confirme'])} que le témoin appuie (laissés)"
    )
    for raison, n in Counter(v.suspicion for v in a_retirer).most_common():
        print(f"  {raison} : {n}")
    for titre, cle in (
        ("À retirer", "retirer"),
        ("Laissés — le témoin ne départage pas", "indecis"),
        ("Laissés — le témoin appuie la durée", "confirme"),
    ):
        print(f"\n{titre} :")
        for v in sorted(par_etat[cle], key=lambda x: (x.fiche.artiste, x.fiche.title)):
            print(_ligne(v, colonnes))

    if not args.apply or not a_retirer:
        print("\n(à blanc — relancer avec --apply pour écrire)")
        return 0
    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup("before_retirer_durees_discogs")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"💾 Backup : {backup}")
    retirees = sum(
        dm.forget_discography_observation(
            v.fiche.id, "duration", "discogs", v.fiche.durations_observees["discogs"]
        )
        for v in a_retirer
    )
    _, apres = lire_fiches(dm)
    changees = sorted(i for i, c in colonnes.items() if apres.get(i) != c)
    print(f"✅ {retirees} observation(s) retirée(s) ; {len(changees)} colonne(s) changée(s) :")
    for i in changees:
        print(f"  #{i} : {colonnes[i]} → {apres.get(i)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
