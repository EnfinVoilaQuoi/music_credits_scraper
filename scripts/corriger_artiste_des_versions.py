"""Relations `version_of` / `remix_of` qui nomment l'artiste de la VERSION au lieu
de celui de l'original.

    python scripts/corriger_artiste_des_versions.py                 # à blanc (défaut)
    python scripts/corriger_artiste_des_versions.py --apply         # backup + écriture
    python scripts/corriger_artiste_des_versions.py --base copie.db # sur une autre base

`ecarts_deezer._ligne` écrivait dans la relation de la version créée l'artiste
qui la créait (`artist.name`), pas l'interprète de l'original (corrigé le
2026-11-22, `artiste_de_l_original`) : NTM « Un ange dans le ciel (Live …) »
#34979 disait « Suprême NTM » pour l'original de Kool Shen #31136. Le lien, la
page et le `track_id` étaient justes : seul le nom change.

Visée : une relation qui porte un `track_id` (seul `ecarts_deezer` en pose),
dont le nom est celui de l'artiste de la fiche et diffère de l'interprète de la
fiche désignée. L'écriture passe par `record_relationships` (sœurs comprises).

Règle de l'interprète (2026-11-22, `ecarts_deezer.artiste_de_l_original`) : le
principal GENIUS de l'original — celui que Genius écrit lui-même dans ses
relations (`primary_artist`) —, lu sur une fiche qui a sa page ; sinon
l'artiste dont c'est la fiche. Sans page, le principal d'une fiche créée depuis
Deezer est l'artiste du DISQUE où elle apparaît, qui peut n'être que son hôte.
Relu dans le brut Genius pour les 18 originaux qui ont une page : le principal
y est partout le `primary_artist` de Genius ; les noms qui étonnent (« -M- »
pour Oxmo, « Dajak » pour Swing, « Napoleon Da Legend » pour Akhenaton,
« Diamond Deuklo » pour les Casseurs Flowters) sont ceux de pages où l'artiste
n'est qu'invité, producteur, ou signataire du disque (*Comment c'est loin*).

Un original DOUTEUX n'est pas renommé : une fiche où l'artiste n'a qu'un rôle
secondaire, quand il a la sienne au même titre (« The Return of Chip
Douglas » de Nuevo (USA), produit par Kid Cudi) — la relation vise la mauvaise
fiche, `scripts/ponctuels/chip_douglas_version.py` la repointe.
"""

import argparse
import json
import sys
from pathlib import Path

TYPES = ("version_of", "remix_of")


def interprete(principal: str | None, proprietaire: str | None, page: bool = True) -> str | None:
    """PUR. Même règle que `ecarts_deezer.artiste_de_l_original` : le principal
    ne fait foi que lu sur une page Genius (`page`)."""
    return (principal if page else None) or proprietaire


def relations_corrigees(relations: list, artiste_de_la_fiche: str, originaux: dict) -> list | None:
    """PUR. Les relations de la fiche, le nom de l'original rétabli ; None si rien
    ne change. `originaux` : {track_id: interprète de l'original}."""
    changees, sortie = False, []
    for r in relations:
        if (
            isinstance(r, dict)
            and r.get("type") in TYPES
            and r.get("track_id") in originaux
            and r.get("artist") == artiste_de_la_fiche
            and originaux[r["track_id"]]
            and originaux[r["track_id"]] != artiste_de_la_fiche
        ):
            r = {**r, "artist": originaux[r["track_id"]]}
            changees = True
        sortie.append(r)
    return sortie if changees else None


def originaux_douteux(fiches) -> set[int]:
    """PUR. Les fiches à rôle SECONDAIRE dont l'artiste a une fiche propre au
    même titre normalisé : l'original d'une version n'y est pas (même règle que
    `ecarts_deezer.classer` et `version_heritage.socle_parmi`). `fiches` :
    `(id, principal, artiste, genius_id, genius_url, rôle, artist_id, titre)`."""
    from src.utils.title_matching import normalize_title

    propres = {(f[6], normalize_title(f[7] or "")) for f in fiches if not f[5]}
    return {f[0] for f in fiches if f[5] and (f[6], normalize_title(f[7] or "")) in propres}


