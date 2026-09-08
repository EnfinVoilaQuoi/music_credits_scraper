"""Retire les identifiants Spotify qui désignent le morceau de quelqu'un d'autre.

Mesuré le 2026-09-08 sur la base réelle : **80 lignes sur 1 367** portent un ID
dont AUCUN artiste attendu n'apparaît chez Spotify. Swing « Mouton noir » pointe
sur *Dessine-moi un mouton* de Mylène Farmer, « Nulle Part » sur *Gravé dans la
roche* de SNIPER, « Rendez-vous avec mon flingue » sur du Georges Brassens.

Le garde-fou de justesse empêche désormais d'en écrire de nouveaux
(`src/utils/spotify_identity.py`) ; ce script nettoie ce qui a déjà été écrit.

**Ce qu'il retire va au-delà de l'ID**, et c'est le point : ReccoBeats s'interroge
PAR le Track ID, donc **54 de ces lignes portent un BPM, une tonalité et un mode
mesurés sur l'autre morceau**, 66 une durée, 31 des streams. Effacer le seul ID
supprimerait la preuve du problème en laissant toutes ses conséquences —
`DataManager.clear_track_spotify_id` fait les trois gestes.

**Critère de retrait : l'ARTISTE ÉTRANGER, et lui seul.** Un titre écrit
autrement (« 1 pour la plume » chez Spotify, « Un pour la plume » chez Genius) ou
un écart de durée peuvent avoir dix causes légitimes ; qu'aucun des artistes
attendus n'apparaisse, non. On ne retire que ce qu'on peut montrer.

Aucune règle n'est réimplémentée : le verdict vient d'`identite_concorde`, le
même prédicat que le garde-fou et que l'audit.

Usage :
    python scripts/repair_spotify_ids.py                     # dry-run (défaut)
    python scripts/repair_spotify_ids.py --apply             # backup + écriture
    python scripts/repair_spotify_ids.py --artiste A2H
    python scripts/repair_spotify_ids.py --depuis ecarts.json  # sans re-scraper
    python scripts/repair_spotify_ids.py --exclure 1233 1252
"""

import argparse
import json
import sys
import time

# Fix encodage Windows (règle projet : reconfigure, jamais de re-wrapping)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.spotify_audit import lignes_a_verifier, track_de_la_ligne
from src.utils.spotify_identity import (
    artiste_etranger,
    identite_concorde,
    lire_identite_http,
)


def a_retirer(dm: DataManager, artiste: str | None, pause: float) -> list[dict]:
    """Les lignes dont l'ID désigne le morceau d'un autre artiste."""
    lignes = lignes_a_verifier(dm.engine, artiste, None)
    print(f"🔎 {len(lignes)} identifiant(s) à vérifier sur l'embed Spotify\n")
    fautifs = []
    for i, ligne in enumerate(lignes, start=1):
        identite = lire_identite_http(ligne["sid"])
        if identite is not None:
            track = track_de_la_ligne(ligne)
            ok, motif = identite_concorde(track, identite)
            if not ok and artiste_etranger(track, identite):
                fautifs.append(
                    {
                        "track_id": ligne["id"],
                        "artiste": ligne["artiste"],
                        "titre": ligne["title"],
                        "spotify_id": ligne["sid"],
                        "motif": motif,
                        "spotify": identite,
                    }
                )
        if i % 50 == 0:
            print(f"   … {i}/{len(lignes)}  ({len(fautifs)} à retirer)")
        time.sleep(pause)
    return fautifs


def depuis_json(chemin: str) -> list[dict]:
    """Reprend les écarts d'un audit précédent — sans redemander une page."""
    with open(chemin, encoding="utf-8") as f:
        return [e for e in json.load(f) if e.get("artiste_etranger")]


def rapport(fautifs: list[dict]) -> None:
    print(f"\n{'─' * 78}")
    print(f"{len(fautifs)} identifiant(s) à retirer\n")
    for f in fautifs:
        artistes = ", ".join(f["spotify"]["artists"])
        print(f"  #{f['track_id']:<6} {f['artiste']} — « {f['titre']} »")
        print(f"         id {f['spotify_id']} → « {f['spotify']['name']} » — {artistes}")


def appliquer(dm: DataManager, fautifs: list[dict]) -> None:
    """Retire les IDs et ce qui en découlait. Backup AVANT (règle projet)."""
    sauvegarde = get_backup_manager().create_backup("before_repair_spotify_ids")
    print(f"\n💾 Backup : {sauvegarde}")

    observations = durees = 0
    for f in fautifs:
        r = dm.clear_track_spotify_id(f["track_id"], f["spotify_id"])
        observations += len(r["observations_retirees"])
        if r["duree_suspecte"]:
            # La durée n'a pas de provenance en base (le lot B la lui donnera) :
            # elle n'est effacée que si ReccoBeats — le seul à l'écrire depuis
            # l'ID — avait bien laissé une observation sur ce morceau.
            dm.clear_track_duration(f["track_id"])
            durees += 1
    print(f"\n✅ {len(fautifs)} ID(s) retiré(s)")
    print(f"   {observations} observation(s) liée(s) à l'ID supprimée(s)")
    print(f"   {durees} durée(s) effacée(s) (venues de ReccoBeats via l'ID)")
    print("\nCes morceaux repassent en « jamais cherché » : le prochain")
    print("enrichissement leur cherchera un ID, et le garde-fou le validera.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Écrire (défaut : dry-run)")
    parser.add_argument("--artiste", help="Limiter à un artiste (nom exact en base)")
    parser.add_argument("--depuis", help="Reprendre le JSON d'un audit précédent")
    parser.add_argument("--exclure", nargs="*", type=int, default=[], help="track_id à épargner")
    parser.add_argument("--pause", type=float, default=0.2, help="Pause entre deux requêtes")
    args = parser.parse_args()

    dm = DataManager()
    fautifs = depuis_json(args.depuis) if args.depuis else a_retirer(dm, args.artiste, args.pause)
    if args.exclure:
        avant = len(fautifs)
        fautifs = [f for f in fautifs if f["track_id"] not in set(args.exclure)]
        print(f"({avant - len(fautifs)} ligne(s) épargnée(s) par --exclure)")

    rapport(fautifs)
    if not fautifs:
        print("\nRien à faire.")
        return 0

    if not args.apply:
        print("\n(dry-run — relancer avec --apply pour écrire)")
        return 0

    # Confirmation CHIFFRÉE : le déroulé des scripts de certifs.
    attendu = str(len(fautifs))
    reponse = input(f"\nTaper {attendu} pour confirmer le retrait : ").strip()
    if reponse != attendu:
        print("Annulé.")
        return 1

    appliquer(dm, fautifs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
