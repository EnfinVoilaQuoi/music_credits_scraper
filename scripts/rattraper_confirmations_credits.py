"""Rattrapage : les sources qui confirment un crédit (`credits.confirme_par`, e70).

    python scripts/rattraper_confirmations_credits.py                 # à blanc (défaut)
    python scripts/rattraper_confirmations_credits.py --apply         # backup + écriture
    python scripts/rattraper_confirmations_credits.py --base copie.db # sur une autre base
    python scripts/rattraper_confirmations_credits.py --sans-brut     # lignes sœurs seules

Jusqu'au 2026-11-23, la table `credits` ne gardait qu'UNE source par crédit : la
seconde source qui donnait le même crédit (même nom, rôle, détail) était jetée.
Ce qui se reconstruit sans réseau, et seulement à la MÊME clé de table :

1. lignes sœurs (même page Genius) : un même crédit porté chez l'une par une
   source, chez l'autre par une autre — chaque ligne reçoit les deux ;
2. Discogs, depuis le brut : le disque Discogs de la fiche (`tracks.discogs_id`,
   celui que le run a retenu), relu dans sa dernière réponse gardée ; ses
   crédits qui concernent la piste (`concerne_la_piste`, place notée par le run)
   et dont le rôle se range comme au run (`DiscogsClient._map_discogs_role_to_enum`)
   confirment le crédit existant de même clé.

Pas les descriptions YouTube : le brut `youtube_data` ne date que du 2026-10-29
et n'en confirme aucun (mesuré le 2026-11-23) ; ni Deezer. Ces confirmations
reviendront aux prochains runs (une étape qui relit la source).

Rien n'est retiré, aucun crédit n'est créé : seules des confirmations s'ajoutent.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import text


def _est_heritage(source: str | None) -> bool:
    from src.utils.version_heritage import est_herite

    return est_herite(source)


def confirmations_des_soeurs(lignes: list[dict]) -> dict[int, set[str]]:
    """PUR. `{credit_id: sources à ajouter}` : chaque ligne d'une famille de sœurs
    (même `genius_id`) reçoit les sources du même crédit chez les autres. Une
    ligne d'héritage ne reçoit rien et ne donne rien."""
    union: dict[tuple, set[str]] = defaultdict(set)
    for ligne in lignes:
        if ligne["genius_id"] is None or _est_heritage(ligne["source"]):
            continue
        union[_cle_famille(ligne)] |= {ligne["source"], *ligne["confirmations"]}
    ajouts: dict[int, set[str]] = {}
    for ligne in lignes:
        if ligne["genius_id"] is None or _est_heritage(ligne["source"]):
            continue
        manque = union[_cle_famille(ligne)] - {ligne["source"]} - set(ligne["confirmations"])
        if manque:
            ajouts[ligne["id"]] = manque
    return ajouts


def _cle_famille(ligne: dict) -> tuple:
    return (ligne["genius_id"], ligne["name"], ligne["role"], ligne["role_detail"] or "")


def credits_discogs_de_la_piste(release: dict, position: str | None) -> set[tuple[str, str, str]]:
    """PUR. Les clés `(nom, rôle, détail)` que le run poserait depuis ce disque
    pour la piste à `position` (`discogs_api` : crédits du DISQUE, filtrés par
    leurs pistes ; détail = libellé brut d'un rôle rangé en OTHER)."""
    from src.api.discogs_api import DiscogsClient
    from src.models.track import CreditRole
    from src.utils.discogs_positions import concerne_la_piste

    ranger = DiscogsClient._map_discogs_role_to_enum
    cles = set()
    for credit in release.get("extraartists") or []:
        nom = credit.get("name")
        if not nom or not concerne_la_piste(credit.get("tracks"), position):
            continue
        libelle = credit.get("role") or "Unknown"
        role = ranger(None, libelle)
        cles.add((nom, role.value, libelle if role == CreditRole.OTHER else ""))
    return cles


