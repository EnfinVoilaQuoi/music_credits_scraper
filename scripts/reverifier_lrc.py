"""Retire les LRC que les paroles Genius DÉMENTENT, avec l'oracle du moment.

L'oracle (`concordance_paroles.lrc_dementi`) a gagné les BIGRAMMES le 2026-09-27 :
un LRC est démenti sous 40 % de mots communs OU sous 10 % de paires de mots
consécutives communes. La lecture l'applique d'elle-même, mais les colonnes
`lyrics_synced*` et les observations écrites AVANT gardent l'ancien verdict :
ce script les rejoue par `TrackRepository.reverifier_lrc` (sœurs comprises,
même arbitrage que la lecture) — aucune règle réimplémentée ici.

Usage :
    python scripts/reverifier_lrc.py                    # dry-run (défaut)
    python scripts/reverifier_lrc.py --apply            # backup + écriture
    python scripts/reverifier_lrc.py --artiste "Kanye West"
"""

import argparse
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from src.utils.concordance_paroles import (
    lrc_dementi,
    paroles_de_reference,
    recouvrement,
    recouvrement_bigrammes,
)
from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager


def candidates(dm: DataManager, artiste: str | None) -> list[dict]:
    """Fiches dont au moins un LRC (observation ou colonne) est démenti."""
    filtre = "AND a.name = :nom" if artiste else ""
    with dm.engine.connect() as conn:
        lignes = conn.execute(
            text(
                "SELECT t.id, t.title, a.name, t.lyrics, t.lyrics_source, t.lyrics_synced, "
                "o.source, o.value FROM tracks t JOIN artists a ON a.id = t.artist_id "
                "LEFT JOIN observations o ON o.track_id = t.id AND o.field = 'lyrics_synced' "
                f"WHERE (t.lyrics_synced IS NOT NULL OR o.id IS NOT NULL) {filtre}"
            ),
            {"nom": artiste},
        ).all()
    fiches: dict[int, dict] = {}
    for tid, titre, nom, texte, source_texte, colonne, source, valeur in lignes:
        paroles = paroles_de_reference(texte, source_texte)
        for lrc, origine in ((valeur, source), (colonne, "colonne")):
            if lrc and lrc_dementi(paroles, lrc):
                f = fiches.setdefault(
                    tid, {"id": tid, "titre": titre, "artiste": nom, "dementis": {}}
                )
                f["dementis"][origine] = (
                    recouvrement(paroles, lrc),
                    recouvrement_bigrammes(paroles, lrc),
                )
    return sorted(fiches.values(), key=lambda f: (f["artiste"], f["titre"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="écrit (backup d'abord)")
    ap.add_argument("--artiste", help="un seul artiste")
    args = ap.parse_args()

    dm = DataManager()
    fiches = candidates(dm, args.artiste)
    for f in fiches:
        detail = ", ".join(
            f"{o} {m:.0%} mots / {b:.0%} bigrammes" if b is not None else f"{o} {m:.0%} mots"
            for o, (m, b) in f["dementis"].items()
        )
        print(f"  {f['artiste'][:18]:18} {f['titre'][:50]:50} {detail}")
    print(f"\n{len(fiches)} fiche(s) portent un LRC démenti.")
    if not args.apply or not fiches:
        if fiches:
            print("Dry-run : relancer avec --apply pour les retirer (backup automatique).")
        return 0

    sauvegarde = get_backup_manager().create_backup("before_reverifier_lrc_bigrammes")
    if sauvegarde is None:
        print("❌ Backup impossible — rien n'est écrit.")
        return 1
    print(f"💾 Backup : {sauvegarde}")
    retirees = modifiees = 0
    for f in fiches:
        n, colonne = dm.reverifier_lrc(f["id"])
        retirees += n
        modifiees += int(colonne)
    print(f"✅ {retirees} observation(s) retirée(s), {modifiees} colonne(s) corrigée(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
