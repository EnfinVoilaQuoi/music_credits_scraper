"""Accepte À LA MAIN un ID Spotify que le gate ne peut pas prouver (2026-10-19).

Le gate refuse à chaque run des ID justes : Spotify ne crédite pas l'artiste
(Diam's « Température - Live 2006 », crédité à 113 et Magic System ; l'interlude
d'un invité sur l'album de l'artiste), ou les range sous le profil d'un
homonyme (SDM, dont le profil mélange plusieurs SDM — on ne confirme alors pas
le profil entier, cf. `confirmer_profil_spotify.py`). L'acceptation va dans la
table `acceptations` (e58), pendant de `refus` : le gate ne juge plus cet ID
que sur sa DURÉE, l'audit cesse de le signaler, la réparation l'épargne.

L'embed Spotify est relu (une requête) : un ID dont la durée dément la fiche
n'est pas accepté. `--poser` l'écrit aussi sur une fiche qui n'a pas d'ID.

    python scripts/accepter_id_spotify.py --fiche 8200 --id 619dGOe1Vd7iQZSUsIsdxf
    python scripts/accepter_id_spotify.py --fiche 8862 --id 20LYwBXnQpOY276biGTyP8 --poser
    python scripts/accepter_id_spotify.py ... --apply    # backup + écriture
"""

import argparse
import re
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.utils.corrections_fiches import fiches_par_id, ids_acceptes, memoriser_id_accepte
from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.spotify_identity import identite_concorde, lire_identite_http

_ID = re.compile(r"(?:spotify:track:|open\.spotify\.com/(?:intl-\w+/)?track/)?([0-9A-Za-z]{22})")


def id_du_morceau(texte: str) -> str | None:
    """L'ID de 22 caractères, depuis l'ID nu, l'URI ou le lien."""
    m = _ID.search(texte or "")
    return m.group(1) if m else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fiche", type=int, required=True, help="track_id")
    parser.add_argument("--id", required=True, help="ID, URI ou lien du morceau Spotify")
    parser.add_argument("--poser", action="store_true", help="l'écrire sur une fiche sans ID")
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    args = parser.parse_args()

    sid = id_du_morceau(args.id)
    if not sid:
        print(f"❌ ID illisible : {args.id}")
        return 1
    dm = DataManager()
    fiche = fiches_par_id([args.fiche]).get(args.fiche)
    if fiche is None:
        print(f"❌ Fiche inconnue : #{args.fiche}")
        return 1
    portes = {e.spotify_id for e in dm.get_track_spotify_ids(args.fiche)}
    print(f"#{args.fiche} {fiche.artist.name} — « {fiche.title} » [{fiche.album}]")
    if sid in ids_acceptes(fiche):
        print(f"✅ {sid} est déjà accepté pour cette fiche")
        return 0
    if sid not in portes and not args.poser:
        print(f"❌ La fiche ne porte pas {sid} : ajouter --poser pour l'écrire")
        return 1

    identite = lire_identite_http(sid)
    if identite is None:
        print("❌ Embed Spotify illisible : on n'accepte pas un ID qu'on n'a pas lu")
        return 1
    print(
        f"   Spotify sert « {identite.get('name')} » — {', '.join(identite.get('artists') or [])}"
        f", {identite.get('duration')} s"
    )
    ok, motif = identite_concorde(fiche, identite, accepte=True)
    if not ok:
        print(f"❌ Même accepté, le gate le refuserait : {motif}")
        return 1
    if not args.apply:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    print(f"💾 Backup : {get_backup_manager().create_backup('before_accepter_id_spotify')}")
    memoriser_id_accepte(fiche, sid)
    if sid not in portes:
        dm.update_track_spotify_id(args.fiche, sid, source="manual")
    print("✅ ID accepté" + (" et posé" if sid not in portes else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
