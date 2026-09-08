"""Confronte chaque `spotify_id` de la base au morceau que Spotify sert pour lui.

Pourquoi : l'identité d'un enregistrement n'était ni correcte ni vérifiée. Le
garde-fou historique contrôlait l'UNICITÉ d'un ID — « est-il déjà pris ? » — et
jamais sa JUSTESSE — « est-ce le bon morceau ? ». Mesuré le 2026-09-08 :
**52 des 152 IDs écrits par un run réel (34 %) désignaient le morceau de
quelqu'un d'autre**, tous parfaitement uniques.

Le garde-fou de justesse est posé (`src/utils/spotify_identity.py`), mais la
donnée déjà écrite ne se corrige pas toute seule : ce script la RELIT.

L'oracle est la page **`/embed/track/{id}`, server-rendered** : titre, artistes
et durée en `requests` NU — ni Playwright, ni patchright. La page `/track/{id}`
ne convient pas : c'est une application JS dont le `<title>` vaut « Spotify »
avant rendu (c'est ce qui rendait `get_spotify_page_title` muet).

**Aucune règle n'est réimplémentée ici** : le verdict vient d'`identite_concorde`,
le même prédicat que le garde-fou. Corriger le prédicat corrige ce script — même
principe que `repair_certifications.py`, qui rejoue `apply_certifications`.

Usage :
    python scripts/audit_spotify_ids.py                      # toute la base
    python scripts/audit_spotify_ids.py --artiste SCH
    python scripts/audit_spotify_ids.py --json ecarts.json   # pour la réparation
"""

import argparse
import json
import sys
import time

# Fix encodage Windows (règle projet : reconfigure, jamais de re-wrapping)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from src.utils.data_manager import DataManager
from src.utils.spotify_audit import lignes_a_verifier, track_de_la_ligne
from src.utils.spotify_identity import (
    TOLERANCE_DUREE,
    artiste_etranger,
    identite_concorde,
    lire_identite_http,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artiste", help="Limiter à un artiste (nom exact en base)")
    parser.add_argument("--limite", type=int, help="Nombre maximum d'IDs à vérifier")
    parser.add_argument(
        "--tolerance",
        type=int,
        default=TOLERANCE_DUREE,
        help=f"Écart de durée toléré, en secondes (défaut {TOLERANCE_DUREE})",
    )
    parser.add_argument("--pause", type=float, default=0.2, help="Pause entre deux requêtes")
    parser.add_argument("--json", help="Écrit les écarts dans ce fichier (pour la réparation)")
    args = parser.parse_args()

    dm = DataManager()
    lignes = lignes_a_verifier(dm.engine, args.artiste, args.limite)
    print(f"🔎 {len(lignes)} identifiant(s) à vérifier sur l'embed Spotify\n")

    ecarts: list[dict] = []
    illisibles = 0
    for i, ligne in enumerate(lignes, start=1):
        identite = lire_identite_http(ligne["sid"])
        if identite is None:
            # Une page illisible ne prouve RIEN sur l'ID — elle n'accuse personne
            # (même raisonnement qu'`absent` côté observabilité).
            illisibles += 1
        else:
            track = track_de_la_ligne(ligne)
            ok, motif = identite_concorde(track, identite, tolerance=args.tolerance)
            if not ok:
                ecarts.append(
                    {
                        "track_id": ligne["id"],
                        "artiste": ligne["artiste"],
                        "titre": ligne["title"],
                        "spotify_id": ligne["sid"],
                        "principal": bool(ligne["principal"]),
                        "motif": motif,
                        "artiste_etranger": artiste_etranger(track, identite),
                        "spotify": identite,
                    }
                )
        if i % 50 == 0:
            print(f"   … {i}/{len(lignes)}  ({len(ecarts)} écart(s))")
        time.sleep(args.pause)

    etrangers = [e for e in ecarts if e["artiste_etranger"]]
    print(f"\n{'─' * 78}")
    print(f"Vérifiés : {len(lignes) - illisibles}   ·   illisibles : {illisibles}")
    print(f"Écarts : {len(ecarts)}   ·   dont ARTISTE ÉTRANGER : {len(etrangers)}\n")
    for e in ecarts:
        marque = "🚨" if e["artiste_etranger"] else "  "
        artistes = ", ".join(e["spotify"]["artists"])
        print(f"{marque} #{e['track_id']:<6} {e['artiste']} — « {e['titre']} »")
        print(f"         id {e['spotify_id']}" + ("" if e["principal"] else "  (édition)"))
        print(f"         Spotify sert : « {e['spotify']['name']} » — {artistes}")
        print(f"         → {e['motif']}\n")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(ecarts, f, ensure_ascii=False, indent=2)
        print(f"📄 Écarts écrits dans {args.json}")

    if ecarts:
        print("🚨 = aucun artiste attendu chez Spotify : l'ID désigne un AUTRE morceau.")
        print("Les autres écarts demandent un œil — un titre écrit autrement, une")
        print("version live, un featuring noté d'un seul côté n'est pas une erreur.")
        print("Réparation : python scripts/repair_spotify_ids.py --dry-run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
