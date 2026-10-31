"""Historique d'une fiche — à lire AVANT toute proposition sur elle (2026-10-31).

    python scripts/historique_fiche.py 8215
    python scripts/historique_fiche.py "Écorchée Vive" --artiste "Diam's"
    python scripts/historique_fiche.py 8215 --panneau     # + ses cas actifs du panneau

Lecture seule, sans réseau. Quatre blocs : la FAMILLE (fiche, sœurs, même socle
chez l'artiste, fiches fusionnées dedans), les DÉCISIONS sur cette famille
(refus, acceptations, corrections, Kworb, verdicts du panneau, corrections
automatiques), ce qu'en disent les NOTES (CORRECTIFS, JOURNAL et ses archives,
WIP, rapports — dossier parent de `DECISIONS_DIR`), et l'ÉTAT actuel de la
fiche. Module : `src/services/historique.py`.
"""

import argparse
import sys

from sqlalchemy import text

from src.services import historique
from src.utils.data_manager import DataManager
from src.utils.logger import configurer_journaux
from src.utils.lrc_garde import fin_du_lrc
from src.utils.title_matching import normalize_title


def _trouver(conn, cible: str, artiste: str | None) -> list[int]:
    if cible.isdigit():
        return [int(cible)]
    lignes = conn.execute(
        text("SELECT t.id, t.title, a.name FROM tracks t JOIN artists a ON a.id = t.artist_id")
    ).all()
    voulu = normalize_title(cible)
    return [
        tid
        for tid, titre, nom in lignes
        if normalize_title(titre) == voulu
        and (artiste is None or normalize_title(nom) == normalize_title(artiste))
    ]


def _etat(conn, tid: int) -> list[str]:
    t = (
        conn.execute(
            text(
                "SELECT t.title, a.name AS artiste, t.album, t.duration, t.genius_url, "
                "t.spotify_id, t.deezer_id, t.youtube_url, t.youtube_url_source, "
                "t.lyrics_synced, t.lyrics_synced_source FROM tracks t "
                "JOIN artists a ON a.id = t.artist_id WHERE t.id = :t"
            ),
            {"t": tid},
        )
        .mappings()
        .first()
    )
    durees = conn.execute(
        text(
            "SELECT field, source, value FROM observations WHERE track_id = :t "
            "AND field IN ('duration', 'lrc_duree_audio') ORDER BY field, source"
        ),
        {"t": tid},
    ).all()
    fin = fin_du_lrc(t["lyrics_synced"]) if t["lyrics_synced"] else None
    return [
        f"#{tid} « {t['title']} » — {t['artiste']} — {t['album'] or 'sans album'}",
        f"   page : {t['genius_url'] or '—'} ; Spotify {t['spotify_id'] or '—'} ; "
        f"Deezer {t['deezer_id'] or '—'}",
        f"   durée : {t['duration']} s ; " + ", ".join(f"{f} {s} {v}" for f, s, v in durees),
        f"   LRC : {t['lyrics_synced_source'] or '—'}" + (f", jusqu'à {fin} s" if fin else ""),
        f"   lien : {t['youtube_url'] or '—'} ({t['youtube_url_source'] or '—'})",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fiche", help="id de la fiche, ou son titre")
    parser.add_argument("--artiste", help="avec un titre : l'artiste")
    parser.add_argument("--panneau", action="store_true", help="ses cas actifs du panneau")
    args = parser.parse_args()
    configurer_journaux()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from src.config import DECISIONS_DIR

    dm = DataManager()
    with dm.engine.connect() as conn:
        ids = _trouver(conn, args.fiche, args.artiste)
        if not ids:
            print(f"Aucune fiche « {args.fiche} ».")
            return 1
        for tid in ids:
            membres = historique.famille(conn, tid)
            print("══ ÉTAT")
            print("\n".join(_etat(conn, tid)))
            print("══ FAMILLE")
            for m in membres:
                print(f"   {m.lien:9} #{m.track_id or '—'} « {m.titre} » ({m.album or '—'})")
            print("══ DÉCISIONS")
            for d in historique.decisions(conn, membres) or []:
                print(f"   {d.ligne()}")
            print("══ NOTES")
            notes = DECISIONS_DIR.parent if DECISIONS_DIR else None
            for ligne in historique.mentions(notes, membres, tid):
                print(f"   {ligne}")
    if args.panneau:
        from src.services import revue

        for tid in ids:
            artiste_id = (
                dm.engine.connect()
                .execute(text("SELECT artist_id FROM tracks WHERE id = :t"), {"t": tid})
                .scalar()
            )
            artiste = next(a for a in dm.get_all_artists() if a.id == artiste_id)
            artiste.tracks = dm.discographie_reunie(artiste)
            print("══ PANNEAU")
            for c in revue.analyser(dm, artiste).actifs:
                if c.track_id == tid:
                    print(f"   {c.detecteur} : {c.motif}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
