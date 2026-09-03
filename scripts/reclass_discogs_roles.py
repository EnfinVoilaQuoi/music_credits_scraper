"""Reclasse les crédits Discogs rangés en `Other` grâce à leur libellé d'origine.

Quand `_map_discogs_role_to_enum` ne connaît pas un rôle Discogs, le crédit est
stocké avec `role='Other'` et le LIBELLÉ brut dans `role_detail` (cf.
`DiscogsClient.enrich_track_data`). Compléter la table d'alias rend donc ces
crédits reclassables **sans réseau** : tout est déjà en base.

Ce script ne duplique AUCUNE correspondance — il rejoue
`DiscogsClient._map_discogs_role_to_enum`, qui reste la seule source de vérité.
Ajouter un alias là-bas suffit pour que ce script le prenne en compte.

Il ne touche QUE la colonne `role` : `role_detail` garde le libellé Discogs.
C'est voulu — la provenance reste lisible dans la fiche morceau, et l'unicité
`(track_id, name, role, role_detail)` empêche toute collision avec une ligne
Genius (dont `role_detail` est NULL la plupart du temps). Contrepartie assumée :
une personne créditée par les deux sources au même rôle apparaît deux fois, avec
un émoji de source distinct — c'est déjà le comportement actuel.

Usage :
    python scripts/reclass_discogs_roles.py            # dry-run (défaut)
    python scripts/reclass_discogs_roles.py --apply    # backup + écriture
    python scripts/reclass_discogs_roles.py --revert   # retour en Other
"""

import argparse
import collections
import re
import sqlite3
import sys

# Fix encodage Windows (règle projet : reconfigure, jamais de re-wrapping)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.api.discogs_api import DiscogsClient
from src.models.track import CreditRole
from src.utils.credit_normalize import identity_key
from src.utils.database_backup import get_backup_manager

DB_PATH = "data/music_credits.db"

# `role_detail` porte soit le LIBELLÉ du rôle, soit la liste des PISTES fournie
# par Discogs (« 16 », « 3, 5, 7, 14 ») quand elle existe — les deux se disputent
# la même colonne (cf. WIP). Une valeur purement numérique n'est donc pas un
# libellé de rôle et ne doit pas être reclassée.
_PISTES_RE = re.compile(r"^[\d\s,&at to\.\-]+$")


def _mapper():
    """Le mapper réel, sans passer par `__init__` (qui monte un client réseau)."""
    return DiscogsClient.__new__(DiscogsClient)


def _est_un_libelle(role_detail: str | None) -> bool:
    return bool(role_detail) and not _PISTES_RE.match(role_detail)


def analyser(conn) -> tuple[list, list]:
    """(reclassements, collisions) — sans rien écrire.

    Un « reclassement » = (id, nom, libellé, rôle cible).
    Une « collision » = une personne déjà créditée à ce rôle sur ce morceau
    (par Genius en général) : le reclassement ajoutera une ligne visible en plus.
    """
    client = _mapper()
    cur = conn.cursor()

    # Index des personnes déjà créditées, par (morceau, rôle), en clé d'identité
    # normalisée — deux graphies du même nom comptent pour une seule personne.
    deja = collections.defaultdict(set)
    for tid, name, role in cur.execute("SELECT track_id, name, role FROM credits"):
        deja[(tid, role)].add(identity_key(name))

    reclassements, collisions = [], []
    for cid, tid, name, role_detail in cur.execute(
        "SELECT id, track_id, name, role_detail FROM credits "
        "WHERE source = 'discogs' AND role = 'Other'"
    ):
        if not _est_un_libelle(role_detail):
            continue
        cible = client._map_discogs_role_to_enum(role_detail)
        if cible == CreditRole.OTHER:
            continue  # libellé volontairement non mappé, ou inconnu
        reclassements.append((cid, name, role_detail, cible.value))
        if identity_key(name) in deja[(tid, cible.value)]:
            collisions.append((name, role_detail, cible.value))
    return reclassements, collisions


def rapport(reclassements, collisions, conn):
    par_libelle = collections.Counter((libelle, cible) for _, _, libelle, cible in reclassements)
    print(f"\n{len(reclassements)} crédit(s) à reclasser :\n")
    for (libelle, cible), n in par_libelle.most_common():
        print(f"   {n:4}  {libelle!r:32} → {cible}")

    restants = collections.Counter(
        rd
        for (rd,) in conn.execute(
            "SELECT role_detail FROM credits WHERE source='discogs' AND role='Other'"
        )
        if _est_un_libelle(rd) and _mapper()._map_discogs_role_to_enum(rd) == CreditRole.OTHER
    )
    if restants:
        print(f"\nLaissés en Other ({sum(restants.values())} crédits) :\n")
        for libelle, n in restants.most_common():
            print(f"   {n:4}  {libelle!r}")

    if collisions:
        print(f"\n⚠️  {len(collisions)} doublon(s) VISIBLE(S) créé(s) — la personne est déjà")
        print("    créditée à ce rôle sur ce morceau (l'autre source la nomme aussi).")
        print("    Assumé : les deux lignes se distinguent par leur émoji de source.\n")
        for name, libelle, cible in collisions:
            print(f"   {name!r} déjà crédité {cible} (arrive via {libelle!r})")

    # `songwriter` → WRITER alimente get_writers(), qui conditionne le badge
    # « ✅ Crédits complets » : le signaler pour que le changement ne surprenne pas.
    n_writer = sum(1 for _, _, _, cible in reclassements if cible == CreditRole.WRITER.value)
    if n_writer:
        print(
            f"\nℹ️  {n_writer} crédit(s) deviennent des auteurs : quelques morceaux peuvent"
            "\n    passer de « ⚠️ Crédits partiels » à « ✅ Crédits complets »."
        )


def appliquer(conn, reclassements) -> int:
    cur = conn.cursor()
    for cid, _, _, cible in reclassements:
        cur.execute("UPDATE credits SET role = ? WHERE id = ?", (cible, cid))
    conn.commit()
    return len(reclassements)


def revert(conn) -> int:
    """Repasse en Other tout crédit Discogs dont le `role_detail` est un libellé
    que la table sait mapper — l'inverse exact de `appliquer`."""
    client = _mapper()
    cur = conn.cursor()
    ids = [
        cid
        for cid, rd in cur.execute(
            "SELECT id, role_detail FROM credits WHERE source='discogs' AND role <> 'Other'"
        )
        if _est_un_libelle(rd) and client._map_discogs_role_to_enum(rd) != CreditRole.OTHER
    ]
    for cid in ids:
        cur.execute("UPDATE credits SET role = 'Other' WHERE id = ?", (cid,))
    conn.commit()
    return len(ids)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit réellement (backup avant)")
    parser.add_argument("--revert", action="store_true", help="repasse les reclassés en Other")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    try:
        if args.revert:
            backup = get_backup_manager().create_backup("before_reclass_revert")
            print(f"💾 Backup : {backup}")
            print(f"↩️  {revert(conn)} crédit(s) repassés en Other.")
            return 0

        reclassements, collisions = analyser(conn)
        rapport(reclassements, collisions, conn)

        if not args.apply:
            print("\nℹ️  DRY-RUN : rien n'a été écrit. Relance avec --apply.")
            return 0
        if not reclassements:
            print("\nRien à faire.")
            return 0

        backup = get_backup_manager().create_backup("before_reclass_discogs")
        if not backup:
            print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
            return 1
        print(f"\n💾 Backup : {backup}")
        print(f"✅ {appliquer(conn, reclassements)} crédit(s) reclassés.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
