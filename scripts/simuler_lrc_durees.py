"""Simule deux règles LRC contre durée, NON branchées (lecture seule).

1. **Deux témoins contre un** (famille d'audit `lrc_trop_long`) : pour un LRC
   qui court plus de 5 s au-delà de la durée de la fiche, on compte les
   familles de sources SOLIDES (±3 s) qui soutiennent la durée de la fiche,
   puis l'audio du LRC (`lrc_duree_audio` de sa source, plus les durées
   observées qui lui sont égales). Deux contre au plus un : celui qui est seul
   ment. Les sources fragiles (legacy, SongBPM, Discogs, vidéos ordinaires)
   contredisent, elles ne soutiennent jamais.
2. **Durée démentie par un LRC confirmé** (WIP) : un LRC observé que les
   paroles Genius confirment (bigrammes >= 0,6), qui ne déborde pas son propre
   audio, finit plus de 10 s après la durée de la fiche, et cette durée n'a
   que des sources fragiles : la durée céderait. Les VERSIONS d'un autre
   enregistrement sont hors règle (leur durée, même isolée, juge le LRC :
   décision utilisateur 2026-10-20).

Chaque cas est montré avec ses témoins indépendants : durées des vidéos de la
fiche (`track_videos.duration`, clips compris), des éditions, page Genius.

    python scripts/simuler_lrc_durees.py                 # la vraie base, en lecture seule
    python scripts/simuler_lrc_durees.py --base copie.db

Résultat du 2026-10-30 (copie prise après le lot `--lrc-sans-duree-audio`) :
règle 1, 15 LRC trop longs, 0 « le LRC ment », 1 « la durée ment » si une
vidéo ordinaire compte comme voix (« Street Lights » : c'est l'ID Apple qui
est celui du titre de *808s & Heartbreak*), 0 sinon ; règle 2, 5 décisions,
dont 2 fausses au témoin (« Parade » porte le LRC de « Poet Parade »,
« CAN'T HURRY LOVE » la durée de sa propre vidéo), et chacune viderait la
colonne. Aucune n'est branchée (JOURNAL 2026-10-30).
"""

from __future__ import annotations

import argparse
import collections
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from src.config import DATA_DIR

BASE = Path(DATA_DIR) / "music_credits.db"

#: Label de colonne `lyrics_synced_source` → source des observations.
SOURCE_DU_LABEL = {"LRCLIB": "lrclib", "YouTube Music": "ytmusic", "Musixmatch": "musixmatch"}
#: Famille de la durée d'audio d'un LRC (YTM lit l'audio de la vidéo).
FAMILLE_AUDIO = {"lrclib": "lrclib", "ytmusic": "youtube", "musixmatch": "musixmatch"}
#: Sources qui ne soutiennent jamais une durée (`vote_durees.FRAGILES` et les
#: vidéos ordinaires, lues par le titre).
FRAGILES = frozenset({"legacy", "songbpm", "discogs", "youtube_video", "youtube_officiel"})
TOLERANCE_S = 3
#: Règle 2 : marge entre la fin du LRC confirmé et la durée accusée.
MARGE_DEMENTI_S = 10

LRC_MENT, DUREE_MENT, INDECIS, SANS_AUDIO, MANUELLE = (
    "le LRC ment",
    "la durée ment",
    "indécis",
    "sans audio",
    "manuelle",
)


def soutiens(durees: dict, cible: int | None) -> set[str]:
    """PUR. Les familles SOLIDES dont une durée est à ±3 s de `cible`."""
    from src.utils.vote_durees import HORS_VOTE, famille

    if not cible:
        return set()
    return {
        famille(s)
        for s, v in durees.items()
        if v and s not in FRAGILES and s not in HORS_VOTE and s != "manual"
        if abs(int(v) - int(cible)) <= TOLERANCE_S
    }


def deux_contre_un(duree: int, durees: dict, audio: int | None, source_lrc: str | None) -> str:
    """PUR. Règle 1 : qui ment, du LRC ou de la durée de la fiche ?"""
    if "manual" in durees:
        return MANUELLE  # une saisie manuelle tranche seule : l'humain la revoit
    if not audio:
        return SANS_AUDIO
    pour_fiche = soutiens(durees, duree)
    pour_lrc = soutiens(durees, audio) | {FAMILLE_AUDIO.get(source_lrc or "", source_lrc)}
    if len(pour_fiche) >= 2 and len(pour_lrc) <= 1:
        return LRC_MENT
    if len(pour_lrc) >= 2 and len(pour_fiche) <= 1:
        return DUREE_MENT
    return INDECIS


def duree_dementie(fin_lrc: int, duree: int, durees: dict) -> bool:
    """PUR. Règle 2 : la fin d'un LRC confirmé dément-elle cette durée, que
    seules des sources fragiles portent ?"""
    return fin_lrc > duree + MARGE_DEMENTI_S and not soutiens(durees, duree)


