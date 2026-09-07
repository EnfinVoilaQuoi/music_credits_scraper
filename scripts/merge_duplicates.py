"""
Script de fusion et suppression des doublons
ATTENTION : Crée un backup avant toute modification
"""

import sqlite3
import sys

# Fix encodage Windows
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


from sqlalchemy import text

from src.utils.database_backup import get_backup_manager
from src.utils.title_matching import normalize_title


def find_normalized_duplicates(artist_name=None):
    """Liste les doublons par titre NORMALISÉ (≠ LOWER exact) : attrape les
    variantes de ponctuation/casse (« My Love (Acoustic) » ≡ « My love [acoustic] »)
    que l'auto-clean exact rate. NE FUSIONNE RIEN — affiche les groupes + les
    commandes --merge à lancer (tu choisis keep/delete).
    """
    conn = sqlite3.connect("data/music_credits.db")
    cur = conn.cursor()

    sql = (
        "SELECT t.id, t.title, t.album, t.release_date, t.spotify_id, a.name "
        "FROM tracks t JOIN artists a ON a.id = t.artist_id"
    )
    params = ()
    if artist_name:
        sql += " WHERE a.name = ?"
        params = (artist_name,)
    rows = cur.execute(sql, params).fetchall()
    conn.close()

    groups = {}
    for tid, title, album, rdate, sid, artist in rows:
        key = (artist, normalize_title(title or ""))
        groups.setdefault(key, []).append((tid, title, album, rdate, sid))

    dups = {k: v for k, v in groups.items() if len(v) > 1}
    if not dups:
        print("Aucun doublon (titre normalisé) trouvé.")
        return

    print(f"\n{'='*60}\n   DOUBLONS PAR TITRE NORMALISÉ ({len(dups)} groupe(s))\n{'='*60}")
    print(
        "⚠️ Vérifie : ce sont peut-être des VERSIONS distinctes (Acoustic/Remix/Live)\n"
        "   → dans ce cas NE PAS fusionner. Intro/Outro/Interlude = souvent le même.\n"
    )
    for (artist, norm), items in sorted(dups.items(), key=lambda x: -len(x[1])):
        print(f"[{artist}]  « {norm} »  ({len(items)} entrées)")
        for tid, title, album, rdate, sid in items:
            d = str(rdate)[:10] if rdate else "—"
            print(
                f"    #{tid:>4}  {title!r}  album={album or '—'}  date={d}  "
                f"sid={'oui' if sid else 'non'}"
            )
        ids = [str(i[0]) for i in items]
        print(
            f"    → fusion (garde le 1er) : python scripts/merge_duplicates.py "
            f"--merge {ids[0]} {ids[1]} --execute\n"
        )