def _cas(dm) -> list[dict]:
    from sqlalchemy import text

    with dm.engine.connect() as conn:
        lignes = conn.execute(
            text(
                "SELECT t.id, t.title, a.name, t.relationships FROM tracks t "
                "JOIN artists a ON a.id = t.artist_id "
                "WHERE t.relationships LIKE '%track_id%'"
            )
        ).all()
        fiches = conn.execute(
            text(
                "SELECT t.id, t.primary_artist_name, a.name, t.genius_id, t.genius_url, "
                "t.secondary_role, t.artist_id, t.title FROM tracks t "
                "JOIN artists a ON a.id = t.artist_id"
            )
        ).all()
        titres = dict(conn.execute(text("SELECT id, title FROM tracks")).all())
    douteux = originaux_douteux(fiches)
    originaux = {
        r[0]: interprete(r[1], r[2], bool(r[3] or r[4])) for r in fiches if r[0] not in douteux
    }
    cas, ecartes = [], []
    for tid, titre, artiste, brut in lignes:
        try:
            relations = json.loads(brut or "[]")
        except json.JSONDecodeError:
            continue
        ecartes += [
            (tid, titre, artiste, r["track_id"], titres.get(r["track_id"]))
            for r in relations
            if isinstance(r, dict) and r.get("type") in TYPES and r.get("track_id") in douteux
        ]
        corrigees = relations_corrigees(relations, artiste, originaux)
        if corrigees is not None:
            cas.append(
                {
                    "id": tid,
                    "titre": titre,
                    "artiste": artiste,
                    "relations": corrigees,
                    "originaux": [
                        (r["track_id"], titres.get(r["track_id"]), r["artist"])
                        for r in corrigees
                        if isinstance(r, dict) and r.get("track_id") in originaux
                    ],
                }
            )
    return cas, ecartes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    import src.utils.data_manager as module_dm
    from src.utils.logger import configurer_journaux

    configurer_journaux()
    if args.base:
        module_dm.DATABASE_URL = f"sqlite:///{args.base.resolve().as_posix()}"
    dm = module_dm.DataManager()
    print(f"Base : {dm.db_path}")

    cas, ecartes = _cas(dm)
    for tid, titre, artiste, oid, otitre in ecartes:
        print(
            f"  ⚠ #{tid} {artiste} « {titre} » → original douteux #{oid} « {otitre} » "
            "(rôle secondaire, fiche propre au même titre) : non renommé"
        )
    for c in cas:
        for oid, otitre, nom in c["originaux"]:
            print(
                f"  #{c['id']} {c['artiste']} « {c['titre']} » → original #{oid} "
                f"« {otitre} » : « {c['artiste']} » → « {nom} »"
            )
    print(f"{len(cas)} fiche(s) à corriger.")
    if not args.apply or not cas:
        print("(à blanc — relancer avec --apply pour écrire)")
        return 0
    if not _sauvegarder(dm, args, "before_corriger_artiste_des_versions"):
        return 1
    echecs = sum(0 if dm.record_relationships(c["id"], c["relations"]) else 1 for c in cas)
    reste, _ = _cas(dm)
    print(f"✅ {len(cas) - echecs} fiche(s) corrigée(s) ; reste {len(reste)}.")
    return 0 if not reste else 2


def _sauvegarder(dm, args, nom: str) -> bool:
    from src.utils.database_backup import DatabaseBackupManager, get_backup_manager

    sauvegarde = (
        DatabaseBackupManager(
            db_path=str(dm.db_path), backup_dir=str(Path(dm.db_path).parent / "backups")
        )
        if args.base
        else get_backup_manager()
    )
    backup = sauvegarde.create_backup(nom)
    if not backup:
        print("❌ Backup impossible — abandon.")
        return False
    print(f"💾 Backup : {backup}")
    return True


if __name__ == "__main__":
    sys.exit(main())
