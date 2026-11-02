"""Rattrapage : le disque, la place et les crédits Discogs d'une autre piste.

    python scripts/retirer_pistes_discogs.py                 # à blanc (défaut)
    python scripts/retirer_pistes_discogs.py --apply         # backup + écriture
    python scripts/retirer_pistes_discogs.py --base copie.db # sur une autre base
    python scripts/retirer_pistes_discogs.py --reference sauvegarde.db

Suite de `retirer_durees_discogs.py` (2026-10-30), qui n'a retiré que la
DURÉE des pistes Discogs prises sur une autre version ou un homonyme : le
disque (`tracks.discogs_id`), la place (`discogs_position`, lien de parution)
et les crédits de la même piste sont restés. Le juge est le même
(`discogs_durees.juger`), sur la durée Discogs que la sauvegarde d'avant le
premier rattrapage garde (`--reference`, défaut : la plus récente
`backup_before_retirer_durees_discogs_*.db` de `data/backups/`).

Ne part que ce qu'un TÉMOIN PROPRE de la fiche montre faux (sa durée lue
ailleurs, à plus de 10 s de la piste) : `discogs_durees.pistes_a_retirer`. Les
cas que seule la page d'origine désigne restent à l'humain, listés à part.
Retrait par `clear_track_discogs_release` (sœurs comprises), disque REFUSÉ
(`memoriser_discogs_refuse`, origine `regle:discogs_autre_piste`) pour que la
recherche suivante ne le reprenne pas.
"""

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import text

ORIGINE = "regle:discogs_autre_piste"
EXEMPLES_PAR_ARTISTE = 3


def reference_par_defaut(dossier: Path) -> Path | None:
    """La sauvegarde la plus récente prise avant le retrait des durées."""
    candidates = sorted(dossier.glob("backup_before_retirer_durees_discogs_*.db"))
    return candidates[-1] if candidates else None


def ce_qui_partirait(dm, track_id: int) -> dict:
    """Ce que la piste a laissé sur la fiche : crédits, observations, places."""
    with dm.engine.connect() as conn:
        return {
            "credits": conn.execute(
                text("SELECT COUNT(*) FROM credits WHERE track_id = :t AND source = 'discogs'"),
                {"t": track_id},
            ).scalar(),
            "observations": [
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT field FROM observations WHERE track_id = :t AND source = 'discogs'"
                    ),
                    {"t": track_id},
                )
            ],
            "places": conn.execute(
                text(
                    "SELECT COUNT(*) FROM release_tracks rt WHERE rt.track_id = :t AND ("
                    "rt.position_source = 'discogs' OR EXISTS (SELECT 1 FROM "
                    "release_track_sources s WHERE s.release_track_id = rt.id "
                    "AND s.source = 'discogs'))"
                ),
                {"t": track_id},
            ).scalar(),
        }


def _ligne(v, traces: dict | None = None) -> str:
    f = v.fiche
    origine = f" ← #{v.origine.id} « {v.origine.title} »" if v.origine else ""
    detail = ""
    if traces is not None:
        detail = (
            f" [disque {f.discogs_id}, place {f.position or '—'}, "
            f"{traces['credits']} crédit(s)]"
        )
    return (
        f"  #{f.id} « {f.title} » ({f.album or 'sans disque'}) — "
        f"{v.suspicion}{origine} : {v.motif}{detail}"
    )


def rapport(dm, a_retirer, humain) -> dict[int, dict]:
    """Imprime le passage à blanc ; rend les traces de chaque fiche à retirer."""
    traces = {v.fiche.id: ce_qui_partirait(dm, v.fiche.id) for v in a_retirer}
    print(
        f"{len(a_retirer)} piste(s) à retirer (témoin propre de la fiche) : "
        f"{sum(t['credits'] for t in traces.values())} crédit(s), "
        f"{sum(1 for t in traces.values() if 'discogs_position' in t['observations'])} "
        f"place(s) Discogs, {sum(t['places'] for t in traces.values())} lien(s) de parution"
    )
    par_artiste = defaultdict(list)
    for v in a_retirer:
        par_artiste[v.fiche.artiste].append(v)
    for artiste, vs in sorted(par_artiste.items(), key=lambda x: (-len(x[1]), x[0])):
        print(f"\n{artiste} : {len(vs)} fiche(s)")
        for v in sorted(vs, key=lambda x: x.fiche.title)[:EXEMPLES_PAR_ARTISTE]:
            print(_ligne(v, traces[v.fiche.id]))
        if len(vs) > EXEMPLES_PAR_ARTISTE:
            print(f"  … et {len(vs) - EXEMPLES_PAR_ARTISTE} autre(s)")
    print(
        f"\nÀ l'humain — durée retirée sur la foi de la page d'origine, sans témoin "
        f"propre ({len(humain)}) : rien n'y est touché"
    )
    for v in sorted(humain, key=lambda x: (x.fiche.artiste, x.fiche.title)):
        print(f"  [{v.fiche.artiste}]" + _ligne(v)[1:])
    return traces


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    parser.add_argument(
        "--reference",
        type=Path,
        help="sauvegarde d'avant le retrait des durées (défaut : la plus récente)",
    )
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.services.discogs_pistes import lire_fiches
    from src.utils.corrections_fiches import fiches_par_id, memoriser_discogs_refuse
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.discogs_durees import juger, pistes_a_retirer
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")
    reference = args.reference or reference_par_defaut(
        Path(module_dm.DATABASE_URL.replace("sqlite:///", "")).parent / "backups"
    )
    if reference is None or not reference.exists():
        print("❌ Aucune sauvegarde de référence (--reference) : sans elle, rien à juger.")
        return 1
    print(f"Référence : {reference}")

    fiches, _ = lire_fiches(dm, reference)
    a_retirer, humain = pistes_a_retirer(juger(fiches))
    rapport(dm, a_retirer, humain)

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
    backup = sauvegarde.create_backup("before_retirer_pistes_discogs")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"💾 Backup : {backup}")
    objets = fiches_par_id(v.fiche.id for v in a_retirer)
    bilan = Counter()
    for v in a_retirer:
        r = dm.clear_track_discogs_release(v.fiche.id, v.fiche.discogs_id)
        if r["id_retire"] is None:
            continue
        # Refusé, sinon la recherche Discogs suivante reprend la même piste.
        memoriser_discogs_refuse(objets[v.fiche.id], v.fiche.discogs_id, origine=ORIGINE)
        bilan["disques"] += 1
        bilan["observations"] += len(r["observations_retirees"])
        bilan["credits"] += r["credits_retires"]
        bilan["places"] += r["liens_parution_retires"] + r["places_retirees"]
        if r["parution_reperee"]:
            print(f"   ⚠️ #{v.fiche.id} : parution repère « {r['parution_reperee']} » laissée")
    print(
        f"✅ {bilan['disques']} disque(s) retiré(s) et refusé(s) ; "
        f"{bilan['observations']} observation(s), {bilan['credits']} crédit(s), "
        f"{bilan['places']} place(s) de parution, sœurs comprises"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