def merge_duplicate_tracks(keep_id, delete_id, dry_run=True):
    """Fusionne `delete_id` dans `keep_id` — DÉLÈGUE à `DataManager.merge_tracks`.

    Ce script en portait sa PROPRE copie, et elle avait divergé (constaté le
    2026-09-07). Elle transférait les crédits sans les dédupliquer — donc violait
    `UNIQUE(track_id, name, role, role_detail)` dès que les deux fiches partageaient
    un crédit —, ignorait `track_videos` (e20), ce qui laissait des lignes orphelines
    pointant sur un morceau supprimé, et ne ré-arbitrait pas les streams. C'est le
    piège des jumeaux du 2026-09-05, en trois exemplaires cette fois. Une seule
    implémentation, testée, désormais.
    """
    from src.utils.data_manager import DataManager

    dm = DataManager()
    with dm.engine.connect() as conn:
        fiches = {
            r["id"]: r
            for r in conn.execute(
                text("SELECT id, title, album FROM tracks WHERE id IN (:k, :d)"),
                {"k": keep_id, "d": delete_id},
            ).mappings()
        }
        if keep_id not in fiches or delete_id not in fiches:
            print("Erreur: un des IDs n'existe pas")
            return False
        compte = dict(
            conn.execute(
                text(
                    "SELECT track_id, COUNT(*) FROM credits "
                    "WHERE track_id IN (:k, :d) GROUP BY track_id"
                ),
                {"k": keep_id, "d": delete_id},
            ).all()
        )

    print()
    print("=" * 60)
    print("   FUSION DE DOUBLONS")
    print("=" * 60)
    for role, tid in (("GARDER", keep_id), ("SUPPRIMER", delete_id)):
        fiche = fiches[tid]
        print()
        print(f"Track a {role} (ID {tid}):")
        print(f"  Titre: {fiche['title']!r}")
        print(f"  Album: {fiche['album'] or 'N/A'}")
        print(f"  Credits: {compte.get(tid, 0)}")

    if dry_run:
        print()
        print("[DRY RUN] Aucune modification effectuee")
        return True

    # Règle projet : backup AVANT toute opération destructive. `merge_tracks` ne le
    # fait pas lui-même, il le laisse explicitement à son appelant.
    sauvegarde = get_backup_manager().create_backup(f"before_merge_{delete_id}_into_{keep_id}")
    print()
    print(f"Backup: {sauvegarde}")

    if not dm.merge_tracks(keep_id, delete_id):
        print("ERREUR: fusion echouee (voir les logs)")
        return False
    print(f"SUCCES: Track {delete_id} fusionne dans {keep_id} et supprime")
    return True


def delete_duplicate_track(track_id, dry_run=True):
    """Supprime un morceau — DÉLÈGUE à `DataManager.delete_track`.

    Même raison que la fusion : la copie locale ne nettoyait pas `track_videos`
    (e20) et laissait donc des lignes orphelines.
    """
    from src.utils.data_manager import DataManager

    dm = DataManager()
    with dm.engine.connect() as conn:
        fiche = (
            conn.execute(text("SELECT id, title, album FROM tracks WHERE id = :i"), {"i": track_id})
            .mappings()
            .first()
        )
        if not fiche:
            print(f"Erreur: Track ID {track_id} n'existe pas")
            return False
        n_credits = conn.execute(
            text("SELECT COUNT(*) FROM credits WHERE track_id = :i"), {"i": track_id}
        ).scalar()

    print()
    print("=" * 60)
    print("   SUPPRESSION DE DOUBLON")
    print("=" * 60)
    print(f"Track a SUPPRIMER (ID {track_id}):")
    print(f"  Titre: {fiche['title']!r}")
    print(f"  Album: {fiche['album'] or 'N/A'}")
    print(f"  Credits: {n_credits}")

    if dry_run:
        print("[DRY RUN] Aucune modification effectuee")
        return True

    sauvegarde = get_backup_manager().create_backup(f"before_delete_{track_id}")
    print(f"Backup: {sauvegarde}")
    if not dm.delete_track(track_id):
        print("ERREUR: suppression echouee (voir les logs)")
        return False
    print(f"SUCCES: Track {track_id} supprime")
    return True


