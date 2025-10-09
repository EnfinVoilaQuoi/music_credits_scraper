"""
Script de détection et analyse des doublons
"""
import sys
import io
from pathlib import Path
import sqlite3

# Fix encodage Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Ajouter le répertoire parent au path
sys.path.insert(0, str(Path(__file__).parent.parent))


def analyze_specific_duplicate(title):
    """Analyse un doublon spécifique"""
    conn = sqlite3.connect('data/music_credits.db')
    cursor = conn.cursor()

    print(f"\n{'='*60}")
    print(f"   ANALYSE DU DOUBLON: {title}")
    print(f"{'='*60}\n")

    cursor.execute("""
        SELECT id, title, album, release_date, genius_id, bpm, duration,
               musical_key, spotify_id, lyrics
        FROM tracks
        WHERE LOWER(title) = LOWER(?)
        ORDER BY title
    """, (title,))

    rows = cursor.fetchall()

    if len(rows) < 2:
        print(f"Aucun doublon trouvé pour '{title}'")
        conn.close()
        return

    print(f"Nombre de doublons: {len(rows)}\n")

    for i, row in enumerate(rows, 1):
        print(f"Version {i}:")
        print(f"  ID: {row[0]}")
        print(f"  Titre: '{row[1]}'")
        print(f"  Album: {row[2] or 'N/A'}")
        print(f"  Date: {row[3] or 'N/A'}")
        print(f"  Genius ID: {row[4] or 'N/A'}")
        print(f"  BPM: {row[5] or 'N/A'}")
        print(f"  Duration: {row[6] or 'N/A'}")
        print(f"  Musical Key: {row[7] or 'N/A'}")
        print(f"  Spotify ID: {row[8] or 'N/A'}")
        print(f"  Has Lyrics: {'Oui' if row[9] else 'Non'}")

        # Compter les crédits
        cursor.execute("SELECT COUNT(*) FROM credits WHERE track_id = ?", (row[0],))
        credits_count = cursor.fetchone()[0]
        print(f"  Credits: {credits_count}")
        print()

    # Recommandation
    print("RECOMMANDATION:")
    # Trouver la version la plus complète
    scores = []
    for row in rows:
        score = 0
        if row[2]: score += 1  # album
        if row[4]: score += 2  # genius_id (important)
        if row[5]: score += 1  # bpm
        if row[6]: score += 1  # duration
        if row[7]: score += 1  # musical_key
        if row[8]: score += 1  # spotify_id
        if row[9]: score += 1  # lyrics

        # Credits
        cursor.execute("SELECT COUNT(*) FROM credits WHERE track_id = ?", (row[0],))
        credits_count = cursor.fetchone()[0]
        if credits_count > 0: score += 2

        scores.append((row[0], score))

    best = max(scores, key=lambda x: x[1])
    worst = min(scores, key=lambda x: x[1])

    print(f"  Garder: ID {best[0]} (score: {best[1]})")
    print(f"  Supprimer: ID {worst[0]} (score: {worst[1]})")

    conn.close()


def find_all_duplicates():
    """Trouve tous les doublons dans la base"""
    conn = sqlite3.connect('data/music_credits.db')
    cursor = conn.cursor()

    print(f"\n{'='*60}")
    print(f"   DETECTION DE TOUS LES DOUBLONS")
    print(f"{'='*60}\n")

    # Trouver les doublons par titre (insensible à la casse)
    cursor.execute("""
        SELECT LOWER(title) as title_lower, COUNT(*) as count
        FROM tracks
        GROUP BY title_lower
        HAVING count > 1
        ORDER BY count DESC, title_lower
    """)

    duplicates = cursor.fetchall()

    if not duplicates:
        print("Aucun doublon trouvé!")
        conn.close()
        return

    print(f"Nombre de doublons: {len(duplicates)}\n")

    for title_lower, count in duplicates:
        # Récupérer les versions exactes
        cursor.execute("""
            SELECT id, title, album, bpm, duration
            FROM tracks
            WHERE LOWER(title) = ?
            ORDER BY id
        """, (title_lower,))

        versions = cursor.fetchall()

        print(f"'{versions[0][1]}' ({count} versions):")
        for version in versions:
            info = f"  ID {version[0]}"
            if version[2]:
                info += f" | Album: {version[2]}"
            if version[3]:
                info += f" | BPM: {version[3]}"
            if version[4]:
                info += f" | Duree: {version[4]}s"
            print(info)
        print()

    conn.close()

    return duplicates


def main():
    """Programme principal"""
    import sys

    if len(sys.argv) > 1:
        # Analyse d'un doublon spécifique
        title = " ".join(sys.argv[1:])
        analyze_specific_duplicate(title)
    else:
        # Liste tous les doublons
        find_all_duplicates()

        print("\nPour analyser un doublon spécifique:")
        print("  python scripts/check_duplicates.py BOSS")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrompu par l'utilisateur")
    except Exception as e:
        print(f"\nErreur: {e}")
        import traceback
        traceback.print_exc()