def _lire(conn):
    from src.enrichment.observation import Observation
    from src.utils.track_mapper import _clean_duration

    pistes = {
        r["id"]: r
        for r in conn.execute(
            "SELECT t.id, t.title, t.genius_url, t.duration, t.lyrics, t.lyrics_source, "
            "t.lyrics_synced, t.lyrics_synced_source, t.instrumental, a.name AS artiste "
            "FROM tracks t JOIN artists a ON a.id = t.artist_id"
        )
    }
    obs = collections.defaultdict(list)
    for r in conn.execute(
        "SELECT track_id, field, source, value, seen_at FROM observations "
        "WHERE field IN ('duration', 'lyrics_synced', 'lrc_duree_audio')"
    ):
        obs[r[0]].append(Observation(r[1], r[3], r[2], seen_at=r[4]))
    temoins = collections.defaultdict(list)
    for r in conn.execute(
        "SELECT track_id, duration FROM track_videos WHERE duration IS NOT NULL "
        "UNION ALL SELECT track_id, duration FROM track_editions WHERE duration IS NOT NULL"
    ):
        temoins[r[0]].append(int(r[1]))

    def durees_de(tid):
        vues = {o.source: _clean_duration(o.value) for o in obs[tid] if o.field == "duration"}
        return {s: v for s, v in vues.items() if v}

    return pistes, obs, temoins, durees_de


def simuler(conn) -> list[str]:
    from src.utils.concordance_paroles import paroles_de_reference, recouvrement_bigrammes
    from src.utils.lrc_garde import (
        CONFIRME,
        ECART_DUREE_S,
        _autre_enregistrement,
        audios_des_lrc,
        deborde_son_audio,
        fin_du_lrc,
    )
    from src.utils.track_mapper import _clean_duration
    from src.utils.version_heritage import est_instrumental

    pistes, obs, temoins, durees_de = _lire(conn)
    regle1, regle2 = collections.Counter(), collections.Counter()
    lignes1, lignes2 = [], []
    for tid, t in pistes.items():
        d = _clean_duration(t["duration"])
        if not d:
            continue
        durees = durees_de(tid)
        audios = audios_des_lrc(obs[tid])
        tem = temoins.get(tid, [])
        fin = fin_du_lrc(t["lyrics_synced"])
        if fin and fin > d + ECART_DUREE_S:
            source = SOURCE_DU_LABEL.get(t["lyrics_synced_source"] or "")
            verdict = deux_contre_un(d, durees, audios.get(source), source)
            regle1[verdict] += 1
            lignes1.append(
                f"- {verdict} — #{tid} {t['artiste']} « {t['title']} » : durée {d} s, LRC "
                f"{source} jusqu'à {fin} s, audio {audios.get(source)} s ; durées {durees} ; "
                f"témoins {tem} ; {t['genius_url']}"
            )
        ref = paroles_de_reference(t["lyrics"], t["lyrics_source"])
        if not ref or t["instrumental"] or est_instrumental(t["title"]):
            continue
        if _autre_enregistrement(t["title"] or ""):
            continue
        confirmes = [
            fin_du_lrc(o.value) or 0
            for o in obs[tid]
            if o.field == "lyrics_synced"
            and (recouvrement_bigrammes(ref, o.value) or 0) >= CONFIRME
            and not deborde_son_audio(o.value, audios.get(o.source))
        ]
        fin_confirmee = max(confirmes, default=0)
        if not fin_confirmee or fin_confirmee <= d + MARGE_DEMENTI_S:
            continue
        if "manual" in durees:
            regle2[MANUELLE] += 1
            continue
        if not duree_dementie(fin_confirmee, d, durees):
            regle2["durée soutenue par un catalogue"] += 1
            continue
        accord = any(abs(w - d) <= TOLERANCE_S for w in tem)
        regle2["démentie" + (", un témoin soutient la durée" if accord else "")] += 1
        lignes2.append(
            f"- #{tid} {t['artiste']} « {t['title']} » : durée {d} s ({durees}), LRC confirmé "
            f"jusqu'à {fin_confirmee} s ; témoins {tem} ; {t['genius_url']}"
        )
    return [
        "# Règle 1 — deux témoins contre un (`lrc_trop_long`)",
        "",
        ", ".join(f"{k} : {v}" for k, v in sorted(regle1.items())),
        "",
        *sorted(lignes1),
        "",
        "# Règle 2 — durée démentie par un LRC confirmé",
        "",
        ", ".join(f"{k} : {v}" for k, v in sorted(regle2.items())),
        "",
        *sorted(lignes2),
    ]


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", type=Path, default=BASE, help="base lue (lecture seule)")
    a = p.parse_args(argv)
    with closing(sqlite3.connect(f"file:{a.base.resolve().as_posix()}?mode=ro", uri=True)) as c:
        c.row_factory = sqlite3.Row
        print("\n".join(simuler(c)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
