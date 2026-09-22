"""Dates de sortie : rendre leur PROVENANCE aux colonnes qui n'en ont pas.

Depuis le lot B (e24), `release_date` est ARBITRÉ : la colonne n'appartient plus
à personne, elle est le verdict des observations. Le backfill de la migration a
déclaré `legacy` les colonnes d'alors — mais la base grossit après une
migration, et **6 201 morceaux datés ont été créés depuis**, par un import
Genius qui ne se déclarait pas encore (corrigé le 2026-09-22). Mesuré :
**5 829 colonnes `release_date` qu'aucune observation n'explique.**

Ce n'est pas cosmétique. Les champs de discographie sont arbitrés à l'écriture :
une source déclare, la colonne prend son verdict, puis un retrait
(`clear_track_deezer_id`) ré-arbitre sur ce qui reste — et s'il ne reste RIEN,
la colonne se vide. La date d'origine, jamais déclarée, disparaîtrait sans que
personne l'ait décidé.

Le script ne réimplémente RIEN : il rejoue le résolveur
(`reconcile.resoudre_date_de_sortie`, la même fonction que `reconcile()` et que
`_arbitrer_discographie`) pour AUDITER, et délègue l'écriture au repository
(`declarer_provenance_legacy`, qui porte la règle de e24).

⚠️ `legacy` et non la source réelle : on ne SAIT pas qui a écrit ces colonnes.
Inventer une source serait pire que d'avouer qu'on l'ignore — et le moteur
écarte `legacy` dès qu'une source réelle existe, ce qui est exactement voulu.

⚠️ Les dates déjà au 1ᵉʳ janvier ne sont PAS retronquées en « année seule » :
rien ne distingue un `datetime(2018, 1, 1)` fabriqué par Genius d'un vrai 1ᵉʳ
janvier. La précision commence avec les runs qui suivent.

Usage :
    python scripts/repair_release_dates.py            # dry-run (défaut)
    python scripts/repair_release_dates.py --apply    # backup + écriture
"""

import argparse
import collections
import sys

# Fix encodage Windows (règle projet : reconfigure, jamais de re-wrapping)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.enrichment.observation import Observation
from src.enrichment.reconcile import DISCOGRAPHY_PRIORITIES, resoudre_date_de_sortie
from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.dates import completer, meme_jour, precision

CHAMP = "release_date"


def auditer(dm) -> tuple[list, dict]:
    """(colonnes sans provenance, compteurs) — sans rien écrire.

    L'audit rejoue le résolveur sur les morceaux QUI ONT des observations : si
    le verdict diffère de la colonne, c'est qu'une voie d'écriture contourne
    l'arbitrage, et il faut le SAVOIR avant de réparer quoi que ce soit.
    """
    compteurs = collections.Counter()
    observations = collections.defaultdict(list)
    with dm.engine.connect() as conn:
        from sqlalchemy import text

        for tid, val, src, conf in conn.execute(
            text("SELECT track_id, value, source, confidence FROM observations WHERE field = :f"),
            {"f": CHAMP},
        ):
            observations[tid].append(Observation(CHAMP, val, src, conf))
        colonnes = {
            int(r[0]): r[1]
            for r in conn.execute(text("SELECT id, release_date FROM tracks"))
            if r[1] is not None and str(r[1]) != ""
        }

    desaccords = []
    for track_id, colonne in colonnes.items():
        obs = observations.get(track_id)
        if not obs:
            continue
        verdict = resoudre_date_de_sortie(obs, DISCOGRAPHY_PRIORITIES[CHAMP])
        attendu = completer(verdict.value) if verdict else None
        if attendu and not meme_jour(colonne, attendu):
            compteurs["colonne ≠ verdict"] += 1
            desaccords.append((track_id, str(colonne)[:10], attendu))
        else:
            compteurs["d'accord avec ses observations"] += 1

    sans_provenance = dm.colonnes_sans_provenance(CHAMP)
    compteurs["sans aucune observation"] = len(sans_provenance)
    compteurs["premier janvier (non retronqué)"] = sum(
        1 for _, v in sans_provenance if str(v)[5:10] == "01-01"
    )
    compteurs["forme illisible (ignorée)"] = sum(
        1 for _, v in sans_provenance if precision(v) is None
    )
    return sans_provenance, dict(compteurs), desaccords


def rapport(sans_provenance, compteurs, desaccords) -> None:
    print("\nDates de sortie — état des lieux :\n")
    for libelle, n in sorted(compteurs.items(), key=lambda kv: -kv[1]):
        print(f"   {n:6}  {libelle}")
    if desaccords:
        print(f"\n⚠️  {len(desaccords)} colonne(s) contredisent leurs propres observations —")
        print("    une voie d'écriture contourne l'arbitrage. À corriger DANS le code,")
        print("    ce script ne les touche pas.\n")
        for track_id, colonne, attendu in desaccords[:20]:
            print(f"   #{track_id} colonne {colonne} · verdict {attendu}")
    print(f"\n{len(sans_provenance)} colonne(s) recevront une observation `legacy`.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit réellement (backup avant)")
    args = parser.parse_args()

    dm = DataManager()
    sans_provenance, compteurs, desaccords = auditer(dm)
    rapport(sans_provenance, compteurs, desaccords)

    if not args.apply:
        print("\nℹ️  DRY-RUN : rien n'a été écrit. Relance avec --apply.")
        return 0
    if not sans_provenance:
        print("\nRien à faire.")
        return 0

    backup = get_backup_manager().create_backup("before_repair_release_dates")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"\n💾 Backup : {backup}")
    n = dm.declarer_provenance_legacy(CHAMP, sans_provenance)
    print(f"✅ {n} colonne(s) déclarée(s) `legacy`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
