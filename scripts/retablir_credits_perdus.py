"""Rétablit les crédits effacés par une Discographie qui croyait l'artiste VIDE.

Les 15 et 16/10/2026, Diam's, Népal et OrelSan ont été relancés sous un nom
que la base ne connaissait pas (« Diams », « Nepal », « Orelsan ») : l'étape
Artiste les a « ajoutés » sans leurs morceaux, la Discographie a vu « 0
morceau en base » et `save_track` a réécrit la table `credits` de chaque fiche
depuis des objets neufs qui ne portaient que les crédits 🎫 de l'API Genius.
Les crédits de la page (et de Discogs, YouTube…) ont disparu. Correctifs du
code : `artiste.charger_ou_ajouter` et la garde « objet neuf » de `save_track`.

Source de vérité : la sauvegarde que la Discographie a prise JUSTE AVANT le
run destructeur (`backup_before_fetch_tracks_*`). Pour chaque fiche de
l'artiste, les crédits de la sauvegarde (hors `genius_api`) absents
aujourd'hui sont rendus, par la règle de coexistence (`credits_genius_api.unir`
: un 🎫 que la page confirme cède la place au crédit scrapé) et par
l'écrivain normal (`save_track` sur la fiche relue en base, qui respecte les
refus faits depuis).

    python scripts/retablir_credits_perdus.py                # passage à blanc
    python scripts/retablir_credits_perdus.py --apply        # backup + écriture
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from src.config import DATA_DIR
from src.utils.credits_genius_api import SOURCE_API
from src.utils.credits_genius_api import unir as unir_credits
from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.track_repository import TrackRepository

#: Artiste → la sauvegarde prise juste avant son run destructeur (journaux).
SAUVEGARDES = {
    "Diam’s": "backup_before_fetch_tracks_20261015_121406.db",
    "Népal": "backup_before_fetch_tracks_20261016_212932.db",
    "OrelSan": "backup_before_fetch_tracks_20261016_214434.db",
}


def credits_de_la_sauvegarde(chemin: Path, track_ids: list[int]) -> dict[int, list]:
    """`{track_id: [Credit]}` de la sauvegarde (lecture seule)."""
    par_fiche: dict[int, list] = {}
    with closing(sqlite3.connect(f"file:{chemin.as_posix()}?mode=ro", uri=True)) as c:
        c.row_factory = sqlite3.Row
        marques = ",".join("?" * len(track_ids))
        for r in c.execute(
            f"SELECT track_id, name, role, role_detail, tracks, source FROM credits "
            f"WHERE track_id IN ({marques})",
            track_ids,
        ):
            credit = TrackRepository._credit_depuis_ligne(r)
            if credit is not None:
                par_fiche.setdefault(r["track_id"], []).append(credit)
    return par_fiche


def _cle(c) -> tuple:
    return (c.name, c.role.value, c.role_detail, c.source)


def plan(dm, nom: str, sauvegarde: Path) -> list[tuple]:
    """`[(fiche, crédits rétablis, crédits proposés)]` pour un artiste."""
    artiste = dm.get_artist_by_name(nom)
    if artiste is None:
        raise SystemExit(f"Artiste absent de la base : {nom}")
    tracks = [t for t in dm.get_artist_tracks(artiste.id) if t.id is not None]
    avant = credits_de_la_sauvegarde(sauvegarde, [t.id for t in tracks])
    sortie = []
    for t in tracks:
        actuels = {_cle(c) for c in t.credits}
        perdus = [
            c for c in avant.get(t.id, []) if c.source != SOURCE_API and _cle(c) not in actuels
        ]
        if not perdus:
            continue
        gardes = [c for c in t.credits if c.source != SOURCE_API]
        api = [c for c in t.credits if c.source == SOURCE_API]
        proposes = unir_credits(gardes + perdus, api)
        sortie.append((t, perdus, proposes))
    return sortie


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="écrire (backup d'abord)")
    p.add_argument("--detail", action="store_true", help="lister chaque fiche")
    a = p.parse_args(argv)

    dm = DataManager()
    dossier = Path(DATA_DIR) / "backups"
    plans = {}
    for nom, fichier in SAUVEGARDES.items():
        chemin = dossier / fichier
        if not chemin.exists():
            print(f"❌ sauvegarde introuvable pour {nom} : {chemin}")
            return 1
        plans[nom] = plan(dm, nom, chemin)

    total_fiches = total_credits = 0
    for nom, lignes in plans.items():
        sources: dict[str, int] = {}
        for _t, perdus, _p in lignes:
            for c in perdus:
                sources[c.source] = sources.get(c.source, 0) + 1
        n = sum(len(p) for _t, p, _x in lignes)
        total_fiches += len(lignes)
        total_credits += n
        print(f"{nom} : {len(lignes)} fiche(s), {n} crédit(s) à rétablir {sources}")
        if a.detail:
            for t, perdus, proposes in lignes:
                api = sum(1 for c in proposes if c.source == SOURCE_API)
                print(f"   • {t.title} : +{len(perdus)}, {api} 🎫 restant(s)")
    print(f"\nTotal : {total_fiches} fiche(s), {total_credits} crédit(s).")
    if not a.apply:
        print("Passage à blanc — rien n'est écrit (--apply pour appliquer).")
        return 0

    backup = get_backup_manager().create_backup("retablir_credits_perdus")
    if backup is None:
        print("❌ backup impossible — rien n'est écrit")
        return 1
    print(f"💾 {backup}")
    for lignes in plans.values():
        for t, _perdus, proposes in lignes:
            t.credits = proposes
            dm.save_track(t)
    print(f"✅ {total_fiches} fiche(s) réécrite(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
