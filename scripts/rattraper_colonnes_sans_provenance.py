"""Rattrapage de la famille d'audit `colonne_sans_provenance`.

    python scripts/rattraper_colonnes_sans_provenance.py                 # à blanc (défaut)
    python scripts/rattraper_colonnes_sans_provenance.py --apply         # backup + écriture
    python scripts/rattraper_colonnes_sans_provenance.py --base copie.db # sur une autre base

Une durée, une date, un ISRC ou un LRC en colonne, sans aucune observation : aucun
juge ne peut les démentir, et une ligne sœur ne les reçoit jamais (« Outro » :
ISRC chez Josman et Népal, pas chez Keroué, Lesram, Sheldon). Quatre issues :

- **ISRC prouvé faux** : Deezer le lit comme un titre GÉNÉRIQUE d'un AUTRE disque
  (l'« Outro » d'*OV3* sur celui de *Reste Calme*) : retiré, sœurs comprises, avec
  les mesures ReccoBeats lues par lui, et REFUSÉ (nature `isrc`, réversible) ;
- **ISRC soutenu** : Deezer le lit comme la piste de la fiche, ou comme le même
  titre, crédité à l'artiste, à ± 2 s d'une durée indépendante : observation
  `legacy`, que l'union donne aux sœurs ;
- **durée, date, LRC d'une fiche à ID de catalogue** : observation (`legacy`, ou la
  source que nomme `lyrics_synced_source` pour un LRC, le seul moyen que le verdict
  LRC le juge). Le verdict recalculé peut alors écarter un LRC que les paroles
  démentent : le passage à blanc ne le dit pas, à mesurer sur une copie (`--base`) ;
- **le reste** est listé, rien n'y est touché.

Réseau : une lecture Deezer `/track/isrc:…` par ISRC, sans écriture.
"""

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from sqlalchemy import text

ORIGINE = "regle:isrc_sans_provenance"
ADRESSE_DEEZER = "https://api.deezer.com/2.0/track/isrc:{}"
PAUSE_DEEZER_S = 0.2
#: Sources d'une durée lues sans passer par l'ISRC jugé ni par une recherche ancienne.
DUREES_NON_INDEPENDANTES = frozenset({"legacy", "reccobeats"})
ACCORD_DUREE_S = 2
SOURCE_DU_LRC = {"LRCLIB": "lrclib", "YouTube Music": "ytmusic", "Musixmatch": "musixmatch"}
IDS_DE_CATALOGUE = ("spotify_id", "deezer_id", "apple_music_id", "discogs_id")
COLONNES = ("duration", "release_date", "isrc", "lyrics_synced")

FAUX, SOUTENU, INCONNU = "faux", "soutenu", "inconnu"


@dataclass
class Piste:
    """Ce que Deezer sert pour un ISRC."""

    id: int
    title: str
    album: str | None
    duration: int | None
    interpretes: list[str] = field(default_factory=list)


def juger_isrc(fiche: dict, piste: Piste | None, durees: dict) -> tuple[str, str]:
    """PUR. `(verdict, motif)` d'un ISRC sans provenance. `durees` : `{source:
    secondes}` observées de la fiche. On ne retire que ce qu'on peut montrer : un
    titre générique ne se rapproche jamais sans le même disque (règle de tous les
    gates) ; tout autre écart reste à l'humain."""
    from src.utils.title_matching import (
        contains_as_words,
        names_match_as_words,
        normalize_title,
    )
    from src.utils.version_descriptors import meme_disque, titre_generique

    if piste is None:
        return INCONNU, "Deezer ne sert aucune piste pour cet ISRC"
    lue = (
        f"« {piste.title} » de {', '.join(piste.interpretes)}, *{piste.album}*, {piste.duration} s"
    )
    if fiche["deezer_id"] and str(fiche["deezer_id"]) == str(piste.id):
        return SOUTENU, f"la piste Deezer de la fiche ({lue})"
    if titre_generique(fiche["title"]) and not meme_disque(fiche["album"], piste.album):
        return FAUX, f"titre générique d'un autre disque que *{fiche['album']}* : {lue}"
    meme_titre = contains_as_words(normalize_title(piste.title), normalize_title(fiche["title"]))
    nommes = [n for n in (fiche["artiste"], fiche["primary_artist_name"]) if n]
    credite = any(names_match_as_words(n, i) for n in nommes for i in piste.interpretes)
    temoins = [d for s, d in durees.items() if s not in DUREES_NON_INDEPENDANTES and d]
    accord = bool(
        piste.duration and any(abs(int(d) - piste.duration) <= ACCORD_DUREE_S for d in temoins)
    )
    if meme_titre and credite and accord:
        return SOUTENU, f"même titre, crédité, durée à ± {ACCORD_DUREE_S} s : {lue}"
    return INCONNU, f"titre {meme_titre}, crédité {credite}, durée {accord} : {lue}"


