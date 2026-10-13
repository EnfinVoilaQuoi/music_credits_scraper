"""B — les invariants après une écriture REJOUÉE (chantier B, 2026-10-13).

Sur une COPIE de la base (sauvegarde SQLite en ligne : la vraie base n'est que
LUE, même pendant un run), les écarts Deezer cochés d'office des artistes sont
CRÉÉS depuis le brut gardé (aucun réseau), puis l'audit de persistance compare
les invariants avant / après. Un invariant qui apparaît = un écrivain fautif.

    python scripts/invariants_apres_rejeu.py Flynt Swing Josman
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))


def copier_la_base(copie: Path) -> None:
    source = sqlite3.connect(f"file:{RACINE / 'data' / 'music_credits.db'}?mode=ro", uri=True)
    cible = sqlite3.connect(copie)
    try:
        source.backup(cible)
    finally:
        source.close()
        cible.close()


def main(artistes: list[str]) -> int:
    copie = Path(tempfile.mkdtemp(prefix="invariants_")) / "copie.db"
    copier_la_base(copie)

    import src.utils.data_manager as module_dm

    module_dm.DATABASE_URL = f"sqlite:///{copie}"

    from src.concurrency import async_loop
    from src.persistence import brut
    from src.services import audit, ecarts_deezer
    from src.services.runtime import Runtime, fermer

    runtime = Runtime.build()
    dm = runtime.data_manager
    if Path(dm.db_path).resolve() != copie.resolve():
        print(f"❌ la base ouverte n'est pas la copie : {dm.db_path}")
        fermer(runtime)
        return 1

    def invariants(nom):
        a = audit.auditer(dm, [nom], persistance=True, revue=False)
        return {f: n for f, n in a.compte().items() if f in audit.INVARIANTS and n}, a

    fautif = False
    try:
        client, http = runtime.data_enricher.deezer_client, runtime.data_enricher.http
        for nom in artistes:
            art = dm.get_artist_by_name(nom)
            if art is None or not art.deezer_id:
                print(f"❌ {nom} : inconnu, ou sans ID Deezer")
                fautif = True
                continue
            art.tracks = dm.get_artist_tracks(art.id)
            avant, _ = invariants(nom)
            with brut.rejeu() as compte:
                bilan = async_loop.run_sync(
                    ecarts_deezer.detecter_async(
                        client, http, dm, art, deezer_id=int(art.deezer_id)
                    )
                )
                coches = [e for e in bilan.ecarts if e.coche]
                comptes = ecarts_deezer.creer_lignes(
                    dm,
                    art,
                    coches,
                    lire_piste=lambda tid: async_loop.run_sync(client.get_track_async(http, tid)),
                )
            apres, a = invariants(nom)
            print(
                f"== {nom} : {len(coches)} écart(s) coché(s), brut {compte['servies']} servie(s), "
                f"{len(compte['absentes'])} absente(s)"
            )
            for ligne in comptes[:15]:
                print("   ", ligne)
            print("   invariants avant :", avant or "aucun")
            print("   invariants après :", apres or "aucun")
            for cas in a.cas:
                if cas.famille in audit.INVARIANTS and cas.famille not in avant:
                    fautif = True
                    print("     ", cas.famille, cas.libelle)
    finally:
        fermer(runtime)
        copie.unlink(missing_ok=True)
    return 1 if fautif else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["Flynt"]))
