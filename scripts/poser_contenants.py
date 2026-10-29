"""Pose la relation `contient` d'un medley vers ses fiches contenues (2026-10-29).

Un medley (« Vers le bas + Freestyle » de Flynt, les triples de Médine) est UNE
fiche qui en contient d'autres : ses vues et ses streams ne s'ajoutent jamais à
ceux des contenus, et ses crédits et paroles ne sont plus réclamés à une page
Genius qu'il n'a pas (`utils.contenants`).

À la main : la décision est consignée en table `corrections` (action
`contient`, exportée avec les autres décisions), puis la relation est posée. Une
fiche contenue encore absente n'est jamais créée : désignée dans le fichier de
corrections, elle sera liée par `appliquer_corrections_fiches.py` quand elle
existera.

    python scripts/poser_contenants.py --artiste Flynt --contenant 9716 --contenus 1250 1251
    python scripts/poser_contenants.py --regle [--artiste Flynt ...]   # règle des titres joints
    python scripts/poser_contenants.py ... --apply                      # backup + écriture

`--regle` : les titres « A + B » / « A / B » dont chaque partie a UNE fiche chez
l'artiste (`contenants.proposer_contenus`) — sans `--apply`, la simulation.
"""

import argparse
import sys

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.services import liens_contenus
from src.utils import contenants
from src.utils import corrections_fiches as cf
from src.utils.track_soeurs import ORIGINE_MANUELLE


def _decrire(t) -> str:
    return f"#{t.id} « {t.title} » [{t.album or '—'}]"


def a_la_main(dm, artiste: str, contenant_id: int, contenus_ids: list[int], apply: bool) -> int:
    artist = dm.get_artist_by_name(artiste)
    if artist is None:
        print(f"❌ Artiste inconnu : {artiste}")
        return 1
    fiches = {t.id: t for t in dm.get_artist_tracks(artist.id)}
    contenant = fiches.get(contenant_id)
    if contenant is None:
        print(f"❌ #{contenant_id} n'est pas une fiche de {artiste}")
        return 1
    inconnus = [i for i in contenus_ids if i not in fiches]
    if inconnus:
        print(f"❌ Pas des fiches de {artiste} : {inconnus}")
        return 1
    contenus = [fiches[i] for i in contenus_ids]
    print(f"{artiste} — {_decrire(contenant)} contient :")
    for c in contenus:
        print(f"   · {_decrire(c)}")
    if not apply:
        ajoutes = liens_contenus.lier(
            dm, contenant, contenus, artiste, ORIGINE_MANUELLE, apply=False
        )
        print(f"   {len(ajoutes)} lien(s) à poser")
        print("\n(à blanc — relancer avec --apply pour écrire)")
        return 0
    from src.utils.database_backup import get_backup_manager

    print(f"💾 Backup : {get_backup_manager().create_backup('before_poser_contenants')}")
    cf.memoriser_contenus(
        artiste, cf.designation_de(contenant), [cf.designation_de(c) for c in contenus]
    )
    ajoutes = liens_contenus.lier(dm, contenant, contenus, artiste, ORIGINE_MANUELLE, apply=True)
    print(f"✅ {len(ajoutes)} lien(s) posé(s), décision consignée")
    return 0


def par_la_regle(dm, artistes: list[str], apply: bool) -> int:
    noms = artistes or dm.get_artist_names()
    lots = []
    for nom in noms:
        artist = dm.get_artist_by_name(nom)
        if artist is None:
            print(f"⚠️ Artiste inconnu : {nom}")
            continue
        lots += [
            (nom, t, c) for t, c in liens_contenus.propositions(dm.get_artist_tracks(artist.id))
        ]
    for nom, t, contenus in lots:
        print(f"{nom} — {_decrire(t)}")
        for c in contenus:
            print(f"   contient {_decrire(c)}")
    print(f"\n{len(lots)} medley(s), {sum(len(c) for _, _, c in lots)} lien(s)")
    if not apply:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    if lots:
        from src.utils.database_backup import get_backup_manager

        print(f"💾 Backup : {get_backup_manager().create_backup('before_poser_contenants')}")
    for nom, t, contenus in lots:
        liens_contenus.lier(dm, t, contenus, nom, contenants.ORIGINE_REGLE, apply=True)
    print("✅ liens posés")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artiste", action="append", default=[], help="nom (répétable)")
    parser.add_argument("--contenant", type=int, help="track_id du medley")
    parser.add_argument("--contenus", type=int, nargs="+", help="track_id des fiches contenues")
    parser.add_argument("--regle", action="store_true", help="règle des titres joints")
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    args = parser.parse_args()

    from src.utils.data_manager import DataManager

    dm = DataManager()
    if args.regle:
        return par_la_regle(dm, args.artiste, args.apply)
    if not (args.contenant and args.contenus and len(args.artiste) == 1):
        parser.error("à la main : --artiste NOM --contenant ID --contenus ID [ID …]")
    return a_la_main(dm, args.artiste[0], args.contenant, args.contenus, args.apply)


if __name__ == "__main__":
    sys.exit(main())
