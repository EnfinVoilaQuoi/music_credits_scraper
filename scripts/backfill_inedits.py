"""Inédits (e34) : poser le constat sur l'existant, PUIS retirer l'astérisque.

Genius marque les morceaux pas encore sortis d'une astérisque finale dans le
titre. La migration e34 a ajouté la colonne mais **pas de backfill** : le
constat doit précéder le RENOMMAGE, qui passe par la déduplication et doit
pouvoir REFUSER — une migration ne sait rien faire de tout cela.

L'ORDRE est la seule chose qui compte ici :

  ① re-mesurer le corpus et s'arrêter si l'écart aux 168 mesurés est anormal
     (le marqueur vient d'un site tiers : il peut changer) ;
  ② poser `unreleased = 1` ;
  ③ si le titre nettoyé est DÉJÀ pris par le même artiste, **ne pas renommer,
     signaler** — « un doublon intra-artiste est SIGNALÉ, jamais synchronisé »,
     et un renommage qui échoue laisse un marqueur dans le titre avec un
     constat posé, c'est-à-dire un état que personne n'a voulu ;
  ④ sinon renommer via `rename_track` (jamais un UPDATE nu : il nettoie le
     titre comme le fait l'enregistrement) ;
  ⑤ imprimer nommément les morceaux DÉJÀ SORTIS malgré leur marqueur — une
     trace de plateforme les trahit (7 mesurés, dont B.B. Jacques
     « Amertume* », 5,5 M de streams).

Le constat n'a pas besoin d'être reposé aux runs suivants : `genius_api` le
pose à l'import et retire le marqueur au POINT D'ENTRÉE, si bien que les
prochains passages rapprochent « Voldemort* » de notre « Voldemort » au lieu
d'en créer un doublon.

Usage :
    python scripts/backfill_inedits.py            # dry-run (défaut)
    python scripts/backfill_inedits.py --apply    # backup + écriture
"""

import argparse
import sys

# Fix encodage Windows (règle projet : reconfigure, jamais de re-wrapping)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.inedits import porte_le_marqueur, titre_sans_marqueur

#: Ce que la mesure du 2026-09-22 a trouvé. Un écart FRANC signale que le
#: marqueur a changé de nature chez Genius — auquel cas il faut re-mesurer
#: avant d'écrire quoi que ce soit, pas faire confiance au script.
ATTENDU = 168
TOLERANCE = 0.4


def relever(dm) -> list[dict]:
    """Les morceaux portant le marqueur, avec ce qu'il faut pour décider."""
    with dm.engine.connect() as conn:
        lignes = (
            conn.execute(
                text(
                    "SELECT t.id, t.title, t.artist_id, a.name AS artiste, t.unreleased, "
                    "t.spotify_id, t.spotify_streams "
                    "FROM tracks t JOIN artists a ON a.id = t.artist_id"
                )
            )
            .mappings()
            .all()
        )
    return [dict(ligne) for ligne in lignes if porte_le_marqueur(ligne["title"])]


def classer(dm, marques: list[dict]) -> tuple[list, list, list]:
    """(à traiter, collisions, déjà sortis) — sans rien écrire."""
    a_traiter, collisions, deja_sortis = [], [], []
    for ligne in marques:
        propre = titre_sans_marqueur(ligne["title"])
        pris_par = dm.titre_deja_pris(ligne["artist_id"], propre, sauf_id=ligne["id"])
        if ligne["spotify_id"] or ligne["spotify_streams"]:
            deja_sortis.append(ligne)
        if pris_par is not None:
            collisions.append((ligne, propre, pris_par))
        else:
            a_traiter.append((ligne, propre))
    return a_traiter, collisions, deja_sortis


def rapport(marques, a_traiter, collisions, deja_sortis) -> bool:
    """Rend False si le corpus s'écarte trop de la mesure d'origine."""
    print(f"\n{len(marques)} morceau(x) portent le marqueur d'inédit (mesuré : {ATTENDU}).")
    ecart = abs(len(marques) - ATTENDU) / ATTENDU
    if ecart > TOLERANCE:
        print(
            f"\n❌ Écart de {100 * ecart:.0f} % à la mesure d'origine : le marqueur a"
            "\n   probablement changé de nature chez Genius. Re-mesurer AVANT d'écrire."
        )
        return False

    print(f"\n   {len(a_traiter):4}  à constater puis renommer")
    print(f"   {len(collisions):4}  collision(s) — constat posé, titre INCHANGÉ")
    print(f"   {len(deja_sortis):4}  déjà sorti(s) malgré le marqueur (trace de plateforme)")

    if collisions:
        print("\n⚠️  Le titre nettoyé est déjà pris chez le même artiste. Ce sont des")
        print("    doublons à fusionner à la main — les renommer violerait l'unicité,")
        print("    et les aligner en silence les rendrait invisibles.\n")
        for ligne, propre, pris_par in collisions:
            print(
                f"   #{ligne['id']} {ligne['artiste']} — {ligne['title']!r} → #{pris_par} {propre!r}"
            )

    if deja_sortis:
        print("\nℹ️  Sortis malgré leur astérisque : le constat est posé quand même, et")
        print("    `save_track` le LÈVERA (`constat_a_ecrire`) à la première sauvegarde.\n")
        for ligne in deja_sortis:
            print(f"   #{ligne['id']} {ligne['artiste']} — {ligne['title']!r}")
    return True


def appliquer(dm, a_traiter, collisions) -> tuple[int, int]:
    """Constat d'abord, renommage ensuite. Rend (constats, renommages)."""
    constats = renommages = 0
    for ligne, _propre in a_traiter:
        if dm.record_unreleased(ligne["id"], True):
            constats += 1
    for ligne, _propre, _pris in collisions:
        # Le constat vaut AUSSI pour une ligne qu'on ne renomme pas : c'est bien
        # un inédit, seul son titre reste en l'état.
        if dm.record_unreleased(ligne["id"], True):
            constats += 1
    for ligne, propre in a_traiter:
        if dm.rename_track(ligne["id"], propre):
            renommages += 1
        else:
            print(f"   ⚠️ renommage refusé pour #{ligne['id']} {ligne['title']!r}")
    return constats, renommages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit réellement (backup avant)")
    args = parser.parse_args()

    dm = DataManager()
    marques = relever(dm)
    a_traiter, collisions, deja_sortis = classer(dm, marques)
    if not rapport(marques, a_traiter, collisions, deja_sortis):
        return 1

    if not args.apply:
        print("\nℹ️  DRY-RUN : rien n'a été écrit. Relance avec --apply.")
        return 0
    if not marques:
        print("\nRien à faire.")
        return 0

    backup = get_backup_manager().create_backup("before_backfill_inedits")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"\n💾 Backup : {backup}")
    constats, renommages = appliquer(dm, a_traiter, collisions)
    print(f"✅ {constats} constat(s) posé(s), {renommages} titre(s) nettoyé(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
