"""Confirme qu'un second profil Spotify est bien celui de l'artiste (2026-10-15).

Spotify dédouble certains artistes : « Medine » sans accent
(spotify:artist:4pJO3XfdxenH27mjTN5je4) est Médine, confirmé à la main le
2026-10-15. Sans confirmation, le gate le prend pour un homonyme
(`spotify_identity.artiste_homonyme`) et l'audit signale ses morceaux.
Le profil rejoint `artist_spotify_ids` (source `manual`), que ni MusicBrainz
ni un run ne réécrivent.

Ne confirmer QUE un profil entièrement à l'artiste : « SDM »
(7ctDfyxwP2UTOoDNaJK1y4) mélange plusieurs SDM — le confirmer ferait
accepter les morceaux des autres.

    python scripts/confirmer_profil_spotify.py --artiste "Médine" --profil 4pJO3XfdxenH27mjTN5je4
    python scripts/confirmer_profil_spotify.py ... --apply    # backup + écriture
"""

import argparse
import re
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager

_ID = re.compile(r"(?:spotify:artist:|open\.spotify\.com/artist/)?([0-9A-Za-z]{22})")


def id_du_profil(texte: str) -> str | None:
    """L'ID de 22 caractères, depuis l'ID nu, l'URI ou le lien."""
    m = _ID.search(texte or "")
    return m.group(1) if m else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artiste", required=True, help="nom exact en base")
    parser.add_argument("--profil", required=True, help="ID, URI ou lien du profil Spotify")
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    args = parser.parse_args()

    sid = id_du_profil(args.profil)
    if not sid:
        print(f"❌ Profil illisible : {args.profil}")
        return 1
    dm = DataManager()
    artiste = dm.get_artist_by_name(args.artiste)
    if artiste is None:
        print(f"❌ Artiste inconnu en base : {args.artiste}")
        return 1
    if sid == artiste.spotify_id or sid in dm.ids_spotify_connus(artiste.id):
        print(f"✅ {sid} est déjà un profil connu de {artiste.name}")
        return 0
    print(f"{artiste.name} : profil principal {artiste.spotify_id}, + {sid} (manual)")
    if not args.apply:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    print(f"💾 Backup : {get_backup_manager().create_backup('before_confirmer_profil_spotify')}")
    dm.confirmer_profil_spotify(artiste.id, sid)
    print("✅ Profil confirmé")
    return 0


if __name__ == "__main__":
    sys.exit(main())
