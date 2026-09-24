"""Fusionne les fiches d'ÉDITION de diffusion dans leur original (étape 4, 2026-09-24).

Décision utilisateur : original, radio edit, clean, explicit, album/single
version et remaster sont UN morceau. Deux reliquats d'avant la règle :

  · des FICHES d'édition (« Impossible (Radio Edit) », « Forever (Explicit
    Version) ») : absorbées par l'original (`services.editions.absorber` — la
    ligne d'édition garde leurs identifiants, durée et page Genius) ;
  · des RENDITIONS Kworb qui sont des éditions (« Put On - Album Version
    (Edited) », 336 M hors total) : converties en éditions ; le run Kworb
    suivant additionne leurs streams au total du morceau.

Une fiche dont l'original est absent ou ambigu est LISTÉE, jamais touchée.

    python scripts/fusionner_editions.py            # dry-run
    python scripts/fusionner_editions.py --apply    # backup + écriture
"""

import argparse
import sys

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.services import editions
from src.utils.version_descriptors import Kind, parse_variant


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrire (défaut : dry-run)")
    args = parser.parse_args()

    from src.utils.data_manager import DataManager

    dm = DataManager()
    if args.apply:
        from src.utils.database_backup import get_backup_manager

        print(f"💾 Backup : {get_backup_manager().create_backup('before_fusionner_editions')}")

    compte = {"absorbees": 0, "sans_original": 0, "renditions": 0}
    for nom in dm.get_artist_names():
        artiste = dm.get_artist_by_name(nom)
        fiches = dm.get_artist_tracks(artiste.id)
        lignes = []
        for fiche in fiches:
            if parse_variant(fiche.title).edition:
                socle = editions.socle_de(fiche.title, fiches)
                if socle is None:
                    compte["sans_original"] += 1
                    lignes.append(f"   ❓ « {fiche.title} » : original absent ou ambigu — gardée")
                    continue
                if args.apply:
                    editions.absorber(dm, socle, fiche)
                compte["absorbees"] += 1
                lignes.append(
                    f"   🎚️ « {fiche.title} » ({fiche.id}) → « {socle.title} » ({socle.id})"
                )
            for entree in fiche.spotify_id_entries or []:
                if not entree.est_rendition or not entree.label:
                    continue
                v = parse_variant(entree.label)
                if not v.edition or v.kind != Kind.NONE:
                    continue
                if args.apply:
                    editions.rattacher(
                        dm, fiche, entree.label, "kworb", spotify_id=entree.spotify_id
                    )
                    dm.forget_track_spotify_id(fiche.id, entree.spotify_id)
                compte["renditions"] += 1
                lignes.append(
                    f"   ➕ rendition « {entree.label} » → édition de « {fiche.title} » "
                    f"(streams comptés au prochain run Kworb)"
                )
        if lignes:
            print(f"== {nom}")
            print("\n".join(lignes))
    print(f"\nTOTAL {compte}")
    if not args.apply:
        print("(dry-run — relancer avec --apply pour écrire)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
