"""Remet à « jamais cherché » les « pas sur Spotify » datés sur une PANNE.

Jusqu'au 2026-09-28 le provider Spotify ID datait `spotify_id_checked_at` dès
que le scraper rendait `None` — y compris quand la recherche n'avait rien pu
lire (erreurs Playwright, aucune page de résultats rendue). Le constat était
permanent : `--manquants` et l'étape Identité excluent les fiches datées.

**Oracle : le cache du scraper** (`spotify_ids_cache.json`, clé
`artiste::titre`), le seul témoin PAR MORCEAU de ce que la recherche a vu :
- un ID en cache ⇒ un candidat a été lu, puis refusé (gate d'identité,
  unicité, plancher) : verdict LÉGITIME, gardé ;
- `not_found` ⇒ sur la voie async (celle de l'app), qui n'avait pas de
  plancher, c'était « aucune page rendue » : oublié ;
- aucune entrée ⇒ une recherche en erreur ne met rien en cache (les rejets
  d'ID remettent déjà la date à NULL) : oublié.
L'usage des sources ne suffit pas : il est agrégé par (artiste, jour), et ses
96 verdicts `absent` se répartissent sur presque toutes les fiches datées.

Oublier un vrai « absent » coûte une recherche de plus ; garder un faux prive
le morceau d'identifiant pour toujours — le doute tranche vers l'oubli.

Usage :
    python scripts/repair_spotify_checked_at.py            # dry-run (défaut)
    python scripts/repair_spotify_checked_at.py --apply    # backup + écriture
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# Fix encodage Windows (règle projet : reconfigure, jamais de re-wrapping)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager


def _cle(artiste: str, titre: str) -> str:
    # Même clé que `SpotifyIDScraper._get_cache_key` (le provider cherche sous
    # `track.artist.name`).
    return f"{artiste.lower().strip()}::{titre.lower().strip()}"


def candidats(dm: DataManager, cache: dict) -> tuple[list[tuple], Counter]:
    """Les fiches datées SANS ID dont le cache ne montre aucun candidat lu."""
    with dm.engine.connect() as conn:
        lignes = conn.execute(
            text(
                "SELECT t.id, t.title, a.name, t.spotify_id_checked_at FROM tracks t "
                "JOIN artists a ON a.id = t.artist_id "
                "WHERE t.spotify_id_checked_at IS NOT NULL "
                "AND (t.spotify_id IS NULL OR t.spotify_id = '')"
            )
        ).all()
    retenus, etats = [], Counter()
    for tid, titre, artiste, date in lignes:
        valeur = cache.get(_cle(artiste, titre))
        etat = "absente" if valeur is None else "not_found" if valeur == "not_found" else "id"
        etats[etat] += 1
        if etat != "id":
            retenus.append((tid, artiste, titre, str(date)[:10], etat))
    return retenus, etats


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="écrire (backup avant)")
    p.add_argument("--cache", default="spotify_ids_cache.json")
    a = p.parse_args()

    fichier = Path(a.cache)
    if not fichier.exists():
        print(f"❌ Cache introuvable : {fichier} — sans oracle, rien n'est touché")
        return 1
    cache = json.loads(fichier.read_text(encoding="utf-8"))
    dm = DataManager()
    retenus, etats = candidats(dm, cache)
    total = sum(etats.values())
    print(f"🔎 {total} fiche(s) datée(s) sans ID Spotify :")
    print(f"   {etats['id']} candidat lu puis refusé → gardées (verdict légitime)")
    print(f"   {etats['not_found']} « not_found » → à oublier")
    print(f"   {etats['absente']} sans entrée de cache → à oublier")
    for tid, artiste, titre, date, etat in retenus[:15]:
        print(f"     #{tid} {artiste} — {titre} (daté {date}, {etat})")
    if len(retenus) > 15:
        print(f"     … et {len(retenus) - 15} autre(s)")
    if not a.apply:
        print("\n(dry-run — relancer avec --apply pour écrire)")
        return 0
    if not retenus:
        return 0
    sauvegarde = get_backup_manager().create_backup("before_repair_spotify_checked_at")
    if sauvegarde is None:
        print("❌ Backup impossible — rien n'est écrit")
        return 1
    print(f"\n💾 Backup : {sauvegarde}")
    faits = sum(1 for tid, *_ in retenus if dm.oublier_constat_spotify(tid))
    print(f"✅ {faits}/{len(retenus)} fiche(s) remise(s) à « jamais cherché »")
    return 0 if faits == len(retenus) else 1


if __name__ == "__main__":
    sys.exit(main())