def confirmations_discogs(lignes: list[dict], fiches: dict[int, dict], lire_disque) -> dict:
    """`{credit_id: {"discogs"}}` : crédit d'une autre source que le disque Discogs
    de sa fiche donne à la même clé. `fiches` : `{track_id: {discogs_id, position}}` ;
    `lire_disque(discogs_id)` rend la réponse gardée (dict) ou None."""
    par_fiche: dict[int, list[dict]] = defaultdict(list)
    for ligne in lignes:
        if ligne["track_id"] in fiches:
            par_fiche[ligne["track_id"]].append(ligne)
    ajouts = {}
    for tid, credits in par_fiche.items():
        fiche = fiches[tid]
        release = lire_disque(fiche["discogs_id"])
        if not release:
            continue
        cles = credits_discogs_de_la_piste(release, fiche["position"])
        for ligne in credits:
            if ligne["source"] == "discogs" or "discogs" in ligne["confirmations"]:
                continue
            if _est_heritage(ligne["source"]):
                continue
            if (ligne["name"], ligne["role"], ligne["role_detail"] or "") in cles:
                ajouts[ligne["id"]] = {"discogs"}
    return ajouts


def lire_lignes(dm) -> list[dict]:
    from src.utils.credit_sources import lire

    with dm.engine.connect() as conn:
        return [
            {**dict(r), "confirmations": lire(r["confirme_par"])}
            for r in conn.execute(
                text(
                    "SELECT c.id, c.track_id, t.genius_id, c.name, c.role, c.role_detail, "
                    "c.source, c.confirme_par FROM credits c JOIN tracks t ON t.id = c.track_id"
                )
            ).mappings()
        ]


def lire_fiches_discogs(dm) -> dict[int, dict]:
    with dm.engine.connect() as conn:
        return {
            r["id"]: {"discogs_id": r["discogs_id"], "position": r["position"]}
            for r in conn.execute(
                text(
                    "SELECT t.id, t.discogs_id, (SELECT o.value FROM observations o "
                    "WHERE o.track_id = t.id AND o.field = 'discogs_position' "
                    "AND o.source = 'discogs') AS position FROM tracks t "
                    "WHERE t.discogs_id IS NOT NULL AND t.discogs_id <> ''"
                )
            ).mappings()
        }


def lecteur_du_brut(chemin: Path | None):
    """`discogs_id → dict | None`, la DERNIÈRE réponse gardée du disque."""
    from src.persistence import brut

    memoire: dict = {}

    def lire(discogs_id):
        if discogs_id not in memoire:
            corps = brut.corps_gardes(
                "discogs", f"https://api.discogs.com/releases/{discogs_id}", chemin
            )
            try:
                memoire[discogs_id] = json.loads(corps[-1]) if corps else None
            except ValueError:
                memoire[discogs_id] = None
        return memoire[discogs_id]

    return lire


def fusionner(*ajouts: dict) -> dict[int, set[str]]:
    out: dict[int, set[str]] = defaultdict(set)
    for a in ajouts:
        for cid, sources in a.items():
            out[cid] |= sources
    return dict(out)


def calculer(dm, chemin_brut: Path | None, avec_brut: bool) -> tuple[dict, Counter]:
    lignes = lire_lignes(dm)
    bilan = Counter()
    discogs = {}
    if avec_brut:
        discogs = confirmations_discogs(
            lignes, lire_fiches_discogs(dm), lecteur_du_brut(chemin_brut)
        )
        bilan["discogs (brut)"] = len(discogs)
        for ligne in lignes:  # les sœurs reçoivent aussi ce que le brut confirme
            if ligne["id"] in discogs:
                ligne["confirmations"] = frozenset(ligne["confirmations"]) | discogs[ligne["id"]]
    soeurs = confirmations_des_soeurs(lignes)
    bilan["lignes sœurs"] = len(soeurs)
    ajouts = fusionner(discogs, soeurs)
    par_source = Counter(s for sources in ajouts.values() for s in sources)
    for s, n in par_source.items():
        bilan[f"confirmation {s}"] = n
    return ajouts, bilan


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit (backup automatique avant)")
    parser.add_argument("--base", type=Path, help="autre base que data/music_credits.db")
    parser.add_argument("--brut", type=Path, help="autre brut que data/brut/brut.db")
    parser.add_argument("--sans-brut", action="store_true", help="lignes sœurs seules")
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

    ajouts, bilan = calculer(dm, args.brut, not args.sans_brut)
    for libelle, n in sorted(bilan.items()):
        print(f"  {libelle} : {n}")
    print(f"→ {len(ajouts)} crédit(s) reçoivent au moins une confirmation")
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
    backup = sauvegarde.create_backup("before_rattraper_confirmations_credits")
    if not backup:
        print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
        return 1
    print(f"💾 Backup : {backup}")
    print(f"✅ {dm.ajouter_confirmations_credits(ajouts)} crédit(s) confirmé(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
