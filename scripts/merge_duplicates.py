"""
Script de fusion et suppression des doublons
ATTENTION : Crée un backup avant toute modification
"""

import sqlite3
import sys

# Fix encodage Windows
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


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
        "SELECT t.id, t.title, t.album, t.release_date, t.spotify_id, t.bpm, a.name "
        "FROM tracks t JOIN artists a ON a.id = t.artist_id"
    )
    params = ()
    if artist_name:
        sql += " WHERE a.name = ?"
        params = (artist_name,)
    rows = cur.execute(sql, params).fetchall()
    conn.close()

    groups = {}
    for tid, title, album, rdate, sid, bpm, artist in rows:
        key = (artist, normalize_title(title or ""))
        groups.setdefault(key, []).append((tid, title, album, rdate, sid, bpm))

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
        for tid, title, album, rdate, sid, bpm in items:
            d = str(rdate)[:10] if rdate else "—"
            print(
                f"    #{tid:>4}  {title!r}  album={album or '—'}  date={d}  "
                f"sid={'oui' if sid else 'non'}  bpm={bpm or '—'}"
            )
        ids = [str(i[0]) for i in items]
        print(
            f"    → fusion (garde le 1er) : python scripts/merge_duplicates.py "
            f"--merge {ids[0]} {ids[1]} --execute\n"
        )


def merge_duplicate_tracks(keep_id, delete_id, dry_run=True):
    """
    Fusionne deux tracks en un seul

    Args:
        keep_id: ID du track à conserver
        delete_id: ID du track à supprimer
        dry_run: Si True, simule sans modifier la base
    """
    conn = sqlite3.connect("data/music_credits.db")
    cursor = conn.cursor()

    print(f"\n{'='*60}")
    print("   FUSION DE DOUBLONS")
    print(f"{'='*60}\n")

    # Récupérer les infos des deux tracks
    cursor.execute("SELECT * FROM tracks WHERE id = ?", (keep_id,))
    keep_track = cursor.fetchone()

    cursor.execute("SELECT * FROM tracks WHERE id = ?", (delete_id,))
    delete_track = cursor.fetchone()

    if not keep_track or not delete_track:
        print("Erreur: Un des IDs n'existe pas")
        conn.close()
        return False

    print(f"Track a GARDER (ID {keep_id}):")
    print(f"  Titre: {keep_track[1]}")
    print(f"  Album: {keep_track[3] or 'N/A'}")

    cursor.execute("SELECT COUNT(*) FROM credits WHERE track_id = ?", (keep_id,))
    keep_credits = cursor.fetchone()[0]
    print(f"  Credits: {keep_credits}")

    print(f"\nTrack a SUPPRIMER (ID {delete_id}):")
    print(f"  Titre: {delete_track[1]}")
    print(f"  Album: {delete_track[3] or 'N/A'}")

    cursor.execute("SELECT COUNT(*) FROM credits WHERE track_id = ?", (delete_id,))
    delete_credits = cursor.fetchone()[0]
    print(f"  Credits: {delete_credits}")

    if dry_run:
        print("\n[DRY RUN] Aucune modification effectuee")
        conn.close()
        return True

    print("\nFusion en cours...")

    try:
        # 1. Transférer les crédits du track à supprimer vers celui à garder
        if delete_credits > 0:
            cursor.execute(
                """
                UPDATE credits
                SET track_id = ?
                WHERE track_id = ?
            """,
                (keep_id, delete_id),
            )
            print(f"  Credits transferes: {delete_credits}")

        # 2. Transférer les erreurs de scraping
        cursor.execute(
            """
            UPDATE scraping_errors
            SET track_id = ?
            WHERE track_id = ?
        """,
            (keep_id, delete_id),
        )

        # 3. Supprimer le track en doublon
        cursor.execute("DELETE FROM tracks WHERE id = ?", (delete_id,))

        conn.commit()
        print(f"\nSUCCES: Track {delete_id} fusionne dans {keep_id} et supprime")

        conn.close()
        return True

    except Exception as e:
        conn.rollback()
        print(f"\nERREUR: {e}")
        conn.close()
        return False


