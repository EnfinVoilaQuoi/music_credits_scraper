"""Repointe la relation `version_of` de la démo de « The Return of Chip Douglas »
de Kid Cudi vers SON original (ponctuel, 2026-11-22).

La fiche #12314 « The Return of Chip Douglas (Demo) », créée depuis Deezer
(écarts Deezer, règle C), portait `version_of` vers #3960 « The Return Of Chip
Douglas » de Nuevo (USA), où Kid Cudi n'est que producteur (`secondary_role`),
au lieu de #3638, le sien (*Speedin' Bullet 2 Heaven*). Cause corrigée dans
`ecarts_deezer.classer` : entre deux fiches au même socle, celle à rôle
secondaire ne sert d'original qu'à défaut d'autre.

`--fugitif` traite aussi le même défaut chez Shurik'n : « Fugitif
(Instrumental) » #9877 visait « Fugitif » d'IAM #2164 (Shurik'n auteur) au lieu
de #12230, le sien (*Où je vis*).

À blanc par défaut ; `--apply` = backup de la base, puis l'écrivain
(`record_relationships`, lignes sœurs comprises). `--base <copie>` lit une
copie (à blanc seulement).

    python scripts/ponctuels/chip_douglas_version.py [--fugitif] [--base <copie>] [--apply]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: `{fiche de la version: (original visé à tort, original juste)}`.
CAS = {12314: (3960, 3638)}
CAS_FUGITIF = {9877: (2164, 12230)}

TYPES = ("version_of", "remix_of")


def _fiche(conn, track_id: int):
    from sqlalchemy import text

    return (
        conn.execute(
            text(
                "SELECT id, artist_id, title, genius_url, secondary_role, relationships "
                "FROM tracks WHERE id = :t"
            ),
            {"t": track_id},
        )
        .mappings()
        .first()
    )


def relations_repointees(relations: list, faux: dict, juste: dict, artiste: str) -> list | None:
    """PUR. Les relations de la version, celle qui vise `faux` repointée vers
    `juste` ; None si aucune ne vise `faux`."""
    sortie, touche = [], False
    for r in relations:
        if isinstance(r, dict) and r.get("type") in TYPES and r.get("track_id") == faux["id"]:
            r = {
                **r,
                "title": juste["title"],
                "artist": artiste,
                "url": juste["genius_url"],
                "track_id": juste["id"],
            }
            touche = True
        sortie.append(r)
    return sortie if touche else None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--fugitif", action="store_true", help="aussi « Fugitif (Instrumental) »")
    p.add_argument("--base", type=Path, help="lire cette copie de la base (à blanc seulement)")
    p.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    a = p.parse_args(argv)
    if a.base and a.apply:
        print("❌ --base est réservé au passage à blanc")
        return 1

    import src.utils.data_manager as module_dm

    if a.base:
        module_dm.DATABASE_URL = f"sqlite:///{a.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    cas = {**CAS, **(CAS_FUGITIF if a.fugitif else {})}
    a_ecrire = []
    with dm.engine.connect() as conn:
        for version_id, (faux_id, juste_id) in cas.items():
            version, faux, juste = (_fiche(conn, i) for i in (version_id, faux_id, juste_id))
            if version is None or faux is None or juste is None:
                print(f"❌ #{version_id} : fiche absente ({version_id}, {faux_id}, {juste_id})")
                return 1
            if len({version["artist_id"], faux["artist_id"], juste["artist_id"]}) != 1:
                print(f"❌ #{version_id} : les trois fiches ne sont pas du même artiste")
                return 1
            if juste["secondary_role"]:
                print(f"❌ #{juste_id} a un rôle secondaire : ce n'est pas l'original")
                return 1
            from sqlalchemy import text

            artiste = conn.execute(
                text("SELECT name FROM artists WHERE id = :a"), {"a": version["artist_id"]}
            ).scalar()
            relations = json.loads(version["relationships"] or "[]")
            nouvelles = relations_repointees(relations, faux, juste, artiste)
            print(f"#{version_id} « {version['title']} » ({artiste})")
            if nouvelles is None:
                print(f"   ✅ ne vise plus #{faux_id} : rien à faire")
                continue
            print(
                f"   version_of : #{faux_id} « {faux['title']} » ({faux['secondary_role']})"
                f" → #{juste_id} « {juste['title']} »"
            )
            a_ecrire.append((version_id, nouvelles))
    if not a_ecrire:
        return 0
    if not a.apply:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    from src.utils.database_backup import get_backup_manager

    print(f"💾 Backup : {get_backup_manager().create_backup('before_chip_douglas_version')}")
    for version_id, nouvelles in a_ecrire:
        if not dm.record_relationships(version_id, nouvelles):
            print(f"❌ #{version_id} : écriture refusée")
            return 1
    print(f"✅ {len(a_ecrire)} relation(s) repointée(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
