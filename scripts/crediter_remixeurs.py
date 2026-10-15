"""Qualifie les remixeurs nommés par les titres, sans les inventer.

Un titre (« Dolce Camara - Snight B Remix ») est une PROPOSITION. Il devient
un crédit « Remixer » seulement si Genius, Discogs ou MusicBrainz crédite déjà
la même personne à la production sur cette version. Le rapport sépare les
cas corroborés, les suggestions à vérifier, et les anciennes déductions par
titre seul.

    python scripts/crediter_remixeurs.py            # à blanc (défaut)
    python scripts/crediter_remixeurs.py --apply    # backup + écriture
"""

import argparse
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.services.versions import candidats_remixeurs, crediter_remixeurs
from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    args = parser.parse_args()

    dm = DataManager()
    par_artiste = []
    totaux = {"deja_credite": 0, "corrobore": 0, "propose": 0, "titre_seul_historique": 0}
    for nom in dm.get_artist_names():
        artiste = dm.get_artist_by_name(nom)
        tracks = dm.get_artist_tracks(artiste.id)
        liste = [(t, candidats_remixeurs(t)) for t in tracks]
        liste = [(t, candidats) for t, candidats in liste if candidats]
        if liste:
            par_artiste.append((artiste, tracks, liste))
            for t, candidats in liste:
                for candidat in candidats:
                    totaux[candidat.statut] += 1
                    detail = f" ({candidat.source})" if candidat.source else ""
                    marque = {
                        "deja_credite": "✓ déjà crédité",
                        "corrobore": "✓ corroboré, sera qualifié",
                        "propose": "? à vérifier",
                        "titre_seul_historique": "! ancien crédit titre seul",
                    }[candidat.statut]
                    print(f"   {marque}{detail} — {nom} — « {t.title} » ← {candidat.nom}")
    total = totaux["corrobore"]
    print(
        "\nBilan : "
        f"{totaux['corrobore']} corroboré(s) · {totaux['propose']} à vérifier · "
        f"{totaux['deja_credite']} déjà crédité(s) · "
        f"{totaux['titre_seul_historique']} ancien(s) crédit(s) titre seul"
    )
    if not args.apply or not total:
        print("(à blanc — --apply n'écrira que les cas corroborés)")
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