def auto_clean_duplicates(dry_run=True):
    """DÉSARMÉ le 2026-09-07 : ne supprime plus rien, RAPPORTE.

    Mesuré sur la base réelle avant retrait : cette fonction groupait par
    `LOWER(title)` **sans l'artiste**, et son mode `--execute` aurait SUPPRIMÉ
    60 morceaux. 47 de ses 51 groupes mélangeaient deux artistes, pour deux raisons
    distinctes et toutes deux rédhibitoires :

      · 36 groupes sont des morceaux VRAIMENT différents au titre commun — « ADN »
        de Flynt (*Pejmaxx*) et « ADN » de Josman (*DOM PERIGNON CRYING*) n'ont
        rien à voir ;
      · 11 groupes sont le MÊME enregistrement stocké sous chaque artiste, ce qui
        est le fonctionnement NORMAL des featurings — « Albiceleste » est un
        morceau de Jazzy Bazz, présent aussi chez Josman avec `is_featuring=1` et
        `primary_artist_name='Jazzy Bazz'`. En supprimer une ligne ampute la
        discographie d'un artiste d'un featuring qu'il a réellement.

    Elle supprimait de surcroît le « perdant » au lieu de fusionner, alors qu'un
    doublon moins complet porte souvent la seule donnée qui manque à l'autre : la
    fusion Josman du 2026-09-07 a montré une fiche qui détenait à elle seule les
    paroles synchronisées, et l'autre les streams.

    Avec le BON regroupement (artiste + titre normalisé) il ne reste que 4 groupes
    — dont Isha « MEILLEUR »/« Meilleur », que le projet documente comme de vrais
    HOMONYMES. Aucune règle automatique ne peut trancher cela, donc la détection est
    déléguée à `find_normalized_duplicates`, qui groupe correctement et rend les
    commandes `--merge` à lancer après vérification.
    """
    print()
    print("[DESARME] --auto ne supprime plus rien : il rapporte.")
    print("   Il groupait par LOWER(title) SANS l'artiste : 47 de ses 51 groupes")
    print("   melangeaient deux artistes, et --execute aurait supprime 60 morceaux.")
    print("   Et supprimer le perdant perd ce qu'il est seul a porter : on FUSIONNE.")
    print()
    find_normalized_duplicates()
    if not dry_run:
        print()
        print("Aucune suppression automatique : verifie chaque groupe, puis lance")
        print("les commandes --merge ci-dessus une par une.")


def main():
    """Programme principal"""
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python scripts/merge_duplicates.py --find [ARTISTE]")
        print("  python scripts/merge_duplicates.py --check TITRE")
        print("  python scripts/merge_duplicates.py --delete ID [--execute]")
        print("  python scripts/merge_duplicates.py --merge KEEP_ID DELETE_ID [--execute]")
        print("  python scripts/merge_duplicates.py --auto [--execute]")
        print("")
        print("Options:")
        print("  --find [ARTISTE] : Liste les doublons par titre NORMALISÉ (recommandé)")
        print("  --check TITRE    : Analyse un doublon specifique")
        print("  --delete ID      : Supprime un track en doublon")
        print("  --merge K D      : Fusionne DELETE_ID dans KEEP_ID")
        print("  --auto           : Nettoie automatiquement tous les doublons")
        print("  --execute        : Execute reellement (sinon dry-run)")
        return

    mode = sys.argv[1]
    dry_run = "--execute" not in sys.argv

    if dry_run:
        print("\n[MODE DRY-RUN] Simulation sans modification")
        print("Ajoutez --execute pour executer reellement\n")

    if mode == "--find":
        artist = None
        extra = [a for a in sys.argv[2:] if a != "--execute"]
        if extra:
            artist = " ".join(extra)
        find_normalized_duplicates(artist)

    elif mode == "--check":
        if len(sys.argv) < 3:
            print("Erreur: Titre manquant")
            return
        title = " ".join(sys.argv[2:]).replace("--execute", "").strip()
        from check_duplicates import analyze_specific_duplicate

        analyze_specific_duplicate(title)

    elif mode == "--delete":
        if len(sys.argv) < 3:
            print("Erreur: ID manquant")
            return
        track_id = int(sys.argv[2])
        delete_duplicate_track(track_id, dry_run=dry_run)

    elif mode == "--merge":
        if len(sys.argv) < 4:
            print("Erreur: IDs manquants")
            return
        keep_id = int(sys.argv[2])
        delete_id = int(sys.argv[3])
        merge_duplicate_tracks(keep_id, delete_id, dry_run=dry_run)

    elif mode == "--auto":
        auto_clean_duplicates(dry_run=dry_run)

    else:
        print(f"Mode inconnu: {mode}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrompu par l'utilisateur")
    except Exception as e:
        print(f"\nErreur: {e}")
        import traceback

        traceback.print_exc()
