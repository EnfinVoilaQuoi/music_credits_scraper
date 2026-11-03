"""Rattrapage : les crédits YouTube en double et les noms composés de clip.

    python scripts/retirer_credits_youtube_doublons.py                 # à blanc (défaut)
    python scripts/retirer_credits_youtube_doublons.py --apply         # backup + écriture
    python scripts/retirer_credits_youtube_doublons.py --base copie.db # sur une autre base

Jusqu'au 2026-11-03, la phase YouTube de l'étape crédits ne reconnaissait un
crédit déjà porté qu'à la casse près : « Lucci » (description du clip)
s'ajoutait à « Lucci’ (FRA) » (Genius), et « Prod : Chilea's / Lucci » entrait
comme UN producteur. Le juge est celui de l'écrivain et de l'audit
`credit_doublon` : `credit_normalize.credits_youtube_en_double` (même rôle, même
`cle_de_doublon`) et `credits_description.noms_d_un_credit_de_clip`.

Un nom composé est remplacé par ses personnes (sauf celles que la fiche porte
déjà) ; un crédit YouTube en double est retiré. Seules les lignes YouTube
bougent ; les sœurs suivent (`remplacer_credits_youtube`).
"""

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text


@dataclass
class Ligne:
    name: str
    role: str
    source: str | None


def plan_de_la_fiche(lignes: list[Ligne]) -> tuple[list[tuple], list[tuple]]:
    """`(à retirer, à ajouter)` en `(nom, rôle, source)` pour UNE fiche."""
    from src.utils.credit_normalize import SOURCES_YOUTUBE, credits_youtube_en_double
    from src.utils.credits_description import noms_d_un_credit_de_clip

    retirer, virtuelles, nouvelles = [], [], []
    for x in lignes:
        noms = noms_d_un_credit_de_clip(x.name, x.source) if x.source in SOURCES_YOUTUBE else []
        if x.source in SOURCES_YOUTUBE and noms != [x.name]:
            retirer.append((x.name, x.role, x.source))
            for nom in noms:
                part = Ligne(nom, x.role, x.source)
                virtuelles.append(part)
                nouvelles.append(part)
        else:
            virtuelles.append(x)
    en_double = credits_youtube_en_double(virtuelles)
    doubles = {id(x) for x in en_double}
    retirer += [(x.name, x.role, x.source) for x in en_double if not _est_nouvelle(x, nouvelles)]
    ajouter = [(x.name, x.role, x.source) for x in nouvelles if id(x) not in doubles]
    return retirer, ajouter


def _est_nouvelle(x, nouvelles) -> bool:
    return any(x is n for n in nouvelles)


def lire(dm) -> dict[int, tuple[dict, list[Ligne]]]:
    with dm.engine.connect() as conn:
        fiches = {
            r[0]: {"id": r[0], "title": r[1], "artiste": r[2]}
            for r in conn.execute(
                text(
                    "SELECT t.id, t.title, a.name FROM tracks t "
                    "JOIN artists a ON a.id = t.artist_id"
                )
            )
        }
        par_fiche: dict[int, list[Ligne]] = {}
        for tid, nom, role, source in conn.execute(
            text("SELECT track_id, name, role, source FROM credits ORDER BY id")
        ):
            if tid in fiches:
                par_fiche.setdefault(tid, []).append(Ligne(nom, role, source))
    return {tid: (fiches[tid], lignes) for tid, lignes in par_fiche.items()}


def plans(dm) -> list[tuple[dict, list, list]]:
    out = []
    for fiche, lignes in lire(dm).values():
        retirer, ajouter = plan_de_la_fiche(lignes)
        if retirer or ajouter:
            out.append((fiche, retirer, ajouter))
    return out


def rapport(a_faire) -> None:
    print(
        f"{len(a_faire)} fiche(s) : {sum(len(r) for _, r, _ in a_faire)} crédit(s) YouTube "
        f"à retirer, {sum(len(a) for _, _, a in a_faire)} à ajouter (sœurs comptées à part)"
    )
    for fiche, retirer, ajouter in sorted(a_faire, key=lambda x: (x[0]["artiste"], x[0]["title"])):
        print(f"  [{fiche['artiste']}] #{fiche['id']} « {fiche['title']} »")
        for nom, role, source in retirer:
            print(f"      − {role} « {nom} » ({source})")
        for nom, role, source in ajouter:
            print(f"      + {role} « {nom} » ({source})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    a_faire = plans(dm)
    rapport(a_faire)
    if not args.apply or not a_faire:
        print("\n(à blanc — relancer avec --apply pour écrire)")
        return 0
    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup("before_retirer_credits_youtube_doublons")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"💾 Backup : {backup}")
    bilan = Counter()
    # Relu fiche par fiche : la réparation d'une fiche a déjà réparé ses sœurs.
    for fiche, _, _ in a_faire:
        lignes = lire_fiche(dm, fiche["id"])
        retirer, ajouter = plan_de_la_fiche(lignes)
        if not (retirer or ajouter):
            continue
        r, a = dm.remplacer_credits_youtube(fiche["id"], retirer, ajouter)
        bilan["retires"] += r
        bilan["ajoutes"] += a
    print(
        f"✅ {bilan['retires']} crédit(s) retiré(s), {bilan['ajoutes']} ajouté(s), sœurs comprises"
    )
    return 0


def lire_fiche(dm, track_id: int) -> list[Ligne]:
    with dm.engine.connect() as conn:
        return [
            Ligne(*r)
            for r in conn.execute(
                text("SELECT name, role, source FROM credits WHERE track_id = :t ORDER BY id"),
                {"t": track_id},
            )
        ]


if __name__ == "__main__":
    sys.exit(main())