def delete_duplicate_track(track_id, dry_run=True):
    """
    Supprime un track en doublon (sans fusion)

    Args:
        track_id: ID du track à supprimer
        dry_run: Si True, simule sans modifier la base
    """
    conn = sqlite3.connect("data/music_credits.db")
    cursor = conn.cursor()

    print(f"\n{'='*60}")
    print("   SUPPRESSION DE DOUBLON")
    print(f"{'='*60}\n")

    # Récupérer les infos du track
    cursor.execute("SELECT * FROM tracks WHERE id = ?", (track_id,))
    track = cursor.fetchone()

    if not track:
        print(f"Erreur: Track ID {track_id} n'existe pas")
        conn.close()
        return False

    print(f"Track a SUPPRIMER (ID {track_id}):")
    print(f"  Titre: {track[1]}")
    print(f"  Album: {track[3] or 'N/A'}")

    cursor.execute("SELECT COUNT(*) FROM credits WHERE track_id = ?", (track_id,))
    credits_count = cursor.fetchone()[0]
    print(f"  Credits: {credits_count}")

    if dry_run:
        print("\n[DRY RUN] Aucune modification effectuee")
        conn.close()
        return True

    print("\nSuppression en cours...")

    try:
        # 1. Supprimer les crédits associés
        cursor.execute("DELETE FROM credits WHERE track_id = ?", (track_id,))
        print(f"  Credits supprimes: {credits_count}")

        # 2. Supprimer les erreurs de scraping
        cursor.execute("DELETE FROM scraping_errors WHERE track_id = ?", (track_id,))

        # 3. Supprimer le track
        cursor.execute("DELETE FROM tracks WHERE id = ?", (track_id,))

        conn.commit()
        print(f"\nSUCCES: Track {track_id} supprime")

        conn.close()
        return True

    except Exception as e:
        conn.rollback()
        print(f"\nERREUR: {e}")
        conn.close()
        return False


def auto_clean_duplicates(dry_run=True):
    """
    Nettoie automatiquement tous les doublons en gardant le plus complet

    Args:
        dry_run: Si True, simule sans modifier la base
    """
    conn = sqlite3.connect("data/music_credits.db")
    cursor = conn.cursor()

    print(f"\n{'='*60}")
    print("   NETTOYAGE AUTOMATIQUE DES DOUBLONS")
    print(f"{'='*60}\n")

    # Trouver tous les doublons
    cursor.execute("""
        SELECT LOWER(title) as title_lower, COUNT(*) as count
        FROM tracks
        GROUP BY title_lower
        HAVING count > 1
        ORDER BY count DESC, title_lower
    """)

    duplicates = cursor.fetchall()

    if not duplicates:
        print("Aucun doublon trouve!")
        conn.close()
        return

    print(f"Nombre de groupes de doublons: {len(duplicates)}\n")

    actions = []

    for title_lower, count in duplicates:
        # Récupérer toutes les versions
        cursor.execute(
            """
            SELECT id, title, album, genius_id, bpm, duration,
                   musical_key, spotify_id, lyrics
            FROM tracks
            WHERE LOWER(title) = ?
            ORDER BY id
        """,
            (title_lower,),
        )

        versions = cursor.fetchall()

        # Calculer les scores
        scores = []
        for version in versions:
            score = 0
            if version[2]:
                score += 1  # album
            if version[3]:
                score += 2  # genius_id
            if version[4]:
                score += 1  # bpm
            if version[5]:
                score += 1  # duration
            if version[6]:
                score += 1  # musical_key
            if version[7]:
                score += 1  # spotify_id
            if version[8]:
                score += 1  # lyrics

            # Credits
            cursor.execute("SELECT COUNT(*) FROM credits WHERE track_id = ?", (version[0],))
            credits_count = cursor.fetchone()[0]
            if credits_count > 0:
                score += 2

            scores.append((version[0], version[1], score))

        # Trouver la meilleure version
        best = max(scores, key=lambda x: x[2])

        # Marquer les autres pour suppression
        for track_id, track_title, _score in scores:
            if track_id != best[0]:
                actions.append(
                    {
                        "keep_id": best[0],
                        "keep_title": best[1],
                        "delete_id": track_id,
                        "delete_title": track_title,
                    }
                )

        print(f"'{best[1]}' ({count} versions):")
        print(f"  Garder: ID {best[0]} (score: {best[2]})")
        for track_id, _track_title, score in scores:
            if track_id != best[0]:
                print(f"  Supprimer: ID {track_id} (score: {score})")
        print()

    conn.close()

    if dry_run:
        print(f"\n[DRY RUN] {len(actions)} doublons seraient supprimes")
        print("\nPour executer reellement:")
        print("  python scripts/merge_duplicates.py --auto --execute")
        return

    # Créer un backup avant modification
    print("\nCreation d'un backup...")
    backup_manager = get_backup_manager()
    backup_path = backup_manager.create_backup("before_merge_duplicates")
    if backup_path:
        print(f"Backup cree: {backup_path.name}")
    else:
        print("ATTENTION: Impossible de creer un backup!")
        confirm = input("Continuer quand meme ? (oui/non): ").strip().lower()
        if confirm not in ["oui", "o", "yes", "y"]:
            print("Annule")
            return

    # Exécuter les suppressions
    print(f"\nSuppression de {len(actions)} doublons...")
    success = 0
    for action in actions:
        if delete_duplicate_track(action["delete_id"], dry_run=False):
            success += 1

    print(f"\nTERMINE: {success}/{len(actions)} doublons supprimes")


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
