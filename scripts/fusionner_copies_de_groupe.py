"""Réparation : les fiches d'un GROUPE recopiées au nom d'un de ses membres.

    python scripts/fusionner_copies_de_groupe.py                 # à blanc (défaut)
    python scripts/fusionner_copies_de_groupe.py --apply         # backup + fusions
    python scripts/fusionner_copies_de_groupe.py --base copie.db # sur une autre base

Jusqu'au 2026-11-18, la fin de l'étape crédits réécrivait au nom du membre toutes
les fiches lues, celles que son groupe lui apporte comprises (`credits._sauver`) :
`save_track` ne les trouvait pas chez lui et en créait des copies, où atterrissaient
les données lues (LRC, place Discogs, BPM…). Chez Akhenaton : 54 fiches sans page
et 18 lignes de pages Genius d'IAM ; chez Rim’K : 4 et 5 de 113. Le panneau les
voyait en doublons (`revue:doublon`), l'audit en `isrc_edition_autre_fiche`.

Une ligne du membre est une COPIE de la fiche du groupe (formation `groupe`
confirmée, qui apporte TOUS ses morceaux) quand elle a été créée APRÈS la fiche du
groupe (identifiant plus grand : la copie naît de la fiche) et :
  · sans page Genius des deux côtés : même ID Deezer, sinon même ISRC, sinon même
    titre exact — et une seule fiche du groupe y répond ;
  · même page Genius, et le membre ne figure pas au générique de la page (ni sous
    son nom ni sous un alias confirmé) : crédité, il a droit à sa ligne sœur.
Mesuré le 2026-11-18 sur toute la base : sans la condition d'ordre, 13 lignes sœurs
justes tombaient (JACKBOYS chez Travis Scott, Casseurs Flowters chez OrelSan :
créées par la discographie Genius du membre, avant celles du groupe, sans crédit
de performance au générique).
La copie est FUSIONNÉE dans la fiche du groupe (`merge_tracks` : crédits,
observations, vidéos, éditions, ID Spotify réunis ; rien n'est perdu).
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Copie:
    membre: str
    groupe: str
    copie_id: int
    garde_id: int
    titre: str
    motif: str


def _cle(v) -> str:
    return str(v or "").strip().upper()


def juger(membre: str, groupe: str, du_membre, du_groupe, noms_du_membre: set[str]) -> list[Copie]:
    """PUR. Les copies parmi les fiches `du_membre` (objets Track) ; `du_groupe`
    = fiches du groupe. Une fiche à plusieurs jumelles possibles est laissée."""
    copies = []
    for t in du_membre:
        du_groupe_avant = [u for u in du_groupe if u.id < t.id]
        if t.genius_id:
            jumelles = [u for u in du_groupe if u.genius_id and u.genius_id == t.genius_id]
            if len(jumelles) != 1 or jumelles[0].id > t.id:
                continue
            u = jumelles[0]
            present = u.personne_presente(noms_du_membre) or t.personne_presente(noms_du_membre)
            if not present:
                copies.append(Copie(membre, groupe, t.id, u.id, t.title, "même page, non crédité"))
            continue
        sans_page = [u for u in du_groupe_avant if not u.genius_id]
        for motif, egal in (
            ("même ID Deezer", lambda u, t=t: t.deezer_id and u.deezer_id == t.deezer_id),
            ("même ISRC", lambda u, t=t: _cle(t.isrc) and _cle(u.isrc) == _cle(t.isrc)),
            ("même titre", lambda u, t=t: u.title == t.title),
        ):
            jumelles = [u for u in sans_page if egal(u)]
            if len(jumelles) == 1:
                copies.append(Copie(membre, groupe, t.id, jumelles[0].id, t.title, motif))
                break
            if jumelles:
                break  # plusieurs : ambigu, laissé
    return copies


def trouver(dm) -> list[Copie]:
    copies = []
    for artiste in dm.artistes_de_la_base():
        groupes = [g for g in dm.ids_discographie_reunie(artiste.id) if g != artiste.id]
        if not groupes:
            continue
        noms = dm.noms_de_lartiste(artiste.id, artiste.name)
        du_membre = dm.get_artist_tracks(artiste.id)
        for gid in groupes:
            du_groupe = dm.get_artist_tracks(gid)
            nom_groupe = du_groupe[0].artist.name if du_groupe else str(gid)
            copies += juger(artiste.name, nom_groupe, du_membre, du_groupe, noms)
    return copies


def rapport(copies: list[Copie]) -> None:
    print(f"{len(copies)} copie(s) à fusionner dans la fiche du groupe")
    for c in copies:
        print(
            f"  [{c.membre} ← {c.groupe}] #{c.copie_id} → #{c.garde_id} « {c.titre} » ({c.motif})"
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    copies = trouver(dm)
    rapport(copies)
    if not args.apply:
        print("\n(à blanc — relancer avec --apply pour écrire)")
        return 0
    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup("before_fusionner_copies_de_groupe")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"💾 Backup : {backup}")
    faites = sum(1 for c in copies if dm.merge_tracks(c.garde_id, c.copie_id))
    print(f"✅ {faites} / {len(copies)} fusion(s)")
    return 0 if faites == len(copies) else 1


if __name__ == "__main__":
    sys.exit(main())
