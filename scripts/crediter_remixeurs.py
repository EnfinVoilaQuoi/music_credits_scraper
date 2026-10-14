"""Crédite le remixeur que NOMME le titre de chaque remix (2026-10-14).

Un remix nommé (« Dolce Camara - Snight B Remix ») dit qui l'a retravaillé ;
mesuré le 2026-10-14, MusicBrainz, quand il le sait, donne le même remixeur
10 fois sur 10. Le run discographie le pose désormais à chaque passage
(`services.versions.crediter_remixeurs`) ; ce script rattrape l'existant
sans attendre. La validation compte le remixeur d'un remix comme son
producteur (`track_validation.remixeur_credite`).

    python scripts/crediter_remixeurs.py            # à blanc (défaut)
    python scripts/crediter_remixeurs.py --apply    # backup + écriture
"""

import argparse
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.models import CreditRole
from src.services.versions import crediter_remixeurs
from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.title_matching import names_match_as_words
from src.utils.version_heritage import remixeurs_du_titre


def a_crediter(tracks) -> list[tuple]:
    out = []
    for t in tracks:
        deja = [c.name for c in t.credits if c.role == CreditRole.REMIXER]
        manquants = [
            n
            for n in remixeurs_du_titre(t.title)
            if not any(names_match_as_words(n, d) for d in deja)
        ]
        if manquants:
            out.append((t, manquants))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    args = parser.parse_args()

    dm = DataManager()
    par_artiste = []
    for nom in dm.get_artist_names():
        artiste = dm.get_artist_by_name(nom)
        tracks = dm.get_artist_tracks(artiste.id)
        liste = a_crediter(tracks)
        if liste:
            par_artiste.append((artiste, tracks, liste))
            for t, noms in liste:
                print(f"   {nom} — « {t.title} » ← {', '.join(noms)}")
    total = sum(len(liste) for _, _, liste in par_artiste)
    print(f"\n{total} remix à créditer")
    if not args.apply or not total:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    backup = get_backup_manager().create_backup("before_crediter_remixeurs")
    if not backup:
        print("❌ Backup impossible — abandon.")
        return 1
    print(f"💾 Backup : {backup}")
    n = sum(crediter_remixeurs(dm, tracks) for _, tracks, _ in par_artiste)
    print(f"✅ {n} remix crédités.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
