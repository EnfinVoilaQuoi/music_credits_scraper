"""Retire ce que les démos, prises alternatives, snippets et références ont
hérité à tort de leur original.

Jusqu'au 2026-09-28 une démo n'avait pas de ligne dans la table d'héritage
(`version_heritage.REGLES`) et retombait sur « edition » : paroles, écriture ET
production de l'original — alors que son texte diffère souvent. Et les
« Reference », « [V3] », « Snippet », « OG » n'étaient même pas reconnus comme
des versions. Décision utilisateur : démo / alternate / snippet = écriture
seule ; référence = rien. Le verdict vient de `version_heritage.famille_de` et
de `REGLES` — aucune règle réimplémentée ici ; l'écriture passe par
`TrackRepository.retirer_heritage`.

Usage :
    python scripts/repair_heritage_versions.py            # dry-run (défaut)
    python scripts/repair_heritage_versions.py --apply    # backup + écriture
"""

import argparse
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from collections import Counter

from sqlalchemy import text

from src.models.track import _WRITER_ROLES
from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.version_heritage import REGLES, famille_de


def candidates(dm: DataManager) -> list[dict]:
    """Fiches d'une famille qui n'hérite pas des paroles ni de la production,
    et qui portent pourtant un héritage de ce genre."""
    with dm.engine.connect() as conn:
        heritages = conn.execute(
            text(
                "SELECT t.id, t.title, a.name, t.lyrics_source, "
                "(SELECT group_concat(c.role, '|') FROM credits c "
                " WHERE c.track_id = t.id AND c.source = 'heritage') "
                "FROM tracks t JOIN artists a ON a.id = t.artist_id"
            )
        ).all()
    sortie = []
    for tid, titre, nom, source_paroles, roles in heritages:
        famille = famille_de(titre or "")
        regle = REGLES.get(famille)
        if regle is None or regle.paroles or regle.production:
            continue
        paroles = (source_paroles or "").startswith("heritage")
        ecriture = {r.value for r in _WRITER_ROLES} if regle.ecriture else set()
        roles = [r for r in (roles or "").split("|") if r and r not in ecriture]
        if not paroles and not roles:
            continue
        sortie.append(
            {
                "id": tid,
                "titre": titre,
                "artiste": nom,
                "famille": famille,
                "garder_ecriture": regle.ecriture,
                "paroles": paroles,
                "roles": roles,
            }
        )
    return sortie


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="écrit (backup d'abord)")
    args = ap.parse_args()

    dm = DataManager()
    fiches = candidates(dm)
    par_famille = Counter(f["famille"] for f in fiches)
    for f in fiches[:40]:
        print(
            f"  {f['artiste'][:14]:14} {f['famille']:10} {f['titre'][:48]:48} "
            f"{'paroles, ' if f['paroles'] else ''}{len(f['roles'])} crédit(s) : {', '.join(sorted(set(f['roles'])))[:60]}"
        )
    print(
        f"\n{len(fiches)} fiche(s) : {dict(par_famille)} — "
        f"{sum(f['paroles'] for f in fiches)} paroles héritées"
    )
    if not args.apply or not fiches:
        if fiches:
            print("Dry-run : relancer avec --apply (backup automatique).")
        return 0
    sauvegarde = get_backup_manager().create_backup("before_repair_heritage_versions")
    if sauvegarde is None:
        print("❌ Backup impossible — rien n'est écrit.")
        return 1
    print(f"💾 Backup : {sauvegarde}")
    credits = paroles = 0
    for f in fiches:
        n, p = dm.retirer_heritage(f["id"], garder_ecriture=f["garder_ecriture"])
        credits += n
        paroles += int(p)
    print(f"✅ {credits} crédit(s) hérité(s) retiré(s), {paroles} paroles héritées retirées.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
