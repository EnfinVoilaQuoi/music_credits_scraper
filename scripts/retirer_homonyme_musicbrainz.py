"""Retire ce qu'un HOMONYME MusicBrainz a posé chez un artiste (2026-11-22).

    python scripts/retirer_homonyme_musicbrainz.py                      # à blanc, cas connus
    python scripts/retirer_homonyme_musicbrainz.py --apply              # backup + écriture
    python scripts/retirer_homonyme_musicbrainz.py Swing 3e39ef34-…     # un cas nommé
    python scripts/retirer_homonyme_musicbrainz.py --base <copie.db>    # sur une copie
    python scripts/retirer_homonyme_musicbrainz.py --brut <brut.db>     # brut lu ailleurs

Jusqu'au 2026-11-22, `MusicBrainzAPI.resoudre_artiste` acceptait un candidat
SEUL au nom exact sans rien en commun avec la base : Swing (rap FR) a été relié
au duo de Hong Kong « Swing » (3e39ef34…), qui a posé son profil Spotify
(`artist_spotify_ids`, source musicbrainz, servi au gate d'identité), ses
membres (Eric Kwok, Jerald Chan) et son ancien nom « Snowman » (alias).

La PREUVE se relit dans le brut MusicBrainz (lookup gardé de l'homonyme) : ne
part que ce que CET homonyme lie — un profil Spotify qu'il lie, un lien
(`member_of` / `has_member` / `alias`) au nom d'une de ses relations ou de ses
alias, et seulement si MusicBrainz est sa SEULE source (un lien que Discogs
donne aussi reste à l'humain).

Gestes, tous réversibles :
- profil Spotify : refus `profil_spotify` + retrait de l'artiste, le geste de
  `scripts/refuser_profil_spotify.py` (`--oublier` le lève) ;
- lien : statut « refused » (`set_relation_status`) — gardé en base, il n'est
  plus reproposé ; « Rétablir » de la fenêtre Formations le rend.

Liste des cas : la simulation du 2026-11-22 (182 lectures du brut, 107
artistes) n'en désigne qu'un, Swing.
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

#: (artiste, MBID de l'homonyme) — tirés de la simulation du 2026-11-22.
CAS_CONNUS = [("Swing", "3e39ef34-c929-405d-8ac0-e62cef0b9329")]

KINDS_RETIRABLES = ("member_of", "has_member", "alias")


def lookup_garde(mbid: str, chemin_brut: Path | None = None) -> dict | None:
    """Le dernier lookup gardé de `mbid` dans le brut (lecture seule)."""
    from src.persistence import brut

    dernier = None
    for requete, corps, _premiere, derniere in brut.parcourir("musicbrainz", chemin_brut):
        u = urlsplit(requete)
        if u.path.rstrip("/").rsplit("/", 1)[-1] != mbid:
            continue
        if "release-groups" not in parse_qs(u.query).get("inc", [""])[0]:
            continue
        if dernier is None or derniere >= dernier[0]:
            dernier = (derniere, json.loads(corps))
    return dernier[1] if dernier else None


def plan(
    dm, nom: str, mbid: str, chemin_brut: Path | None = None
) -> tuple[object, list[str], list[tuple[str, str]], list[str]]:
    """(artiste, profils Spotify à refuser, liens (kind, nom) à refuser, constats)."""
    from src.api.musicbrainz_api import (
        aliases_de,
        compter_albums_communs,
        relations_membre,
        spotify_ids_de,
    )
    from src.utils.title_matching import normalize_name, normalize_title

    constats = []
    artiste = dm.get_artist_by_name(nom)
    if artiste is None:
        return None, [], [], [f"❌ Artiste inconnu : {nom}"]
    detail = lookup_garde(mbid, chemin_brut)
    if detail is None:
        return artiste, [], [], [f"❌ Aucun lookup de {mbid} dans le brut : rien à prouver"]
    nos_albums = {normalize_title(t.album) for t in dm.get_artist_tracks(artiste.id) if t.album}
    communs = compter_albums_communs(detail, nos_albums)
    constats.append(
        f"Homonyme {mbid} : « {detail.get('name')} », {detail.get('country') or '?'}, "
        f"« {detail.get('disambiguation') or ''} » — {communs} album(s) commun(s) "
        f"sur {len(nos_albums)} albums en base"
    )
    if communs:
        constats.append("⚠ L'homonyme partage un album avec la base : rien n'est retiré.")
        return artiste, [], [], constats

    lies = set(spotify_ids_de(detail))
    en_base = dm.ids_spotify_connus(artiste.id)
    profils = sorted(lies & en_base)
    if artiste.spotify_id in lies:
        constats.append(
            f"⚠ L'ID Spotify mémorisé de l'artiste ({artiste.spotify_id}) est lié par "
            "l'homonyme : à l'humain."
        )
        profils = [p for p in profils if p != artiste.spotify_id]

    noms = {normalize_name(r.nom) for r in relations_membre(detail)}
    noms |= {normalize_name(a.nom) for a in aliases_de(detail)}
    noms.discard(normalize_name(artiste.name))
    liens, gardes = [], []
    for rel in dm.get_artist_relations(artiste.id, status=None):
        if rel.kind not in KINDS_RETIRABLES or normalize_name(rel.related_name) not in noms:
            continue
        if rel.status == "refused":
            continue
        if (rel.source or "") == "musicbrainz":
            liens.append((rel.kind, rel.related_name, rel.status))
        else:
            gardes.append(f"{rel.kind} {rel.related_name} (source {rel.source}, gardé)")
    constats += [f"  · {g}" for g in gardes]
    return artiste, profils, liens, constats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("artiste", nargs="?")
    parser.add_argument("mbid", nargs="?", help="MBID de l'homonyme")
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    parser.add_argument("--brut", type=Path, help="autre brut que data/brut/brut.db (lu seul)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if bool(args.artiste) != bool(args.mbid):
        parser.error("un artiste ET le MBID de son homonyme, ou rien (cas connus)")

    import src.utils.data_manager as module_dm
    from src.utils import corrections_fiches as cf
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")
    cas = [(args.artiste, args.mbid)] if args.artiste else CAS_CONNUS

    a_faire = []
    for nom, mbid in cas:
        artiste, profils, liens, constats = plan(dm, nom, mbid, args.brut)
        print(f"\n== {nom}")
        for c in constats:
            print(c)
        for p in profils:
            print(f"  → profil Spotify {p} : refusé (profil_spotify) et retiré")
        for kind, rel, statut in liens:
            print(f"  → lien {kind} « {rel} » ({statut}) : refusé")
        if not profils and not liens:
            print("  (rien à retirer)")
        elif artiste is not None:
            a_faire.append((artiste, profils, liens))

    if not args.apply:
        print("\n(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not a_faire:
        return 0
    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    if not sauvegarde.create_backup("before_retirer_homonyme_musicbrainz"):
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    for artiste, profils, liens in a_faire:
        for p in profils:
            cf.memoriser_profil_spotify_refuse(artiste.name, p, artiste.name)
            dm.retirer_profil_spotify(artiste.id, p)
        for kind, rel, _statut in liens:
            dm.set_relation_status(artiste.id, rel, kind, "refused")
        print(f"✅ {artiste.name} : {len(profils)} profil(s), {len(liens)} lien(s) refusés")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