def lire_piste_deezer(isrc: str) -> Piste | None:
    """La piste que Deezer sert pour cet ISRC, ou None (absente, ou sans réponse)."""
    try:
        data = httpx.get(ADRESSE_DEEZER.format(isrc), timeout=15).json()
    except (httpx.HTTPError, ValueError):
        return None
    if not data.get("id"):
        return None
    interpretes = [c.get("name") for c in data.get("contributors") or [] if c.get("name")]
    principal = (data.get("artist") or {}).get("name")
    if principal and principal not in interpretes:
        interpretes.insert(0, principal)
    return Piste(
        id=data["id"],
        title=data.get("title") or "",
        album=(data.get("album") or {}).get("title"),
        duration=data.get("duration"),
        interpretes=interpretes,
    )


def colonnes_a_rattraper(dm) -> list[dict]:
    """Une entrée par (fiche, colonne) remplie sans observation de son champ."""
    with dm.engine.connect() as conn:
        observees = {
            (r[0], r[1])
            for r in conn.execute(
                text(
                    "SELECT DISTINCT track_id, field FROM observations "
                    "WHERE field IN ('duration', 'release_date', 'isrc', 'lyrics_synced')"
                )
            )
        }
        fiches = (
            conn.execute(
                text(
                    "SELECT t.id, t.title, t.album, a.name AS artiste, t.primary_artist_name, "
                    "t.duration, t.release_date, t.isrc, t.lyrics_synced, t.lyrics_synced_source, "
                    "t.lyrics_scraped_at, t.spotify_id, t.deezer_id, t.apple_music_id, t.discogs_id "
                    "FROM tracks t JOIN artists a ON a.id = t.artist_id ORDER BY a.name, t.title"
                )
            )
            .mappings()
            .all()
        )
    return [
        {**f, "colonne": col}
        for f in fiches
        for col in COLONNES
        if f[col] not in (None, "") and (f["id"], col) not in observees
    ]


def durees_observees(dm, track_id: int) -> dict:
    return {o.source: o.value for o in dm.get_observations(track_id) if o.field == "duration"}


def observation_de(entree: dict):
    """L'observation qui donne sa provenance à une colonne non-ISRC, ou None si rien
    ne la soutient (aucun ID de catalogue, ou source du LRC inconnue)."""
    from src.enrichment.observation import Observation

    if not any(entree[i] for i in IDS_DE_CATALOGUE):
        return None
    col = entree["colonne"]
    if col != "lyrics_synced":
        return Observation(col, entree[col], "legacy")
    source = SOURCE_DU_LRC.get(entree["lyrics_synced_source"])
    if source is None:
        return None
    return Observation(col, entree[col], source, seen_at=entree["lyrics_scraped_at"])


def main(argv=None, lire=lire_piste_deezer) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.enrichment.observation import Observation
    from src.utils.corrections_fiches import fiches_par_id, memoriser_isrc_refuse
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    entrees = colonnes_a_rattraper(dm)
    lectures: dict[str, Piste | None] = {}
    a_retirer, a_poser, a_lister = [], [], []
    for e in entrees:
        lib = f"#{e['id']} {e['artiste']} « {e['title']} » {e['colonne']}"
        if e["colonne"] == "isrc":
            if e["isrc"] not in lectures:
                lectures[e["isrc"]] = lire(e["isrc"])
                time.sleep(PAUSE_DEEZER_S if lire is lire_piste_deezer else 0)
            verdict, motif = juger_isrc(e, lectures[e["isrc"]], durees_observees(dm, e["id"]))
            if verdict == FAUX:
                a_retirer.append((e, f"{lib} {e['isrc']} — {motif}"))
            elif verdict == SOUTENU:
                obs = Observation("isrc", e["isrc"], "legacy")
                a_poser.append((e, obs, f"{lib} {e['isrc']} — {motif}"))
            else:
                a_lister.append(f"{lib} {e['isrc']} — {motif}")
            continue
        obs = observation_de(e)
        if obs is None:
            a_lister.append(f"{lib} — rien ne la soutient")
        else:
            a_poser.append((e, obs, f"{lib} — source {obs.source}"))

    print(f"\n{len(entrees)} colonne(s) sans observation")
    print(f"\nISRC prouvés faux, à retirer et refuser ({len(a_retirer)}) :")
    for _, ligne in a_retirer:
        print(f"  {ligne}")
    print(f"\nProvenance à poser ({len(a_poser)}) :")
    for _, _, ligne in a_poser:
        print(f"  {ligne}")
    print(f"\nÀ l'humain, rien n'y est touché ({len(a_lister)}) :")
    for ligne in a_lister:
        print(f"  {ligne}")

    if not args.apply or not (a_retirer or a_poser):
        print("\n(à blanc — relancer avec --apply pour écrire)")
        return 0
    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup("before_rattraper_colonnes_sans_provenance")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"💾 Backup : {backup}")
    objets = fiches_par_id(e["id"] for e, _ in a_retirer)
    retires = 0
    for e, _ in a_retirer:
        # Refusé d'abord par la fiche, puis retiré : la sœur porteuse part avec elle.
        memoriser_isrc_refuse(objets[e["id"]], e["isrc"], origine=ORIGINE)
        retires += bool(dm.clear_track_isrc(e["id"])["isrc_efface"])
    for e, obs, _ in a_poser:
        dm.upsert_observations(e["id"], [obs])
    print(f"✅ {retires} ISRC retiré(s) et refusé(s) ; {len(a_poser)} provenance(s) posée(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
