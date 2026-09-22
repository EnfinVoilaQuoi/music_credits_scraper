"""Reclasse les crédits rangés en `Other` grâce à leur libellé d'origine.

Quand un parseur ne sait pas traduire un libellé, le crédit est stocké avec
`role='Other'` et le LIBELLÉ brut dans `role_detail`. Les tables d'alias, elles,
GROSSISSENT — si bien qu'un libellé aujourd'hui connu reste en `Other` sur tous
les morceaux scrapés avant. Mesuré le 2026-09-22 : **1 847 crédits** dans ce cas,
dont 194 `Writers` de Genius (que la table traduit depuis l'extraction de
`credit_roles.py`) et 407 `Programmer`. C'est ce qui faisait dire à la
validation « pas d'auteur » sur 62 morceaux qui en avaient un.

Tout est déjà en base : aucun réseau. Le script ne duplique AUCUNE
correspondance — il rejoue le mapper de la SOURCE de chaque ligne
(`DiscogsClient._map_discogs_role_to_enum` pour Discogs, `credit_roles.map_role`
pour Genius et les autres), qui restent les seules sources de vérité. Ajouter un
alias là-bas suffit pour que ce script le prenne en compte.

Il ne touche QUE la colonne `role` : `role_detail` garde le libellé Discogs.
C'est voulu — la provenance reste lisible dans la fiche morceau, et l'unicité
`(track_id, name, role, role_detail)` empêche toute collision avec une ligne
Genius (dont `role_detail` est NULL la plupart du temps). Contrepartie assumée :
une personne créditée par les deux sources au même rôle apparaît deux fois, avec
un émoji de source distinct — c'est déjà le comportement actuel.

Usage :
    python scripts/reclass_credit_roles.py             # dry-run (défaut)
    python scripts/reclass_credit_roles.py --apply     # backup + écriture
    python scripts/reclass_credit_roles.py --revert    # retour en Other
    python scripts/reclass_credit_roles.py --source genius
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
from src.utils.credit_roles import map_role
from src.utils.database_backup import get_backup_manager

DB_PATH = "data/music_credits.db"

# `role_detail` porte soit le LIBELLÉ du rôle, soit la liste des PISTES fournie
# par Discogs (« 16 », « 3, 5, 7, 14 ») quand elle existe — les deux se disputent
# la même colonne (cf. WIP). Une valeur purement numérique n'est donc pas un
# libellé de rôle et ne doit pas être reclassée.
_PISTES_RE = re.compile(r"^[\d\s,&at to\.\-]+$")


def _mapper_discogs():
    """Le mapper réel, sans passer par `__init__` (qui monte un client réseau)."""
    return DiscogsClient.__new__(DiscogsClient)


def cible_de(source: str | None, libelle: str) -> CreditRole:
    """Le rôle que la source aurait donné à ce libellé AUJOURD'HUI.

    Discogs a sa propre table (ses libellés sont ceux de son formulaire :
    « Written-By », « Music By »…) ; tout le reste — Genius, YouTube, Spotify —
    passe par `credit_roles.map_role`. Envoyer un libellé Genius dans la table
    Discogs, ou l'inverse, fabriquerait des verdicts que la source n'a jamais
    rendus : c'est précisément ce que ce script s'interdit.
    """
    if (source or "").lower() == "discogs":
        return _mapper_discogs()._map_discogs_role_to_enum(libelle)
    return map_role(libelle)


def _est_un_libelle(role_detail: str | None) -> bool:
    return bool(role_detail) and not _PISTES_RE.match(role_detail)


def analyser(conn, source: str | None = None) -> tuple[list, list, list, list]:
    """(reclassements, doublons, collisions, en_attente) — sans rien écrire.

    Un « reclassement » = (id, nom, libellé, rôle cible, source).

    Deux sorts différents quand la personne est DÉJÀ créditée à ce rôle sur ce
    morceau, et c'est la SOURCE qui les sépare :

    · même source ⇒ **doublon** (24 mesurés) : Genius a nommé Josman deux fois,
      une fois sous « Writer » et une fois sous le libellé « Writers » resté en
      `Other`. Reclasser la seconde afficherait la personne deux fois au même
      rôle — la ligne est SUPPRIMÉE, elle n'apporte rien ;
    · source différente ⇒ **accord** entre sources (8 mesurés) : Genius ET
      Discogs nomment Skread comme programmeur. Les deux lignes restent en base
      — chacune porte SA provenance — et la fiche morceau les affiche sur une
      seule ligne, « 🎤💿 Skread » (`helpers.lignes_de_credits`). Rien à
      arbitrer : 136 accords du même genre dormaient déjà en base.
    """
    cur = conn.cursor()

    # Index des personnes déjà créditées, par (morceau, rôle), en clé d'identité
    # normalisée — deux graphies du même nom comptent pour une seule personne.
    deja = collections.defaultdict(set)
    for tid, name, role, src in cur.execute("SELECT track_id, name, role, source FROM credits"):
        deja[(tid, role)].add((identity_key(name), (src or "").lower()))

    reclassements, doublons, collisions, en_attente = [], [], [], []
    requete = (
        "SELECT id, track_id, name, role_detail, source, tracks FROM credits WHERE role = 'Other'"
    )
    params: tuple = ()
    if source is not None:
        requete += " AND source = ?"
        params = (source,)
    for cid, tid, name, role_detail, src, pistes in cur.execute(requete, params):
        if not _est_un_libelle(role_detail):
            continue
        cible = cible_de(src, role_detail)
        if cible == CreditRole.OTHER:
            continue  # libellé volontairement non mappé, ou inconnu
        if (pistes or "").strip():
            # Discogs a nommé des PISTES, et rien ne dit que celle-ci en fait
            # partie : le crédit a été recopié sur tout le disque (défaut du
            # 2026-09-22, 1 460 attributions en trop). Promouvoir la ligne d'un
            # `Other` discret vers un rôle visible — un AUTEUR, qui plus est,
            # compté par la validation — amplifierait l'erreur. La position
            # Discogs du morceau n'étant stockée nulle part, seul un re-run
            # Discogs peut trancher : on attend.
            en_attente.append((cid, name, role_detail, cible.value, src))
            continue
        sources_deja = {s for (n, s) in deja[(tid, cible.value)] if n == identity_key(name)}
        if (src or "").lower() in sources_deja:
            doublons.append((cid, name, role_detail, cible.value, src))
            continue
        reclassements.append((cid, name, role_detail, cible.value, src))
        if sources_deja:
            collisions.append((name, role_detail, cible.value))
    return reclassements, doublons, collisions, en_attente


def rapport(reclassements, doublons, collisions, en_attente, conn):
    par_libelle = collections.Counter(
        (src, libelle, cible) for _, _, libelle, cible, src in reclassements
    )
    print(f"\n{len(reclassements)} crédit(s) à reclasser :\n")
    for (src, libelle, cible), n in par_libelle.most_common():
        print(f"   {n:4}  [{src}] {libelle!r:32} → {cible}")

    restants = collections.Counter(
        (src, rd)
        for rd, src in conn.execute("SELECT role_detail, source FROM credits WHERE role='Other'")
        if _est_un_libelle(rd) and cible_de(src, rd) == CreditRole.OTHER
    )
    if restants:
        print(f"\nLaissés en Other ({sum(restants.values())} crédits) :\n")
        for (src, libelle), n in restants.most_common(40):
            print(f"   {n:4}  [{src}] {libelle!r}")

    if en_attente:
        par_libelle = collections.Counter(lib for _, _, lib, _, _ in en_attente)
        print(f"\n⏸️  {len(en_attente)} crédit(s) EN ATTENTE — Discogs les rattache à des PISTES")
        print("    précises et rien ne dit que ce morceau en fait partie : le crédit a été")
        print("    recopié sur tout le disque. Les promouvoir amplifierait l'erreur.")
        print("    Remède : re-run Discogs sur ces morceaux, puis relancer ce script.\n")
        for libelle, n in par_libelle.most_common(10):
            print(f"   {n:4}  {libelle!r}")

    if doublons:
        print(f"\n🗑️  {len(doublons)} ligne(s) SUPPRIMÉE(S) — la même source crédite déjà")
        print("    la personne à ce rôle : reclasser l'afficherait deux fois.\n")
        for _, name, libelle, cible, src in doublons[:20]:
            print(f"   [{src}] {name!r} déjà {cible} (arrive via {libelle!r})")

    if collisions:
        print(f"\n🤝 {len(collisions)} accord(s) entre sources — l'AUTRE source crédite déjà")
        print("    la personne à ce rôle. Les deux lignes restent (chacune sa provenance) ;")
        print("    la fiche morceau les affiche sur une seule, « 🎤💿 Nom ».\n")
        for name, libelle, cible in collisions:
            print(f"   {name!r} déjà crédité {cible} (arrive via {libelle!r})")

    # `songwriter` → WRITER alimente get_writers(), qui conditionne le badge
    # « ✅ Crédits complets » : le signaler pour que le changement ne surprenne pas.
    n_writer = sum(1 for _, _, _, cible, _ in reclassements if cible == CreditRole.WRITER.value)
    if n_writer:
        print(
            f"\nℹ️  {n_writer} crédit(s) deviennent des auteurs : quelques morceaux peuvent"
            "\n    passer de « ⚠️ Crédits partiels » à « ✅ Crédits complets »."
        )


def appliquer(reclassements, doublons=()) -> tuple[int, int]:
    """DÉLÈGUE l'écriture au repository (`tests/test_scripts_deleguent`).

    Le script décide — c'est lui qui rejoue les mappers —, le repository écrit,
    en une seule transaction.
    """
    from src.utils.data_manager import DataManager

    roles = {cid: cible for cid, _, _, cible, _ in reclassements}
    a_retirer = [cid for cid, _, _, _, _ in doublons]
    return DataManager().reclasser_credits(roles, a_retirer)


def revert(conn, source: str | None = None) -> int:
    """Repasse en Other tout crédit dont le `role_detail` est un libellé que la
    table de SA source sait mapper — l'inverse exact de `appliquer`."""
    cur = conn.cursor()
    requete = "SELECT id, role_detail, source FROM credits WHERE role <> 'Other'"
    params: tuple = ()
    if source is not None:
        requete += " AND source = ?"
        params = (source,)
    ids = [
        cid
        for cid, rd, src in cur.execute(requete, params)
        if _est_un_libelle(rd) and cible_de(src, rd) != CreditRole.OTHER
    ]
    from src.utils.data_manager import DataManager

    changes, _ = DataManager().reclasser_credits(dict.fromkeys(ids, "Other"))
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="écrit réellement (backup avant)")
    parser.add_argument("--revert", action="store_true", help="repasse les reclassés en Other")
    parser.add_argument("--source", help="ne traiter qu'une source (genius, discogs…)")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    try:
        if args.revert:
            backup = get_backup_manager().create_backup("before_reclass_revert")
            print(f"💾 Backup : {backup}")
            print(f"↩️  {revert(conn, args.source)} crédit(s) repassés en Other.")
            return 0

        reclassements, doublons, collisions, en_attente = analyser(conn, args.source)
        rapport(reclassements, doublons, collisions, en_attente, conn)

        if not args.apply:
            print("\nℹ️  DRY-RUN : rien n'a été écrit. Relance avec --apply.")
            return 0
        if not reclassements and not doublons:
            print("\nRien à faire.")
            return 0

        backup = get_backup_manager().create_backup("before_reclass_credit_roles")
        if not backup:
            print("❌ Backup impossible — abandon (règle projet : jamais d'écriture sans backup).")
            return 1
        print(f"\n💾 Backup : {backup}")
        n_recl, n_doub = appliquer(reclassements, doublons)
        print(f"✅ {n_recl} crédit(s) reclassés, {n_doub} doublon(s) supprimé(s).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
