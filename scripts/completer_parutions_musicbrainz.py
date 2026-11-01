"""Comble les parutions jamais vues par MusicBrainz : lesquelles sont LIVE (e64).

Pour chaque parution à album Deezer sans verdict MusicBrainz : le code-barres
(base, sinon la fiche album Deezer), la recherche MusicBrainz par code-barres
(1 req/s), puis `mb_checked_at` / `mb_live` (`services.parutions_musicbrainz`).
Reprend là où il s'est arrêté ; s'arrête si une source ne répond plus. Le
résumé finit par les fiches studio que la famille d'audit `studio_sur_live`
signale.

    python scripts/completer_parutions_musicbrainz.py                    # à blanc (défaut)
    python scripts/completer_parutions_musicbrainz.py --apply            # backup + écriture
    python scripts/completer_parutions_musicbrainz.py --artiste "MC Solaar" --limite 20
    python scripts/completer_parutions_musicbrainz.py --base copie.db --brut brut.db

À blanc, les sources sont INTERROGÉES (le brut les garde) mais rien n'est
écrit en base. Écrivain de la base avec `--apply` : jamais pendant un lot.
"""

import argparse
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--artiste", help="les seules parutions de cet artiste")
    parser.add_argument("--limite", type=int, help="au plus N parutions")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    parser.add_argument("--brut", type=Path, help="autre brut que data/brut/brut.db")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    import src.utils.data_manager as module_dm
    from src.api.deezer_api import DeezerAPI
    from src.api.musicbrainz_api import MusicBrainzAPI
    from src.persistence import brut
    from src.services import parutions_musicbrainz as service
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    if args.brut:
        brut.CHEMIN = args.brut.resolve()
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path} · brut : {brut.CHEMIN}")

    parutions = dm.parutions_sans_musicbrainz(args.artiste)
    print(f"{len(parutions)} parution(s) à album Deezer jamais vue(s) par MusicBrainz")
    if args.limite is not None:
        parutions = parutions[: args.limite]
    if not parutions:
        return 0
    if args.apply and not _sauvegarder(dm, args, "before_completer_parutions_musicbrainz"):
        return 1

    debut = time.monotonic()
    bilan = service.completer(
        dm, parutions, deezer=DeezerAPI(), mb=MusicBrainzAPI(), ecrire=args.apply
    )
    duree = time.monotonic() - debut
    traitees = bilan.vues + bilan.sans_upc
    print(
        f"\n{bilan.vues} parution(s) vue(s) par MusicBrainz en {duree:.0f} s "
        f"({duree / max(traitees, 1):.1f} s par parution) ; "
        f"{bilan.upc_deezer} code(s)-barres lu(s) chez Deezer, {bilan.sans_upc} sans ; "
        f"{bilan.connues_mb} connue(s) de MusicBrainz, {len(bilan.live)} album(s) live"
    )
    for p in bilan.live:
        print(f"  live : {p['artiste']} — « {p['title']} » (parution {p['id']}, UPC {p['upc']})")
    if bilan.arret:
        print(f"⛔ Arrêt : source sans réponse — {bilan.arret}. Relancer pour reprendre.")
    if not args.apply:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 2 if bilan.arret else 0

    cas = service.fiches_studio_sur_live(dm, [args.artiste] if args.artiste else None)
    print(f"\nstudio_sur_live : {len(cas)} fiche(s) studio sur une parution live")
    for c in cas:
        print(f"  [{c.artiste}] {c.libelle}")
    return 2 if bilan.arret else 0


def _sauvegarder(dm, args, nom: str) -> bool:
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager

    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup(nom)
    if not backup:
        print("❌ Backup impossible — abandon.")
        return False
    print(f"💾 Backup : {backup}")
    return True


if __name__ == "__main__":
    sys.exit(main())
