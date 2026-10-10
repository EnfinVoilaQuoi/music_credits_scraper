"""Réparation des paroles synchronisées par la garde LRC (CORRECTIFS 2.4).

Rejoue les règles de la phase 1 sur ce qui est DÉJÀ en base, sans en
réimplémenter aucune :

- `services.versions.constater_instrumentaux` : un titre instrumental est
  constaté — ni paroles ni LRC (écrivain `constater_instrumental`) ;
- `lrc_garde.GardeLrc` : un LRC qui appartient à une AUTRE fiche de l'artiste
  (« Fin » ← « Histoire sans fin »), ou le LRC de l'original sur une version
  qui n'est pas le même enregistrement, est retiré par l'écrivain `retirer_lrc`
  (observations de toute source qui l'a servi, sœurs comprises, colonne
  ré-arbitrée). La fiche repart « à chercher » ; la garde refusera qu'on lui
  redonne le même LRC.

    python scripts/repair_lrc_garde.py                 # dry-run (défaut)
    python scripts/repair_lrc_garde.py --apply         # backup + écriture
    python scripts/repair_lrc_garde.py --artiste Booba
"""

from __future__ import annotations

import argparse
import sys

from src.services.versions import constater_instrumentaux
from src.utils.data_manager import DataManager
from src.utils.database_backup import get_backup_manager
from src.utils.lrc_garde import GardeLrc
from src.utils.version_heritage import est_instrumental


def _plan(dm, artiste) -> tuple[list, list]:
    """(instrumentaux à constater, (fiche, motif) des LRC à retirer)."""
    tracks = dm.get_artist_tracks(artiste.id)
    instrus = [
        t
        for t in tracks
        if est_instrumental(t.title)
        and not (t.lyrics.instrumental is True and not t.lyrics.text and not t.lyrics.synced)
    ]
    garde = GardeLrc(tracks)
    a_retirer = []
    for t in tracks:
        if t in instrus or not t.lyrics.synced:
            continue
        motif = garde.motif(t, t.lyrics.synced)
        if motif:
            a_retirer.append((t, motif))
    return instrus, a_retirer


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    p.add_argument("--artiste", action="append", help="limiter à cet artiste (répétable)")
    a = p.parse_args(argv)

    dm = DataManager()
    noms = a.artiste or dm.get_artist_names()
    plans = []
    for nom in noms:
        artiste = dm.get_artist_by_name(nom)
        if artiste is None:
            print(f"❌ Artiste absent : {nom}")
            return 1
        instrus, a_retirer = _plan(dm, artiste)
        if instrus or a_retirer:
            plans.append((artiste, instrus, a_retirer))

    n_instru = sum(len(i) for _, i, _ in plans)
    n_lrc = sum(len(r) for _, _, r in plans)
    print(f"{n_instru} instrumental(aux) à constater, {n_lrc} LRC à retirer\n")
    for artiste, instrus, a_retirer in plans:
        print(f"── {artiste.name}")
        for t in instrus:
            print(
                f"   🎹 #{t.id} « {t.title} » paroles={len(t.lyrics.text or '')} "
                f"LRC={'oui' if t.lyrics.synced else 'non'}"
            )
        for t, motif in a_retirer:
            print(f"   ⏱ #{t.id} « {t.title} » ({t.lyrics.synced_source}) — {motif}")

    if not a.apply:
        print("\n(dry-run — relancer avec --apply pour écrire)")
        return 0
    backup = get_backup_manager().create_backup("before_repair_lrc_garde")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"\n💾 Backup : {backup}")
    retirees = 0
    for _artiste, instrus, a_retirer in plans:
        constater_instrumentaux(dm, instrus)
        for t, _motif in a_retirer:
            retirees += dm.retirer_lrc(t.id, t.lyrics.synced)
    print(f"✅ {n_instru} instrumental(aux) constaté(s), {retirees} observation(s) LRC retirée(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
